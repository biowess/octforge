"""Composition module for OCTForge.

Handles multi-panel figure composition, grid layouts, panel annotations,
figure title/subtitle, layer color legend, and synthetic metadata blocks.

CRITICAL ARCHITECTURAL LAW:
Composition arranges already-rendered images (or presentations) from the
RenderEntry cache without re-invoking the forward optical synthesis model.
"""

import io
import base64
import math
from typing import List, Tuple, Optional, Dict, Any
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from backend.app.schema import (
    CompositionConfig,
    CompositionResponse,
    PanelConfig,
    PanelLayoutInfo,
    VisualizationConfig,
    AnnotationSet,
    AnnotationItem,
    MeasurementSet,
)
from backend.app.rendering.cache import render_cache, RenderEntry
from backend.app.presentation import (
    generate_visualization,
    generate_annotations,
    generate_measurements,
    hex_to_rgb,
    PALETTES,
    PALETTE_CLINICAL,
    LAYER_IDS,
)


def _get_font(size: int = 14) -> ImageFont.ImageFont:
    """Return a PIL font instance of the given size."""
    try:
        # Try common system TTF fonts if available
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        try:
            return ImageFont.truetype("LiberationSans-Regular.ttf", size)
        except Exception:
            return ImageFont.load_default()


def _draw_annotation_items(
    draw: ImageDraw.ImageDraw,
    items: List[AnnotationItem],
    panel_w: int,
    panel_h: int,
    scene: Optional[SceneConfig] = None,
) -> None:
    """Draw structured annotation shape primitives onto a PIL panel image."""
    for item in items:
        color_rgb = hex_to_rgb(item.color or "#FFFFFF")
        fill_rgb = hex_to_rgb(item.fill_color) if item.fill_color else None
        stroke_w = int(max(1, round(item.stroke_width or 1.5)))

        if item.type == "dot":
            if item.x is not None and item.y is not None:
                r = float(item.radius or 3.0)
                bbox = [item.x - r, item.y - r, item.x + r, item.y + r]
                draw.ellipse(bbox, fill=fill_rgb or color_rgb, outline=color_rgb, width=stroke_w)

        elif item.type in ["leader_line", "reference_line"]:
            if item.x is not None and item.y is not None and item.x2 is not None and item.y2 is not None:
                draw.line([(item.x, item.y), (item.x2, item.y2)], fill=color_rgb, width=stroke_w)

        elif item.type == "arrow":
            if item.x is not None and item.y is not None and item.x2 is not None and item.y2 is not None:
                draw.line([(item.x, item.y), (item.x2, item.y2)], fill=color_rgb, width=stroke_w)
                # Arrowhead at (x2, y2)
                dx = item.x2 - item.x
                dy = item.y2 - item.y
                length = math.hypot(dx, dy)
                if length > 0.1:
                    ux, uy = dx / length, dy / length
                    perp_x, perp_y = -uy, ux
                    head_len = 8.0
                    head_w = 4.0
                    p_left = (item.x2 - head_len * ux + head_w * perp_x, item.y2 - head_len * uy + head_w * perp_y)
                    p_right = (item.x2 - head_len * ux - head_w * perp_x, item.y2 - head_len * uy - head_w * perp_y)
                    draw.polygon([(item.x2, item.y2), p_left, p_right], fill=color_rgb)

        elif item.type == "text":
            if item.x is not None and item.y is not None and (item.text or item.label):
                font = _get_font(int(item.font_size or 12))
                text_str = str(item.text or item.label)
                
                # Check if this is a boxed text (has width > 0)
                if getattr(item, "width", None) is not None and item.width > 0:
                    width = float(item.width)
                    height = float(getattr(item, "height", 0) or 0)
                    rotation = float(getattr(item, "rotation", 0) or 0)
                    
                    lines = []
                    if item.metadata and "lines" in item.metadata:
                        lines = item.metadata["lines"]
                    else:
                        # Greedy wrapping fallback
                        words = text_str.split()
                        curr_line = []
                        for w in words:
                            test_line = " ".join(curr_line + [w]) if curr_line else w
                            bbox = font.getbbox(test_line)
                            tw = bbox[2] - bbox[0] if bbox else 0
                            if tw > width and curr_line:
                                lines.append(" ".join(curr_line))
                                curr_line = [w]
                            else:
                                curr_line.append(w)
                        if curr_line:
                            lines.append(" ".join(curr_line))
                            
                    # Calculate total height of text
                    line_height = int((item.font_size or 12) * 1.25)
                    text_total_height = len(lines) * line_height
                    box_height = max(height, text_total_height)
                    
                    # Pad box to avoid clipping during rotation
                    pad = int(max(width, box_height))
                    layer_w = int(width + 2*pad)
                    layer_h = int(box_height + 2*pad)
                    txt_layer = Image.new("RGBA", (layer_w, layer_h), (0,0,0,0))
                    txt_draw = ImageDraw.Draw(txt_layer)
                    
                    cy = pad
                    for line in lines:
                        # dark outline
                        for ox, oy in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                            txt_draw.text((pad + ox, cy + oy), line, font=font, fill=(0, 0, 0, 255))
                        txt_draw.text((pad, cy), line, font=font, fill=tuple(list(color_rgb) + [255]))
                        cy += line_height
                        
                    # Rotate around box center
                    if rotation != 0:
                        center = (pad + width/2.0, pad + box_height/2.0)
                        txt_layer = txt_layer.rotate(-rotation, center=center, resample=Image.BICUBIC)
                        
                    # Paste back to main image using alpha masking
                    paste_x = int(item.x - pad)
                    paste_y = int(item.y - pad)
                    draw._image.paste(txt_layer, (paste_x, paste_y), mask=txt_layer)
                    
                else:
                    # Legacy point label
                    # Draw subtle dark outline for readability
                    for ox, oy in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                        draw.text((item.x + ox, item.y + oy), text_str, font=font, fill=(0, 0, 0))
                    draw.text((item.x, item.y), text_str, font=font, fill=color_rgb)

        elif item.type == "numbered_marker":
            if item.x is not None and item.y is not None:
                r = float(item.radius or 8.0)
                bbox = [item.x - r, item.y - r, item.x + r, item.y + r]
                draw.ellipse(bbox, fill=fill_rgb or (0, 0, 0), outline=color_rgb, width=stroke_w)
                if item.text or item.label:
                    font = _get_font(10)
                    text_str = str(item.text or item.label)
                    draw.text((item.x - 3, item.y - 5), text_str, font=font, fill=color_rgb)

        elif item.type == "bracket":
            if item.x is not None and item.y is not None and item.y2 is not None:
                bx = item.x
                y_top = min(item.y, item.y2)
                y_bot = max(item.y, item.y2)
                arm = 6.0
                draw.line([(bx + arm, y_top), (bx, y_top), (bx, y_bot), (bx + arm, y_bot)], fill=color_rgb, width=stroke_w)

        elif item.type == "caliper":
            if item.x is not None and item.y is not None and item.x2 is not None and item.y2 is not None:
                p1 = (item.x, item.y)
                p2 = (item.x2, item.y2)
                cal_color = color_rgb or (255, 235, 59)
                cal_stroke = max(2, stroke_w)

                # Dark drop-shadow outline for high contrast against any tissue
                draw.line([p1, p2], fill=(0, 0, 0), width=cal_stroke + 2)
                # Main line
                draw.line([p1, p2], fill=cal_color, width=cal_stroke)

                dx = item.x2 - item.x
                dy = item.y2 - item.y
                length = math.hypot(dx, dy)
                nx, ny = (-dy / length, dx / length) if length > 0.1 else (0.0, -1.0)
                tick_len = 6.0

                # Perpendicular cross-ticks at both endpoints
                for tx, ty in [(item.x, item.y), (item.x2, item.y2)]:
                    t_start = (tx - nx * tick_len, ty - ny * tick_len)
                    t_end = (tx + nx * tick_len, ty + ny * tick_len)
                    draw.line([t_start, t_end], fill=(0, 0, 0), width=cal_stroke + 2)
                    draw.line([t_start, t_end], fill=cal_color, width=cal_stroke)

                # Endpoint dots
                r = 3.0
                draw.ellipse([item.x - r, item.y - r, item.x + r, item.y + r], fill=cal_color, outline=(0, 0, 0))
                draw.ellipse([item.x2 - r, item.y2 - r, item.x2 + r, item.y2 + r], fill=cal_color, outline=(0, 0, 0))

                # Physical distance calculation using anisotropic pixel pitch
                scan_w_mm = scene.acquisition.scan_width if scene else 6.0
                scan_d_mm = scene.acquisition.scan_depth if scene else 2.0
                dx_um = abs(item.x2 - item.x) * (scan_w_mm * 1000.0 / panel_w)
                dz_um = abs(item.y2 - item.y) * (scan_d_mm * 1000.0 / panel_h)
                dist_um = math.hypot(dx_um, dz_um)
                cal_label = f"{dist_um / 1000.0:.2f} mm" if dist_um >= 1000.0 else f"{dist_um:.1f} μm"

                # Midpoint dimension badge
                mid_x = (item.x + item.x2) / 2.0
                mid_y = (item.y + item.y2) / 2.0
                offset = 14.0
                bx = mid_x + nx * offset
                by = mid_y + ny * offset

                font = _get_font(10)
                pill_w = max(52, int(len(cal_label) * 7.0 + 8))
                pill_h = 16
                rect_box = [bx - pill_w / 2, by - pill_h / 2, bx + pill_w / 2, by + pill_h / 2]
                draw.rectangle(rect_box, fill=(18, 20, 26), outline=cal_color, width=1)
                draw.text((bx - pill_w / 2 + 4, by - pill_h / 2 + 2), cal_label, font=font, fill=cal_color)


