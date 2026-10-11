"""Export module for OCTForge.

Handles multi-format export (PNG, JPEG, TIFF, PDF, SVG), export presets,
DPI/color-mode conversions, embedded technical/clinical metadata,
and synthetic education indicators.

CRITICAL ARCHITECTURAL LAW:
Export consumes already-rendered images or composed figures exactly as authored.
It NEVER modifies cached RenderEntry objects or triggers scene re-rendering.
"""

import io
import json
import base64
import datetime
import math
from typing import Dict, Any, Optional, Tuple
import numpy as np
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin, TiffImagePlugin, ExifTags

from backend.app.schema import (
    ExportConfig,
    ExportRequest,
    ExportResponse,
    ExportPresetInfo,
    CompositionConfig,
    VisualizationConfig,
    AnnotationSet,
    MeasurementSet,
    SceneConfig,
)
from backend.app.rendering.cache import render_cache, RenderEntry
from backend.app.presentation import (
    generate_visualization,
    generate_annotations,
    generate_measurements,
    hex_to_rgb,
)
from backend.app.composition import compose_figure, _get_font, _draw_annotation_items, _draw_scale_bars


# ==============================================================================
# Export Presets Definition
# ==============================================================================

EXPORT_PRESETS: Dict[str, ExportConfig] = {
    "lecture": ExportConfig(
        preset="lecture",
        format="png",
        dpi=150,
        width=1920,
        height=1080,
        color_mode="RGB",
        quality=95,
        watermark=True,
        watermark_text="SYNTHETIC — FOR EDUCATION ONLY",
    ),
    "powerpoint": ExportConfig(
        preset="powerpoint",
        format="png",
        dpi=150,
        width=1920,
        height=1080,
        color_mode="RGB",
        quality=95,
        watermark=True,
        watermark_text="SYNTHETIC — FOR EDUCATION ONLY",
    ),
    "a4": ExportConfig(
        preset="a4",
        format="pdf",
        dpi=300,
        width=2480,
        height=3508,
        color_mode="RGB",
        quality=100,
        watermark=True,
        watermark_text="SYNTHETIC — FOR EDUCATION ONLY",
    ),
    "publication": ExportConfig(
        preset="publication",
        format="tiff",
        dpi=600,
        color_mode="RGB",
        compression="lzw",
        quality=100,
        watermark=False,
    ),
    "web": ExportConfig(
        preset="web",
        format="jpeg",
        dpi=72,
        width=800,
        height=800,
        color_mode="RGB",
        quality=85,
        watermark=True,
        watermark_text="SYNTHETIC — FOR EDUCATION ONLY",
    ),
    "high_resolution": ExportConfig(
        preset="high_resolution",
        format="png",
        dpi=600,
        color_mode="RGB",
        quality=100,
        watermark=False,
    ),
    "low_resource": ExportConfig(
        preset="low_resource",
        format="jpeg",
        dpi=72,
        width=400,
        height=400,
        color_mode="L",
        quality=70,
        watermark=True,
        watermark_text="SYNTHETIC — FOR EDUCATION ONLY",
    ),
}


def get_export_preset(name: str) -> Optional[ExportConfig]:
    """Retrieve a copy of an export preset by name."""
    preset = EXPORT_PRESETS.get(name.lower())
    if preset:
        return preset.model_copy(deep=True)
    return None


def list_export_presets() -> list[ExportPresetInfo]:
    """Return all available export presets with descriptions."""
    descriptions = {
        "lecture": "1080p widescreen presentation slide with educational watermark",
        "powerpoint": "16:9 1080p clean presentation slide",
        "a4": "A4 print layout at 300 DPI (PDF document format)",
        "publication": "High-fidelity 600 DPI LZW-compressed TIFF for scientific journals",
        "web": "Lightweight 72 DPI sRGB JPEG optimized for web browsers",
        "high_resolution": "Archival quality 600 DPI lossless PNG",
        "low_resource": "Ultra-compact 72 DPI grayscale JPEG for bandwidth-constrained environments",
    }
    return [
        ExportPresetInfo(
            id=k,
            name=k.replace("_", " ").title(),
            description=descriptions.get(k, "Export preset"),
            config=v,
        )
        for k, v in EXPORT_PRESETS.items()
    ]


# ==============================================================================
# Metadata Extraction & Embedding
# ==============================================================================

