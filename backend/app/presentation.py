"""Presentation module for OCTForge.

Handles visualization styling, color overlays, colorblind-safe palettes,
annotation shape primitives, automatic layer labeling, and physical measurements.

CRITICAL ARCHITECTURAL LAW:
All functions in this module operate strictly on cached RenderEntry artifacts
(pixels, layer_id_map, boundary_curves, vessel_info, acquisition geometry).
They NEVER invoke the rendering pipeline (forward_model, pathology, artifacts).
"""

import io
import base64
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
from PIL import Image
from scipy.ndimage import label as nd_label

from backend.app.rendering.cache import RenderEntry
from backend.app.schema import (
    VisualizationConfig,
    VisualizationResponse,
    AnnotationItem,
    AnnotationSet,
    Point2D,
    CaliperRequest,
    CaliperResult,
    LayerThicknessMetric,
    FluidRegionMetric,
    FovealMetrics,
    ScaleBar,
    ScaleBars,
    MeasurementSet,
)


# ==============================================================================
# Layer Definitions & Palettes
# ==============================================================================

LAYER_IDS = {
    "VITREOUS": 0,
    "RNFL":     1,
    "GCL":      2,
    "IPL":      3,
    "INL":      4,
    "OPL":      5,
    "ONL":      6,
    "EZ":       7,
    "RPE":      8,
    "CHOROID":  9,
    "SCLERA":  10,
    "VESSEL":  11,
}

LAYER_ID_TO_NAME = {v: k for k, v in LAYER_IDS.items()}

# Clinical anatomical palette (standard color-coding)
PALETTE_CLINICAL: Dict[str, str] = {
    "VITREOUS": "#000000",
    "RNFL":     "#FF9800",  # Amber / Orange
    "GCL":      "#00BCD4",  # Cyan
    "IPL":      "#2196F3",  # Blue
    "INL":      "#4CAF50",  # Green
    "OPL":      "#8BC34A",  # Light Green / Lime
    "ONL":      "#3F51B5",  # Indigo / Dark Blue
    "EZ":       "#FFEB3B",  # Bright Yellow (Photoreceptors)
    "RPE":      "#E91E63",  # Crimson / Pink (RPE)
    "CHOROID":  "#795548",  # Brown (Choroid)
    "SCLERA":   "#9E9E9E",  # Slate Gray
    "VESSEL":   "#D32F2F",  # Dark Red
}

# Color-blind safe palette (Okabe-Ito / Tol palette for deuteranopia/protanopia/tritanopia)
PALETTE_COLORBLIND_SAFE: Dict[str, str] = {
    "VITREOUS": "#000000",
    "RNFL":     "#E69F00",  # Orange
    "GCL":      "#56B4E9",  # Sky Blue
    "IPL":      "#009E73",  # Bluish Green
    "INL":      "#F0E442",  # Yellow
    "OPL":      "#0072B2",  # Deep Blue
    "ONL":      "#D55E00",  # Vermillion
    "EZ":       "#CC79A7",  # Reddish Purple
    "RPE":      "#882255",  # Wine
    "CHOROID":  "#44AA99",  # Teal
    "SCLERA":   "#999999",  # Neutral Gray
    "VESSEL":   "#117733",  # Forest Green
}

# Educational palette (high-contrast, vivid textbook style)
PALETTE_EDUCATIONAL: Dict[str, str] = {
    "VITREOUS": "#000000",
    "RNFL":     "#FF5722",  # Deep Orange
    "GCL":      "#00E5FF",  # Bright Cyan
    "IPL":      "#2979FF",  # Bright Blue
    "INL":      "#00E676",  # Bright Green
    "OPL":      "#AEEA00",  # Vivid Lime
    "ONL":      "#AA00FF",  # Vivid Purple
    "EZ":       "#FFD600",  # Vivid Gold
    "RPE":      "#FF1744",  # Vivid Red
    "CHOROID":  "#8D6E63",  # Warm Brown
    "SCLERA":   "#78909C",  # Blue Gray
    "VESSEL":   "#C2185B",  # Berry
}

PALETTES = {
    "clinical": PALETTE_CLINICAL,
    "colorblind_safe": PALETTE_COLORBLIND_SAFE,
    "educational": PALETTE_EDUCATIONAL,
}