def _draw_scale_bars(draw: ImageDraw.ImageDraw, meas: MeasurementSet, scale_bar_color: str, panel_w: int, panel_h: int) -> None:
    """Draw calibrated scale bars in bottom right corner of panel with high-contrast outlines."""
    color_rgb = hex_to_rgb(scale_bar_color)
    lat_bar = meas.scale_bars.lateral
    ax_bar = meas.scale_bars.axial

    # High-contrast dark drop-shadow outline
    for ox, oy in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)]:
        draw.line([(lat_bar.start_px.x + ox, lat_bar.start_px.y + oy), (lat_bar.end_px.x + ox, lat_bar.end_px.y + oy)], fill=(0, 0, 0), width=5)
        draw.line([(ax_bar.start_px.x + ox, ax_bar.start_px.y + oy), (ax_bar.end_px.x + ox, ax_bar.end_px.y + oy)], fill=(0, 0, 0), width=5)

    # Lateral bar
    draw.line([(lat_bar.start_px.x, lat_bar.start_px.y), (lat_bar.end_px.x, lat_bar.end_px.y)], fill=color_rgb, width=3)
    # Axial bar
    draw.line([(ax_bar.start_px.x, ax_bar.start_px.y), (ax_bar.end_px.x, ax_bar.end_px.y)], fill=color_rgb, width=3)

    # End tick caps
    tick_len = 4.0
    # Left tick of lateral bar
    draw.line([(lat_bar.start_px.x, lat_bar.start_px.y - tick_len), (lat_bar.start_px.x, lat_bar.start_px.y + tick_len)], fill=(0, 0, 0), width=5)
    draw.line([(lat_bar.start_px.x, lat_bar.start_px.y - tick_len), (lat_bar.start_px.x, lat_bar.start_px.y + tick_len)], fill=color_rgb, width=3)
    # Top tick of axial bar
    draw.line([(ax_bar.start_px.x - tick_len, ax_bar.start_px.y), (ax_bar.start_px.x + tick_len, ax_bar.start_px.y)], fill=(0, 0, 0), width=5)
    draw.line([(ax_bar.start_px.x - tick_len, ax_bar.start_px.y), (ax_bar.start_px.x + tick_len, ax_bar.start_px.y)], fill=color_rgb, width=3)

    font = _get_font(10)
    # Outline text for maximum readability
    for ox, oy in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
        draw.text((lat_bar.start_px.x + ox, lat_bar.start_px.y - 15 + oy), lat_bar.label, font=font, fill=(0, 0, 0))
        draw.text((ax_bar.start_px.x - 48 + ox, ax_bar.start_px.y - 12 + oy), ax_bar.label, font=font, fill=(0, 0, 0))

    # Lateral label
    draw.text((lat_bar.start_px.x, lat_bar.start_px.y - 15), lat_bar.label, font=font, fill=color_rgb)
    # Axial label
    draw.text((ax_bar.start_px.x - 48, ax_bar.start_px.y - 12), ax_bar.label, font=font, fill=color_rgb)