def extract_metadata_block(
    scene: Optional[SceneConfig] = None,
    render_id: Optional[str] = None,
    export_cfg: Optional[ExportConfig] = None,
) -> Dict[str, Any]:
    """Build a comprehensive metadata dictionary embedded into exported files."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    meta = {
        "software": "OCTForge",
        "octforge_version": "1.0.0",
        "synthetic_content": True,
        "synthetic_notice": "SYNTHETIC — FOR EDUCATION ONLY",
        "timestamp": now_iso,
        "render_id": render_id or "composed_figure",
        "export_preset": export_cfg.preset if export_cfg else "custom",
        "export_format": export_cfg.format if export_cfg else "png",
        "dpi": export_cfg.dpi if export_cfg else 300,
    }

    if scene:
        meta["scene_name"] = scene.meta.name
        meta["render_seed"] = scene.anatomy.render_seed
        meta["fovea_center"] = scene.anatomy.fovea_center
        meta["fovea_pit_depth"] = scene.anatomy.fovea_pit_depth
        meta["scan_width_mm"] = scene.acquisition.scan_width
        meta["scan_depth_mm"] = scene.acquisition.scan_depth
        meta["output_resolution"] = [scene.acquisition.width, scene.acquisition.height]
        meta["signal_strength"] = scene.acquisition.signal_strength
        meta["snr_db"] = scene.acquisition.snr_db
        meta["speckle_amount"] = scene.realism.speckle_amount
        meta["pathology_enabled"] = scene.pathology.enabled
        if scene.pathology.enabled and scene.pathology.instances:
            meta["pathology_instances"] = [
                {"type": inst.type, "severity": inst.severity, "size": inst.size, "location": inst.location}
                for inst in scene.pathology.instances
            ]
        else:
            meta["pathology_instances"] = []
        meta["artifacts_enabled"] = scene.artifacts.enabled
    else:
        meta["scene_name"] = "Composed Figure"
        meta["render_seed"] = None

    if export_cfg and export_cfg.custom_metadata:
        meta["custom"] = export_cfg.custom_metadata

    return meta


def _apply_watermark(img: Image.Image, watermark_text: str) -> Image.Image:
    """Render a clean, semi-transparent watermark banner along bottom of image."""
    img_rgba = img.convert("RGBA")
    w, h = img_rgba.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)

    banner_h = max(24, int(h * 0.05))
    banner_y = h - banner_h

    # Semi-transparent dark banner
    odraw.rectangle([0, banner_y, w, h], fill=(0, 0, 0, 180))

    font_size = max(10, int(banner_h * 0.55))
    font = _get_font(font_size)

    # Yellow warning text
    odraw.text((16, banner_y + int((banner_h - font_size) * 0.4)), watermark_text, font=font, fill=(255, 193, 7, 240))

    watermarked = Image.alpha_composite(img_rgba, overlay)
    return watermarked


# ==============================================================================
# SVG Vector Composite Builder
# ==============================================================================

def generate_svg_export(
    base_img: Image.Image,
    annotations: Optional[AnnotationSet],
    measurements: Optional[MeasurementSet],
    metadata: Dict[str, Any],
    watermark: bool = False,
    watermark_text: str = "SYNTHETIC — FOR EDUCATION ONLY",
    include_annotations: bool = True,
    include_measurements: bool = True,
    include_scale_bars: bool = True,
    scene: Optional[SceneConfig] = None,
) -> str:
    """Build a complete SVG document containing the embedded base raster image + vector chrome."""
    w, h = base_img.size

    # Convert base image to base64 PNG data URI
    buf = io.BytesIO()
    base_img.save(buf, format="PNG")
    raw_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    img_data_uri = f"data:image/png;base64,{raw_b64}"

    metadata_json = json.dumps(metadata, indent=2)

    svg_parts = [
        f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        f'  <metadata>',
        f'    <octforge:metadata xmlns:octforge="http://octforge.org/schema">',
        f'      <![CDATA[{metadata_json}]]>',
        f'    </octforge:metadata>',
        f'  </metadata>',
        f'  <desc>SYNTHETIC — FOR EDUCATION ONLY</desc>',
        f'  <g id="oct_base_layer">',
        f'    <image x="0" y="0" width="{w}" height="{h}" href="{img_data_uri}" xlink:href="{img_data_uri}"/>',
        f'  </g>',
        f'  <g id="oct_vector_chrome">',
    ]

    # Add structured annotations as SVG elements (excluding calipers)
    if include_annotations and annotations and annotations.items:
        for item in annotations.items:
            if item.type == "caliper":
                continue
            color = item.color or "#FFFFFF"
            stroke_w = item.stroke_width or 1.5

            if item.type == "dot" and item.x is not None and item.y is not None:
                r = item.radius or 3.0
                fill = item.fill_color or color
                svg_parts.append(f'    <circle cx="{item.x}" cy="{item.y}" r="{r}" fill="{fill}" stroke="{color}" stroke-width="{stroke_w}"/>')

            elif item.type in ["leader_line", "reference_line"] and item.x is not None and item.y is not None and item.x2 is not None and item.y2 is not None:
                svg_parts.append(f'    <line x1="{item.x}" y1="{item.y}" x2="{item.x2}" y2="{item.y2}" stroke="{color}" stroke-width="{stroke_w}"/>')

            elif item.type == "arrow" and item.x is not None and item.y is not None and item.x2 is not None and item.y2 is not None:
                svg_parts.append(f'    <line x1="{item.x}" y1="{item.y}" x2="{item.x2}" y2="{item.y2}" stroke="{color}" stroke-width="{stroke_w}"/>')
                dx = item.x2 - item.x
                dy = item.y2 - item.y
                dist = math.hypot(dx, dy)
                if dist > 0.1:
                    ux, uy = dx / dist, dy / dist
                    px, py = -uy, ux
                    x_tip = item.x2
                    y_tip = item.y2
                    x1 = x_tip - 8.0 * ux + 4.0 * px
                    y1 = y_tip - 8.0 * uy + 4.0 * py
                    x2 = x_tip - 8.0 * ux - 4.0 * px
                    y2 = y_tip - 8.0 * uy - 4.0 * py
                    svg_parts.append(f'    <polygon points="{x_tip},{y_tip} {x1},{y1} {x2},{y2}" fill="{color}"/>')

            elif item.type == "text" and item.x is not None and item.y is not None:
                import html
                txt = html.escape(str(item.text or item.label or ""))
                fs = item.font_size or 12
                
                if getattr(item, "width", None) is not None and item.width > 0:
                    width = float(item.width)
                    height = float(getattr(item, "height", 0) or 0)
                    rotation = float(getattr(item, "rotation", 0) or 0)
                    
                    lines = []
                    if item.metadata and "lines" in item.metadata:
                        lines = [html.escape(str(l)) for l in item.metadata["lines"]]
                    else:
                        lines = [html.escape(str(l)) for l in str(item.text or item.label or "").split()]
                        
                    line_height = int(fs * 1.25)
                    text_total_height = len(lines) * line_height
                    box_height = max(height, text_total_height)
                    
                    cx = item.x + width / 2.0
                    cy = item.y + box_height / 2.0
                    
                    transform = f' transform="rotate({rotation} {cx} {cy})"' if rotation != 0 else ""
                    svg_parts.append(f'    <text x="{item.x}" y="{item.y}" font-family="sans-serif" font-size="{fs}" fill="{color}" stroke="black" stroke-width="0.5" paint-order="stroke fill"{transform}>')
                    
                    for i, line in enumerate(lines):
                        dy = line_height if i > 0 else fs
                        svg_parts.append(f'      <tspan x="{item.x}" dy="{dy}px">{line}</tspan>')
                    svg_parts.append(f'    </text>')
                else:
                    svg_parts.append(f'    <text x="{item.x}" y="{item.y}" font-family="sans-serif" font-size="{fs}" fill="{color}" stroke="black" stroke-width="0.5" paint-order="stroke fill">{txt}</text>')

            elif item.type == "numbered_marker" and item.x is not None and item.y is not None:
                r = item.radius or 8.0
                fill = item.fill_color or "#141518"
                txt = str(item.text or item.label or "1")
                svg_parts.append(f'    <circle cx="{item.x}" cy="{item.y}" r="{r}" fill="{fill}" stroke="{color}" stroke-width="{stroke_w}"/>')
                svg_parts.append(f'    <text x="{item.x}" y="{item.y + 3.5}" font-family="monospace" font-size="10" font-weight="bold" fill="{color}" text-anchor="middle">{txt}</text>')

            elif item.type == "bracket" and item.x is not None and item.y is not None and item.y2 is not None:
                y_top = min(item.y, item.y2)
                y_bot = max(item.y, item.y2)
                bx = item.x
                arm = 6.0
                pts = f"{bx + arm},{y_top} {bx},{y_top} {bx},{y_bot} {bx + arm},{y_bot}"
                svg_parts.append(f'    <polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{stroke_w}"/>')

    # Add calipers as structured SVG elements
    if include_measurements and annotations and annotations.items:
        scan_w_mm = scene.acquisition.scan_width if scene else 6.0
        scan_d_mm = scene.acquisition.scan_depth if scene else 2.0
        um_per_px_lat = (scan_w_mm * 1000.0) / w
        um_per_px_ax = (scan_d_mm * 1000.0) / h

        for item in annotations.items:
            if item.type == "caliper" and item.x is not None and item.y is not None and item.x2 is not None and item.y2 is not None:
                color = item.color or "#FFEB3B"
                sw = int(max(2, item.stroke_width or 2))
                dx = item.x2 - item.x
                dy = item.y2 - item.y
                length = math.hypot(dx, dy)
                nx, ny = (-dy / length, dx / length) if length > 0.1 else (0.0, -1.0)
                tick_len = 6.0

                dx_um = abs(dx) * um_per_px_lat
                dz_um = abs(dy) * um_per_px_ax
                dist_um = math.hypot(dx_um, dz_um)
                cal_label = f"{dist_um / 1000.0:.2f} mm" if dist_um >= 1000.0 else f"{dist_um:.1f} μm"

                mid_x = (item.x + item.x2) / 2.0
                mid_y = (item.y + item.y2) / 2.0
                bx = mid_x + nx * 14.0
                by = mid_y + ny * 14.0
                pw = max(52, int(len(cal_label) * 7.0 + 8))
                ph = 16

                svg_parts.append(f'    <g id="{item.id}">')
                # Dark outline
                svg_parts.append(f'      <line x1="{item.x}" y1="{item.y}" x2="{item.x2}" y2="{item.y2}" stroke="rgba(0,0,0,0.85)" stroke-width="{sw + 2}"/>')
                # Main line
                svg_parts.append(f'      <line x1="{item.x}" y1="{item.y}" x2="{item.x2}" y2="{item.y2}" stroke="{color}" stroke-width="{sw}"/>')
                # Cross-ticks
                for tx, ty in [(item.x, item.y), (item.x2, item.y2)]:
                    svg_parts.append(f'      <line x1="{tx - nx * tick_len}" y1="{ty - ny * tick_len}" x2="{tx + nx * tick_len}" y2="{ty + ny * tick_len}" stroke="rgba(0,0,0,0.85)" stroke-width="{sw + 2}"/>')
                    svg_parts.append(f'      <line x1="{tx - nx * tick_len}" y1="{ty - ny * tick_len}" x2="{tx + nx * tick_len}" y2="{ty + ny * tick_len}" stroke="{color}" stroke-width="{sw}"/>')
                # End dots
                svg_parts.append(f'      <circle cx="{item.x}" cy="{item.y}" r="3" fill="{color}" stroke="black" stroke-width="1"/>')
                svg_parts.append(f'      <circle cx="{item.x2}" cy="{item.y2}" r="3" fill="{color}" stroke="black" stroke-width="1"/>')
                # Badge pill
                svg_parts.append(f'      <rect x="{bx - pw / 2}" y="{by - ph / 2}" width="{pw}" height="{ph}" fill="#12141a" stroke="{color}" stroke-width="1" rx="3"/>')
                svg_parts.append(f'      <text x="{bx}" y="{by + 4}" font-family="monospace" font-size="10" font-weight="bold" fill="{color}" text-anchor="middle">{cal_label}</text>')
                svg_parts.append(f'    </g>')

    # Add measurement scale bars as vector elements with high-contrast outlines
    if include_scale_bars and measurements and measurements.scale_bars:
        lat = measurements.scale_bars.lateral
        ax = measurements.scale_bars.axial
        svg_parts.extend([
            f'    <g id="scale_bars">',
            f'      <!-- Outlines for high contrast -->',
            f'      <line x1="{lat.start_px.x}" y1="{lat.start_px.y}" x2="{lat.end_px.x}" y2="{lat.end_px.y}" stroke="black" stroke-width="5"/>',
            f'      <line x1="{ax.start_px.x}" y1="{ax.start_px.y}" x2="{ax.end_px.x}" y2="{ax.end_px.y}" stroke="black" stroke-width="5"/>',
            f'      <line x1="{lat.start_px.x}" y1="{lat.start_px.y - 4}" x2="{lat.start_px.x}" y2="{lat.start_px.y + 4}" stroke="black" stroke-width="5"/>',
            f'      <line x1="{ax.start_px.x - 4}" y1="{ax.start_px.y}" x2="{ax.start_px.x + 4}" y2="{ax.start_px.y}" stroke="black" stroke-width="5"/>',
            f'      <!-- Scale bar lines -->',
            f'      <line x1="{lat.start_px.x}" y1="{lat.start_px.y}" x2="{lat.end_px.x}" y2="{lat.end_px.y}" stroke="#FFFFFF" stroke-width="3"/>',
            f'      <line x1="{ax.start_px.x}" y1="{ax.start_px.y}" x2="{ax.end_px.x}" y2="{ax.end_px.y}" stroke="#FFFFFF" stroke-width="3"/>',
            f'      <line x1="{lat.start_px.x}" y1="{lat.start_px.y - 4}" x2="{lat.start_px.x}" y2="{lat.start_px.y + 4}" stroke="#FFFFFF" stroke-width="3"/>',
            f'      <line x1="{ax.start_px.x - 4}" y1="{ax.start_px.y}" x2="{ax.start_px.x + 4}" y2="{ax.start_px.y}" stroke="#FFFFFF" stroke-width="3"/>',
            f'      <!-- Labels -->',
            f'      <text x="{lat.start_px.x}" y="{lat.start_px.y - 8}" font-family="monospace" font-size="10" font-weight="bold" fill="#FFFFFF" stroke="black" stroke-width="2" paint-order="stroke fill">{lat.label}</text>',
            f'      <text x="{ax.start_px.x - 52}" y="{ax.start_px.y - 8}" font-family="monospace" font-size="10" font-weight="bold" fill="#FFFFFF" stroke="black" stroke-width="2" paint-order="stroke fill">{ax.label}</text>',
            f'    </g>',
        ])

    # Watermark
    if watermark:
        banner_h = max(24, int(h * 0.05))
        banner_y = h - banner_h
        svg_parts.extend([
            f'    <g id="watermark">',
            f'      <rect x="0" y="{banner_y}" width="{w}" height="{banner_h}" fill="rgba(0,0,0,0.7)"/>',
            f'      <text x="16" y="{banner_y + int(banner_h * 0.7)}" font-family="sans-serif" font-size="12" fill="#FFC107" font-weight="bold">{watermark_text}</text>',
            f'    </g>',
        ])

    svg_parts.append('  </g>')
    svg_parts.append('</svg>')

    return '\n'.join(svg_parts)


# ==============================================================================
# Main Export Engine
# ==============================================================================

def export_artifact(
    image: Image.Image,
    config: ExportConfig,
    metadata: Dict[str, Any],
    annotations: Optional[AnnotationSet] = None,
    measurements: Optional[MeasurementSet] = None,
    scene: Optional[SceneConfig] = None,
) -> Tuple[bytes, str, str]:
    """Export PIL Image or composed figure into binary file bytes, format name, and MIME type.

    Embeds technical metadata, synthetic notices, and configured DPI.
    """
    fmt = config.format.lower().replace(".", "")
    if fmt == "jpg":
        fmt = "jpeg"
    elif fmt == "tif":
        fmt = "tiff"

    # 1. Apply watermark if requested
    processed_img = image.copy()
    if config.watermark:
        processed_img = _apply_watermark(processed_img, config.watermark_text)

    # 2. Resize if explicit dimensions requested
    if config.width is not None or config.height is not None:
        cur_w, cur_h = processed_img.size
        if config.width is not None and config.height is not None:
            # Fit within the box, preserving aspect ratio (letterbox)
            scale = min(config.width / cur_w, config.height / cur_h)
            target_w = int(round(cur_w * scale))
            target_h = int(round(cur_h * scale))
        elif config.width is not None:
            scale = config.width / cur_w
            target_w = config.width
            target_h = int(round(cur_h * scale))
        else:
            scale = config.height / cur_h
            target_w = int(round(cur_w * scale))
            target_h = config.height
        processed_img = processed_img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    # 3. Handle Color Mode
    color_mode = config.color_mode.upper()
    if fmt == "jpeg":
        if color_mode == "RGBA":
            color_mode = "RGB"
        if processed_img.mode != color_mode:
            processed_img = processed_img.convert(color_mode)
    elif fmt == "png":
        if color_mode == "L":
            processed_img = processed_img.convert("L")
        elif color_mode == "RGB":
            processed_img = processed_img.convert("RGB")
    elif fmt == "tiff":
        if color_mode in ["RGB", "L", "RGBA"]:
            processed_img = processed_img.convert(color_mode)

    dpi_val = (config.dpi, config.dpi)
    out_buf = io.BytesIO()

    # 4. Format-Specific Binary Serialization with Embedded Metadata
    if fmt == "png":
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("Software", "OCTForge 1.0.0")
        png_info.add_text("SyntheticNotice", "SYNTHETIC — FOR EDUCATION ONLY")
        png_info.add_text("Comment", json.dumps(metadata))
        png_info.add_text("MetadataJSON", json.dumps(metadata))

        optimize = (config.compression == "optimize")
        processed_img.save(
            out_buf,
            format="PNG",
            dpi=dpi_val,
            pnginfo=png_info,
            optimize=optimize,
        )
        mime_type = "image/png"

    elif fmt == "jpeg":
        # Embed EXIF user comment with metadata
        exif_dict = processed_img.getexif()
        meta_str = f"OCTForge Synthetic OCT | SYNTHETIC — FOR EDUCATION ONLY | {json.dumps(metadata)}"
        exif_dict[0x010E] = "OCTForge Synthetic B-Scan (SYNTHETIC — FOR EDUCATION ONLY)"  # ImageDescription
        exif_dict[0x0131] = "OCTForge"  # Software
        exif_dict[0x9286] = meta_str    # UserComment

        processed_img.save(
            out_buf,
            format="JPEG",
            dpi=dpi_val,
            quality=config.quality,
            exif=exif_dict,
        )
        mime_type = "image/jpeg"

    elif fmt == "tiff":
        # Build TIFF tags
        compression_arg = config.compression or "lzw"
        if compression_arg == "lzw":
            comp_mode = "tiff_lzw"
        elif compression_arg == "deflate":
            comp_mode = "tiff_adobe_deflate"
        elif compression_arg == "packbits":
            comp_mode = "group4" if color_mode == "1" else "raw"
        else:
            comp_mode = "raw"

        # TIFF Info tags
        tiff_info = {
            270: f"OCTForge Synthetic OCT | SYNTHETIC — FOR EDUCATION ONLY | {json.dumps(metadata)}",  # ImageDescription
            305: "OCTForge 1.0.0",                                                                       # Software
            315: "OCTForge Synthetic Generator",                                                         # Artist
            33432: "SYNTHETIC — FOR EDUCATION ONLY",                                                     # Copyright
        }

        processed_img.save(
            out_buf,
            format="TIFF",
            dpi=dpi_val,
            compression=comp_mode,
            tiffinfo=tiff_info,
        )
        mime_type = "image/tiff"

    elif fmt == "pdf":
        # Export as high-resolution PDF document
        pdf_img = processed_img.convert("RGB")
        pdf_meta = {
            "title": metadata.get("scene_name", "OCTForge Synthetic B-Scan"),
            "author": "OCTForge",
            "subject": "SYNTHETIC — FOR EDUCATION ONLY",
            "keywords": "OCT, Synthetic, Education, Ophthalmology, Retina",
            "creator": "OCTForge 1.0.0",
            "producer": "OCTForge Synthetic Engine",
        }
        pdf_img.save(
            out_buf,
            format="PDF",
            resolution=config.dpi,
            title=pdf_meta["title"],
            author=pdf_meta["author"],
            subject=pdf_meta["subject"],
            producer=pdf_meta["producer"],
        )
        mime_type = "application/pdf"

    elif fmt == "svg":
        svg_content = generate_svg_export(
            base_img=processed_img,
            annotations=annotations,
            measurements=measurements,
            metadata=metadata,
            watermark=config.watermark,
            watermark_text=config.watermark_text,
            include_annotations=config.include_annotations,
            include_measurements=config.include_measurements,
            include_scale_bars=config.include_scale_bars,
            scene=scene,
        )
        out_buf.write(svg_content.encode("utf-8"))
        mime_type = "image/svg+xml"

    else:
        # Default fallback to PNG
        processed_img.save(out_buf, format="PNG", dpi=dpi_val)
        mime_type = "image/png"
        fmt = "png"

    file_bytes = out_buf.getvalue()
    return file_bytes, fmt, mime_type


def export_scene_api(request: ExportRequest) -> ExportResponse:
    """API handler for POST /api/export."""
    export_cfg = request.config

    # Apply preset if specified and known
    if export_cfg.preset != "custom":
        preset_cfg = get_export_preset(export_cfg.preset)
        if preset_cfg is not None:
            # Merge requested overrides with preset
            merged_dict = preset_cfg.model_dump()
            req_dict = export_cfg.model_dump(exclude_unset=True)
            merged_dict.update(req_dict)
            export_cfg = ExportConfig(**merged_dict)

    # 1. Composed Figure Export
    if request.composition is not None:
        composed_img, _ = compose_figure(request.composition)
        metadata = extract_metadata_block(
            scene=None,
            render_id=None,
            export_cfg=export_cfg,
        )
        file_bytes, fmt, mime_type = export_artifact(
            image=composed_img,
            config=export_cfg,
            metadata=metadata,
            annotations=None,
            measurements=None,
        )
        width, height = composed_img.size

    # 2. Single Render Export
    elif request.render_id is not None:
        entry = render_cache.get(request.render_id)
        scene = entry.scene if entry else None

        # Build base image
        if entry is not None:
            vis_cfg = request.visualization or VisualizationConfig(mode="colored_layer", palette="clinical")
            vis_resp = generate_visualization(entry, vis_cfg)
            raw_b64 = vis_resp.image.split("base64,")[-1]
            base_img = Image.open(io.BytesIO(base64.b64decode(raw_b64))).convert("RGB")
        else:
            base_img = Image.new("RGB", (512, 512), color=(50, 50, 50))

        # Annotations & Measurements
        draw = ImageDraw.Draw(base_img)
        w, h = base_img.size

        # Resolve scale bars if requested
        meas_data = request.measurements
        if export_cfg.include_scale_bars and (meas_data is None or not meas_data.scale_bars) and entry is not None:
            meas_data = generate_measurements(entry)

        if request.annotations and request.annotations.items and export_cfg.format != "svg":
            annot_items = [i for i in request.annotations.items if i.type != "caliper"]
            caliper_items = [i for i in request.annotations.items if i.type == "caliper"]

            if export_cfg.include_annotations and annot_items:
                _draw_annotation_items(draw, annot_items, w, h, scene=scene)

            if export_cfg.include_measurements and caliper_items:
                _draw_annotation_items(draw, caliper_items, w, h, scene=scene)

        if export_cfg.include_scale_bars and meas_data and meas_data.scale_bars and export_cfg.format != "svg":
            _draw_scale_bars(draw, meas_data, "#FFFFFF", w, h)

        metadata = extract_metadata_block(
            scene=scene,
            render_id=request.render_id,
            export_cfg=export_cfg,
        )

        file_bytes, fmt, mime_type = export_artifact(
            image=base_img,
            config=export_cfg,
            metadata=metadata,
            annotations=request.annotations,
            measurements=meas_data,
            scene=scene,
        )
        width, height = base_img.size

    else:
        # Fallback empty export
        empty_img = Image.new("RGB", (512, 512), color=(20, 20, 20))
        metadata = extract_metadata_block(None, None, export_cfg)
        file_bytes, fmt, mime_type = export_artifact(
            image=empty_img,
            config=export_cfg,
            metadata=metadata,
        )
        width, height = empty_img.size

    # Prepare return payload (Data URI or SVG string)
    raw_b64 = base64.b64encode(file_bytes).decode("utf-8")
    data_uri = f"data:{mime_type};base64,{raw_b64}"
    filename = f"octforge_export_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt}"

    return ExportResponse(
        status="success",
        format=fmt,
        mime_type=mime_type,
        filename=filename,
        data=data_uri,
        width=width,
        height=height,
        dpi=export_cfg.dpi,
        file_size_bytes=len(file_bytes),
        metadata=metadata,
    )