def hex_to_rgb(hex_code: str) -> Tuple[int, int, int]:
    """Convert hex color string (#RGB or #RRGGBB or #RRGGBBAA) to RGB tuple."""
    hex_code = hex_code.lstrip("#")
    if len(hex_code) == 3:
        hex_code = "".join([c * 2 for c in hex_code])
    if len(hex_code) >= 6:
        r = int(hex_code[0:2], 16)
        g = int(hex_code[2:4], 16)
        b = int(hex_code[4:6], 16)
        return r, g, b
    return 255, 255, 255


def _image_to_base64_png(img_arr: np.ndarray) -> str:
    """Convert RGB/Grayscale numpy uint8 array to base64 PNG data URI."""
    if img_arr.ndim == 2:
        pil_img = Image.fromarray(img_arr, mode="L")
    else:
        pil_img = Image.fromarray(img_arr, mode="RGB")
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    raw_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{raw_b64}"


# ==============================================================================
# 1. Visualization System
# ==============================================================================

def generate_visualization(
    entry: RenderEntry,
    config: VisualizationConfig,
) -> VisualizationResponse:
    """Generate a styled OCT visualization from a cached RenderEntry.

    Supports 4 modes:
      - 'grayscale': Raw OCT grayscale B-scan
      - 'colored_layer' (or 'layers'): Per-layer color blend over grayscale
      - 'hybrid': Selective highlight of chosen layers/pathology over grayscale base
      - 'educational': Vivid high-contrast textbook palette with boundary enhancement
    """
    pixels = entry.pixels
    layer_id_map = entry.layer_id_map
    height, width = pixels.shape

    mode = config.mode
    if mode == "layers":
        mode = "colored_layer"

    # Base grayscale image as RGB float32
    base_rgb = np.stack([pixels, pixels, pixels], axis=-1).astype(np.float32)

    # 1. Grayscale mode: return directly without color overlays
    if mode == "grayscale":
        out_img = pixels
        img_b64 = _image_to_base64_png(out_img)
        return VisualizationResponse(
            status="success",
            render_id=entry.render_id,
            image=img_b64,
            mode="grayscale",
            palette=config.palette,
            width=width,
            height=height,
        )

    # 2. Select base palette
    palette_dict = PALETTES.get(config.palette, PALETTE_CLINICAL).copy()

    # 3. Build Lookup Tables for Color and Opacity
    # Lookup table size 32 accommodates layer IDs 0..11, vessels, and special IDs
    lut_color = np.zeros((32, 3), dtype=np.float32)
    lut_alpha = np.zeros((32,), dtype=np.float32)

    # Default layer opacity
    global_opacity = float(config.global_opacity)

    for name, lid in LAYER_IDS.items():
        if lid == LAYER_IDS["VITREOUS"]:
            # Vitreous remains uncolored / transparent by default
            lut_alpha[lid] = 0.0
            continue

        color_hex = palette_dict.get(name, "#FFFFFF")
        layer_opacity = 0.6
        layer_visible = True

        # Apply per-layer overrides if present
        if name in config.layers:
            override = config.layers[name]
            if override.color:
                color_hex = override.color
            if override.opacity is not None:
                layer_opacity = float(override.opacity)
            if override.visible is not None:
                layer_visible = bool(override.visible)

        r, g, b = hex_to_rgb(color_hex)
        lut_color[lid] = [r, g, b]

        if mode == "colored_layer":
            lut_alpha[lid] = (layer_opacity * global_opacity) if layer_visible else 0.0
        elif mode == "hybrid":
            # Hybrid mode: only layers explicitly configured with visible=True in config.layers
            # (or selected) are highlighted; others remain grayscale (alpha = 0)
            if name in config.layers and config.layers[name].visible:
                lut_alpha[lid] = layer_opacity * global_opacity
            else:
                lut_alpha[lid] = 0.0
        elif mode == "educational":
            # Educational mode: higher contrast vivid saturation
            lut_alpha[lid] = min(1.0, layer_opacity * global_opacity * 1.2) if layer_visible else 0.0

    # Hybrid mode: pathology / fluid highlight
    if mode == "hybrid" and config.highlight_pathology:
        # Highlight dropout / destroyed signal (layer ID -1) or pathology regions
        pathology_color = [0.0, 229.0, 255.0]  # Bright Neon Cyan / Amber
        lut_color[31] = pathology_color
        lut_alpha[31] = 0.85

    # 4. Map layer_id_map through LUT
    # Handle negative indices (e.g. -1 for dropout) safely
    mapped_ids = np.where(layer_id_map < 0, 31, layer_id_map)
    mapped_ids = np.clip(mapped_ids, 0, 31)

    alpha_map = lut_alpha[mapped_ids]          # (H, W)
    color_map = lut_color[mapped_ids]          # (H, W, 3)

    # 5. Alpha Blend: Out = (1 - alpha) * Base + alpha * Color
    blended = (1.0 - alpha_map[:, :, None]) * base_rgb + alpha_map[:, :, None] * color_map

    # 6. Educational Mode: Enhance layer boundaries
    if mode == "educational":
        # Detect layer boundaries using gradient of layer_id_map
        dy = np.abs(np.diff(mapped_ids, axis=0, prepend=mapped_ids[0:1, :])) > 0
        dx = np.abs(np.diff(mapped_ids, axis=1, prepend=mapped_ids[:, 0:1])) > 0
        boundaries = (dy | dx) & (mapped_ids > 0)
        # Draw high-contrast subtle boundary lines
        blended[boundaries] = np.clip(blended[boundaries] * 0.4 + 150.0, 0, 255)

    out_pixels = np.clip(blended + 0.5, 0, 255).astype(np.uint8)
    img_b64 = _image_to_base64_png(out_pixels)

    return VisualizationResponse(
        status="success",
        render_id=entry.render_id,
        image=img_b64,
        mode=mode,
        palette=config.palette,
        width=width,
        height=height,
    )