def render_panel_image(panel: PanelConfig) -> Image.Image:
    """Render and return a single panel PIL Image with all overrides applied."""
    entry = render_cache.get(panel.render_id)
    if entry is None:
        # Render missing fallback placeholder image
        placeholder = Image.new("RGB", (512, 512), color=(40, 40, 40))
        pdraw = ImageDraw.Draw(placeholder)
        font = _get_font(16)
        pdraw.text((120, 240), f"Render '{panel.render_id[:8]}' Expired", font=font, fill=(255, 100, 100))
        return placeholder

    # 1. Base visualization
    if panel.visualization is not None:
        vis_resp = generate_visualization(entry, panel.visualization)
        raw_b64 = vis_resp.image.split("base64,")[-1]
        pil_img = Image.open(io.BytesIO(base64.b64decode(raw_b64))).convert("RGB")
    else:
        # Default colored layer visualization if not specified
        default_vis = VisualizationConfig(mode="colored_layer", palette="clinical")
        vis_resp = generate_visualization(entry, default_vis)
        raw_b64 = vis_resp.image.split("base64,")[-1]
        pil_img = Image.open(io.BytesIO(base64.b64decode(raw_b64))).convert("RGB")

    panel_w, panel_h = pil_img.size
    draw = ImageDraw.Draw(pil_img)

    # 2. Annotations & Auto-labeling
    all_items: List[AnnotationItem] = []
    if panel.annotations is not None:
        all_items.extend(panel.annotations.items)

    if panel.auto_label:
        palette_name = panel.palette or "clinical"
        auto_set = generate_annotations(
            entry=entry,
            auto_label=True,
            auto_label_x=panel.auto_label_x,
            palette_name=palette_name,
        )
        all_items.extend(auto_set.items)

    if all_items:
        _draw_annotation_items(draw, all_items, panel_w, panel_h, scene=entry.scene)

    # 3. Calipers & Scale bars
    if panel.calipers or panel.show_scale_bar:
        meas = generate_measurements(entry, calipers=panel.calipers)
        if panel.show_scale_bar:
            _draw_scale_bars(draw, meas, panel.scale_bar_color, panel_w, panel_h)

        if panel.calipers:
            for cal_res in meas.calipers:
                p1 = (cal_res.p1.x, cal_res.p1.y)
                p2 = (cal_res.p2.x, cal_res.p2.y)
                draw.line([p1, p2], fill=(255, 235, 59), width=2)
                draw.ellipse([p1[0] - 3, p1[1] - 3, p1[0] + 3, p1[1] + 3], fill=(255, 235, 59))
                draw.ellipse([p2[0] - 3, p2[1] - 3, p2[0] + 3, p2[1] + 3], fill=(255, 235, 59))
                mid_x = (p1[0] + p2[0]) / 2.0
                mid_y = (p1[1] + p2[1]) / 2.0
                cal_label = f"{cal_res.physical_distance_um:.1f} μm"
                font = _get_font(11)
                draw.text((mid_x + 4, mid_y - 12), cal_label, font=font, fill=(255, 235, 59))

    # 4. Panel Label Badge (e.g. "A", "B", "(A)")
    if panel.label:
        label_font = _get_font(16)
        badge_text = str(panel.label)
        # Draw dark semi-transparent badge background
        draw.rectangle([10, 10, 10 + len(badge_text) * 12 + 16, 38], fill=(0, 0, 0), outline=(200, 200, 200), width=1)
        draw.text((18, 14), badge_text, font=label_font, fill=(255, 255, 255))

    # 5. Panel Title
    if panel.title:
        title_font = _get_font(13)
        t_str = str(panel.title)
        draw.text((12, panel_h - 24), t_str, font=title_font, fill=(240, 240, 240))

    return pil_img


def calculate_grid_layout(num_panels: int, layout_mode: str, grid_columns: Optional[int] = None) -> Tuple[int, int]:
    """Determine grid (columns, rows) based on layout mode and panel count."""
    mode = layout_mode.lower().replace(" ", "")

    if grid_columns is not None and grid_columns > 0:
        cols = grid_columns
        rows = math.ceil(num_panels / cols)
        return cols, rows

    if mode in ["single", "1x1"]:
        return 1, 1
    elif mode in ["1x2", "a-b", "ab"]:
        return 2, 1
    elif mode in ["2x1"]:
        return 1, 2
    elif mode in ["1x3", "a-b-c", "abc"]:
        return 3, 1
    elif mode in ["3x1"]:
        return 1, 3
    elif mode in ["2x2", "a-b-c-d", "abcd"]:
        return 2, 2
    elif mode in ["1x4"]:
        return 4, 1
    elif mode in ["4x1"]:
        return 1, 4

    # Default auto grid
    if num_panels <= 1:
        return 1, 1
    elif num_panels == 2:
        return 2, 1
    elif num_panels == 3:
        return 3, 1
    elif num_panels == 4:
        return 2, 2
    elif num_panels <= 6:
        return 3, 2
    elif num_panels <= 8:
        return 4, 2
    else:
        cols = math.ceil(math.sqrt(num_panels))
        rows = math.ceil(num_panels / cols)
        return cols, rows