# ==============================================================================
# 2. Annotation System
# ==============================================================================

def generate_annotations(
    entry: RenderEntry,
    auto_label: bool = False,
    auto_label_x: Optional[float] = None,
    palette_name: str = "clinical",
    user_annotations: Optional[AnnotationSet] = None,
) -> AnnotationSet:
    """Generate structured annotation primitives and automatic layer labeling.

    Reads the ground-truth layer_id_map and boundary_curves to derive exact
    anatomical coordinates for leader lines, labels, and legends.
    """
    items: List[AnnotationItem] = []
    if user_annotations is not None:
        items.extend(user_annotations.items)

    palette = PALETTES.get(palette_name, PALETTE_CLINICAL)
    width = entry.width
    height = entry.height
    layer_map = entry.layer_id_map

    # Automatic layer labeling
    if auto_label:
        # Determine probe column (default: nasal parafoveal peak around 20% width)
        if auto_label_x is not None:
            probe_x = int(np.clip(auto_label_x, 0, width - 1))
        else:
            probe_x = int(width * 0.20)

        # Retinal and choroidal layers to label top-down
        ordered_layers = [
            "RNFL", "GCL", "IPL", "INL", "OPL", "ONL", "EZ", "RPE", "CHOROID", "SCLERA"
        ]

        # Offset side for text labels — labels land in a column, staggered
        is_left_side = probe_x < (width * 0.5)
        # Label column x: a fixed margin from probe that stays on screen
        if is_left_side:
            label_col_x = float(max(15, probe_x - 55))
        else:
            label_col_x = float(min(width - 15, probe_x + 55))

        font_h = 13  # pixel height per label row at font_size=11
        min_gap = 2  # minimum vertical gap between adjacent label rows
        row_step = font_h + min_gap

        # First pass: collect visible layers with their z_mid
        visible = []
        for name in ordered_layers:
            lid = LAYER_IDS.get(name)
            if lid is None:
                continue
            rows = np.where(layer_map[:, probe_x] == lid)[0]
            if len(rows) == 0:
                continue
            z_start = float(rows[0])
            z_end   = float(rows[-1])
            z_mid   = (z_start + z_end) / 2.0
            visible.append((name, z_mid))

        # Second pass: assign staggered label y positions, greedy top-down
        label_ys = []
        last_y = -9999.0
        for name, z_mid in visible:
            desired = z_mid
            placed  = max(desired, last_y + row_step)
            label_ys.append(placed)
            last_y = placed

        for (name, z_mid), label_y in zip(visible, label_ys):
            color = palette.get(name, "#FFFFFF")

            # 1. Anchor dot on layer centre
            items.append(AnnotationItem(
                id=f"dot_{name}",
                type="dot",
                x=float(probe_x),
                y=z_mid,
                radius=2.5,
                color=color,
                fill_color=color,
                layer=name,
            ))

            # 2. Leader line from anchor to label position
            items.append(AnnotationItem(
                id=f"leader_{name}",
                type="leader_line",
                x=float(probe_x),
                y=z_mid,
                x2=label_col_x,
                y2=label_y,
                color=color,
                stroke_width=1.5,
                layer=name,
            ))

            # 3. Layer text label at staggered position
            items.append(AnnotationItem(
                id=f"text_{name}",
                type="text",
                x=label_col_x,
                y=label_y,
                text=name,
                label=name,
                color=color,
                font_size=11,
                layer=name,
            ))

    # Legend generation
    legend_items = [
        {"id": lid, "label": name, "color": palette.get(name, "#FFFFFF")}
        for name, lid in LAYER_IDS.items()
        if lid > 0
    ]

    return AnnotationSet(items=items, legend=legend_items)


# ==============================================================================
# 3. Measurement System
# ==============================================================================

def generate_measurements(
    entry: RenderEntry,
    calipers: Optional[List[CaliperRequest]] = None,
    include_profiles: bool = False,
) -> MeasurementSet:
    """Derive accurate quantitative thickness and morphology measurements.

    Derives all values directly from ground-truth layer_id_map and boundary_curves
    with physical unit conversion (pixels -> μm / mm) using acquisition geometry.
    """
    scene = entry.scene
    width = entry.width
    height = entry.height

    scan_width_mm = float(scene.acquisition.scan_width)
    scan_depth_mm = float(scene.acquisition.scan_depth)

    # Pixel to physical unit conversion factors (μm / pixel)
    pixel_width_um = (scan_width_mm * 1000.0) / width
    pixel_height_um = (scan_depth_mm * 1000.0) / height

    # Fovea center coordinate
    fovea_cx = int(np.clip(round(scene.anatomy.fovea_center), 0, width - 1))
    fovea_cx_mm = float(scene.anatomy.fovea_center * (scan_width_mm / width))

    # 1. Total Retinal Thickness (TRT: ILM to BM / RPE_bot)
    curves = entry.boundary_curves
    ilm_curve = curves.get("ILM")
    bm_curve = curves.get("BM", curves.get("RPE_bot"))

    if ilm_curve is not None and bm_curve is not None:
        trt_px = np.maximum(bm_curve - ilm_curve, 0.0)
    else:
        # Fallback to counting retinal layer pixels in layer_id_map (layers 1..8)
        retina_mask = (entry.layer_id_map >= 1) & (entry.layer_id_map <= 8)
        trt_px = np.sum(retina_mask, axis=0).astype(np.float64)

    trt_um = trt_px * pixel_height_um

    central_retinal_thickness_um = float(trt_um[fovea_cx])
    total_retinal_thickness_mean_um = float(np.mean(trt_um))
    total_retinal_thickness_min_um = float(np.min(trt_um))
    total_retinal_thickness_max_um = float(np.max(trt_um))

    # 2. Foveal Pit Morphology
    if ilm_curve is not None:
        # Foveal pit depth: difference between parafoveal ILM crest and foveal pit floor
        fovea_window = slice(max(0, fovea_cx - int(width * 0.25)), min(width, fovea_cx + int(width * 0.25)))
        ilm_crest = float(np.min(ilm_curve[fovea_window]))  # Highest point in image = lowest z
        ilm_floor = float(ilm_curve[fovea_cx])              # Deepest point of pit
        pit_depth_px = max(0.0, ilm_floor - ilm_crest)
    else:
        pit_depth_px = float(scene.anatomy.fovea_pit_depth)

    pit_depth_um = pit_depth_px * pixel_height_um

    # Foveal pit width: Gaussian Full Width at Half Maximum (FWHM)
    # FWHM = 2 * sqrt(2 * ln(2)) * sigma ≈ 2.355 * sigma
    pit_sigma_px = scene.anatomy.fovea_pit_width * width
    pit_fwhm_px = 2.0 * np.sqrt(2.0 * np.log(2.0)) * pit_sigma_px
    pit_fwhm_um = pit_fwhm_px * pixel_width_um
    pit_fwhm_mm = pit_fwhm_um / 1000.0

    foveal_metrics = FovealMetrics(
        fovea_center_x_px=float(scene.anatomy.fovea_center),
        fovea_center_x_mm=fovea_cx_mm,
        central_foveal_thickness_um=central_retinal_thickness_um,
        central_foveal_thickness_px=float(trt_px[fovea_cx]),
        foveal_pit_depth_um=pit_depth_um,
        foveal_pit_depth_px=pit_depth_px,
        foveal_pit_width_um=pit_fwhm_um,
        foveal_pit_width_mm=pit_fwhm_mm,
    )

    # 3. Per-Layer Thickness Metrics
    layer_boundary_pairs = {
        "RNFL":    ("ILM", "RNFL_bot"),
        "GCL":     ("RNFL_bot", "GCL_bot"),
        "IPL":     ("GCL_bot", "IPL_bot"),
        "INL":     ("IPL_bot", "INL_bot"),
        "OPL":     ("INL_bot", "OPL_bot"),
        "ONL":     ("OPL_bot", "ONL_bot"),
        "EZ":      ("ONL_bot", "EZ_bot"),
        "RPE":     ("EZ_bot", "RPE_bot"),
        "CHOROID": ("BM", "CSI"),
    }

    # Parafoveal index mask (e.g. 1.0 - 2.5 mm from fovea)
    col_coords = np.arange(width)
    dist_from_fovea_px = np.abs(col_coords - fovea_cx)
    parafoveal_mask = (dist_from_fovea_px >= int(width * 0.10)) & (dist_from_fovea_px <= int(width * 0.35))
    if not np.any(parafoveal_mask):
        parafoveal_mask = np.ones(width, dtype=bool)

    layer_thicknesses: Dict[str, LayerThicknessMetric] = {}

    for lname, (top_b, bot_b) in layer_boundary_pairs.items():
        if top_b in curves and bot_b in curves:
            t_px = np.maximum(curves[bot_b] - curves[top_b], 0.0)
        else:
            lid = LAYER_IDS.get(lname, -1)
            t_px = np.sum(entry.layer_id_map == lid, axis=0).astype(np.float64)

        t_um = t_px * pixel_height_um

        layer_thicknesses[lname] = LayerThicknessMetric(
            layer=lname,
            mean_thickness_um=float(np.mean(t_um)),
            min_thickness_um=float(np.min(t_um)),
            max_thickness_um=float(np.max(t_um)),
            foveal_thickness_um=float(t_um[fovea_cx]),
            parafoveal_thickness_um=float(np.mean(t_um[parafoveal_mask])),
            mean_thickness_px=float(np.mean(t_px)),
            profile_um=t_um.tolist() if include_profiles else None,
        )

    # 4. Fluid Region Metrics (Detect fluid pockets / cysts / detachment regions)
    fluid_regions: List[FluidRegionMetric] = []
    # Identify fluid / cyst / hyporeflective lesion components
    # We inspect pathology instances or detect fluid pixels from layer map (e.g. dropout or fluid)
    if scene.pathology.enabled:
        for idx, inst in enumerate(scene.pathology.instances):
            if inst.type in ["intraretinal_cyst", "cystoid_macular_edema", "subretinal_fluid", "pigment_epithelial_detachment"]:
                # Compute physical and pixel dimensions of this lesion instance
                cx_inst_px = inst.location * width
                w_inst_px = max(2.0, inst.size * width)
                # Axial height estimate based on severity and extent
                h_inst_px = max(2.0, inst.extent * inst.severity * 40.0)
                z_inst_px = float(curves.get("INL_bot", np.full(width, height * 0.4))[int(np.clip(cx_inst_px, 0, width - 1))])

                x_min_px = max(0.0, cx_inst_px - w_inst_px / 2.0)
                x_max_px = min(float(width), cx_inst_px + w_inst_px / 2.0)
                z_min_px = max(0.0, z_inst_px - h_inst_px / 2.0)
                z_max_px = min(float(height), z_inst_px + h_inst_px / 2.0)

                w_um = (x_max_px - x_min_px) * pixel_width_um
                h_um = (z_max_px - z_min_px) * pixel_height_um
                area_px = (x_max_px - x_min_px) * (z_max_px - z_min_px) * 0.785  # Elliptical area
                area_um2 = area_px * pixel_width_um * pixel_height_um

                fluid_regions.append(FluidRegionMetric(
                    id=idx + 1,
                    layer_id=LAYER_IDS.get("INL", 4),
                    pathology_type=inst.type,
                    x_min_px=x_min_px,
                    x_max_px=x_max_px,
                    z_min_px=z_min_px,
                    z_max_px=z_max_px,
                    width_px=w_inst_px,
                    height_px=h_inst_px,
                    width_um=w_um,
                    height_um=h_um,
                    area_px=area_px,
                    area_um2=area_um2,
                    area_mm2=area_um2 / 1e6,
                    centroid_x_px=cx_inst_px,
                    centroid_z_px=z_inst_px,
                    centroid_x_mm=cx_inst_px * (scan_width_mm / width),
                    centroid_z_mm=z_inst_px * (scan_depth_mm / height),
                ))

    # 5. Calibrated Scale Bars
    # Lateral: 1.0 mm
    lat_len_px = float(1.0 / (scan_width_mm / width))
    lat_bar = ScaleBar(
        orientation="lateral",
        length_px=lat_len_px,
        length_physical=1.0,
        unit="mm",
        label="1.0 mm",
        start_px=Point2D(x=float(width - 20 - lat_len_px), y=float(height - 20)),
        end_px=Point2D(x=float(width - 20), y=float(height - 20)),
    )

    # Axial: 200 μm (0.2 mm)
    ax_len_px = float(0.2 / (scan_depth_mm / height))
    ax_bar = ScaleBar(
        orientation="axial",
        length_px=ax_len_px,
        length_physical=200.0,
        unit="μm",
        label="200 μm",
        start_px=Point2D(x=float(width - 20), y=float(height - 20 - ax_len_px)),
        end_px=Point2D(x=float(width - 20), y=float(height - 20)),
    )
    scale_bars = ScaleBars(lateral=lat_bar, axial=ax_bar)

    # 6. Manual Caliper Measurements
    caliper_results: List[CaliperResult] = []
    if calipers:
        for cal in calipers:
            dx_px = abs(cal.p2.x - cal.p1.x)
            dz_px = abs(cal.p2.y - cal.p1.y)
            pixel_dist = float(np.sqrt(dx_px ** 2 + dz_px ** 2))

            dx_um_val = dx_px * pixel_width_um
            dz_um_val = dz_px * pixel_height_um
            phys_dist_um = float(np.sqrt(dx_um_val ** 2 + dz_um_val ** 2))
            phys_dist_mm = phys_dist_um / 1000.0

            angle_deg = float(np.degrees(np.arctan2(cal.p2.y - cal.p1.y, cal.p2.x - cal.p1.x)))

            caliper_results.append(CaliperResult(
                id=cal.id,
                label=cal.label,
                p1=cal.p1,
                p2=cal.p2,
                pixel_distance=pixel_dist,
                dx_pixels=dx_px,
                dz_pixels=dz_px,
                physical_distance_um=phys_dist_um,
                dx_um=dx_um_val,
                dz_um=dz_um_val,
                physical_distance_mm=phys_dist_mm,
                angle_deg=angle_deg,
            ))

    return MeasurementSet(
        scan_width_mm=scan_width_mm,
        scan_depth_mm=scan_depth_mm,
        width_px=width,
        height_px=height,
        pixel_width_um=pixel_width_um,
        pixel_height_um=pixel_height_um,
        central_retinal_thickness_um=central_retinal_thickness_um,
        total_retinal_thickness_mean_um=total_retinal_thickness_mean_um,
        total_retinal_thickness_min_um=total_retinal_thickness_min_um,
        total_retinal_thickness_max_um=total_retinal_thickness_max_um,
        foveal_metrics=foveal_metrics,
        layer_thicknesses=layer_thicknesses,
        fluid_regions=fluid_regions,
        scale_bars=scale_bars,
        calipers=caliper_results,
    )