def compose_figure(config: CompositionConfig) -> Tuple[Image.Image, List[PanelLayoutInfo]]:
    """Compose multiple panels into a complete publication/lecture figure.

    Returns the composed PIL Image and calculated panel layout coordinates.
    """
    panels = config.panels
    if not panels:
        # Default empty canvas
        img = Image.new("RGB", (600, 400), color=hex_to_rgb(config.background_color))
        return img, []

    # 1. Render all panel images
    panel_images = [render_panel_image(p) for p in panels]
    num_panels = len(panels)

    # 2. Determine grid dimensions
    cols, rows = calculate_grid_layout(num_panels, config.layout, config.grid_columns)

    # Determine uniform panel slot dimensions
    max_panel_w = max(img.width for img in panel_images)
    max_panel_h = max(img.height for img in panel_images)

    # 3. Calculate Canvas Heights for Header, Grid, Legend, and Footer
    header_h = 0
    if config.title:
        header_h += config.font_size_title + 16
    if config.subtitle:
        header_h += config.font_size_subtitle + 12

    legend_h = 44 if config.show_legend else 0
    metadata_h = 36 if config.show_metadata_block else 0

    grid_w = cols * max_panel_w + (cols - 1) * config.spacing
    grid_h = rows * max_panel_h + (rows - 1) * config.spacing

    canvas_w = grid_w + 2 * config.padding
    canvas_h = header_h + grid_h + legend_h + metadata_h + 2 * config.padding

    # Create background canvas
    bg_color = hex_to_rgb(config.background_color)
    text_color = hex_to_rgb(config.text_color)
    canvas = Image.new("RGB", (canvas_w, canvas_h), color=bg_color)
    draw = ImageDraw.Draw(canvas)

    # 4. Draw Header (Title and Subtitle)
    curr_y = config.padding
    if config.title:
        title_font = _get_font(config.font_size_title)
        draw.text((config.padding, curr_y), config.title, font=title_font, fill=text_color)
        curr_y += config.font_size_title + 14

    if config.subtitle:
        subtitle_font = _get_font(config.font_size_subtitle)
        draw.text((config.padding, curr_y), config.subtitle, font=subtitle_font, fill=(180, 180, 180))
        curr_y += config.font_size_subtitle + 10

    # 5. Paste Panels & Record Layout Info
    panel_layouts: List[PanelLayoutInfo] = []

    for idx, (p_cfg, p_img) in enumerate(zip(panels, panel_images)):
        r = idx // cols
        c = idx % cols

        px = config.padding + c * (max_panel_w + config.spacing)
        py = curr_y + r * (max_panel_h + config.spacing)

        # Center if panel is smaller than slot
        offset_x = (max_panel_w - p_img.width) // 2
        offset_y = (max_panel_h - p_img.height) // 2
        dest_x = px + offset_x
        dest_y = py + offset_y

        canvas.paste(p_img, (dest_x, dest_y))

        panel_layouts.append(PanelLayoutInfo(
            index=idx,
            label=p_cfg.label,
            title=p_cfg.title,
            render_id=p_cfg.render_id,
            x=dest_x,
            y=dest_y,
            width=p_img.width,
            height=p_img.height,
        ))

    curr_y += grid_h + 16

    # 6. Draw Layer Legend Bar if requested
    if config.show_legend:
        palette = PALETTES.get(config.legend_palette, PALETTE_CLINICAL)
        legend_font = _get_font(11)
        legend_x = config.padding
        legend_y = curr_y

        draw.text((legend_x, legend_y), "Layers:", font=legend_font, fill=(200, 200, 200))
        legend_x += 55

        for name, lid in LAYER_IDS.items():
            if lid == 0:  # Skip vitreous in legend
                continue
            color_hex = palette.get(name, "#FFFFFF")
            color_rgb = hex_to_rgb(color_hex)
            # Draw color swatch
            draw.rectangle([legend_x, legend_y + 2, legend_x + 12, legend_y + 12], fill=color_rgb, outline=(255, 255, 255))
            draw.text((legend_x + 16, legend_y), name, font=legend_font, fill=text_color)
            legend_x += 16 + len(name) * 7 + 16

        curr_y += 30

    # 7. Draw Metadata / Synthetic notice block if requested
    if config.show_metadata_block:
        meta_font = _get_font(10)
        meta_text = config.metadata_block_text or "SYNTHETIC OCT IMAGE — FOR EDUCATION AND RESEARCH ONLY — NOT FOR CLINICAL DIAGNOSIS"
        draw.rectangle([config.padding, curr_y, canvas_w - config.padding, curr_y + 24], fill=(25, 25, 25), outline=(60, 60, 60))
        draw.text((config.padding + 12, curr_y + 6), meta_text, font=meta_font, fill=(255, 193, 7))

    return canvas, panel_layouts


def compose_scene_api(config: CompositionConfig) -> CompositionResponse:
    """API handler function for POST /api/compose."""
    canvas, layouts = compose_figure(config)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    raw_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    img_b64 = f"data:image/png;base64,{raw_b64}"

    return CompositionResponse(
        status="success",
        image=img_b64,
        width=canvas.width,
        height=canvas.height,
        panel_count=len(config.panels),
        panels=layouts,
        config=config,
    )
