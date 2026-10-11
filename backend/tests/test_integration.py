"""End-to-End Integration Tests for OCTForge Platform.

Covers complete cross-subsystem workflows:
  render → visualize → annotate → measure → compose → export
for:
  1. A normal eye
  2. A single-pathology scene (CME)
  3. A multi-pathology scene (Drusen + SRF + ERM)
  4. A multi-panel composition (with badges, scale bars, legend, metadata block)
  5. Each of the 7 export presets (lecture, powerpoint, a4, publication, web, high_resolution, low_resource)
  6. Project persistence lifecycle and end-to-end seed reproducibility.
"""

import io
import json
import base64
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.schema import (
    SceneConfig,
    PathologyInstance,
    ArtifactInstance,
    VisualizationConfig,
    AnnotationSet,
    AnnotationItem,
    Point2D,
    CaliperRequest,
    CompositionConfig,
    PanelConfig,
    ExportConfig,
)
from backend.app.presets import (
    _build_normal_macula,
    _build_cme,
    _build_drusen,
    _build_srf,
    _build_erm,
    list_pathology_presets,
)
from backend.app.export import list_export_presets


client = TestClient(app)


# ==============================================================================
# Workflow 1: Normal Eye Full Pipeline
# ==============================================================================

def test_workflow_normal_eye_end_to_end():
    """Workflow: Normal macula render -> visualize -> annotate -> measure -> export."""
    scene = _build_normal_macula()
    scene.acquisition.width = 512
    scene.acquisition.height = 512

    # 1. Render Preview
    r_render = client.post("/api/render/preview", json=scene.model_dump())
    assert r_render.status_code == 200
    render_data = r_render.json()
    render_id = render_data["render_id"]
    assert render_data["status"] == "success"
    assert render_data["width"] == 512
    assert render_data["height"] == 512
    assert render_data["image"].startswith("data:image/png;base64,")

    # 2. Visualize in all 4 modes
    for mode in ["grayscale", "colored_layer", "hybrid", "educational"]:
        vis_req = {
            "render_id": render_id,
            "config": {
                "mode": mode,
                "palette": "colorblind_safe" if mode != "grayscale" else "clinical",
                "global_opacity": 0.65,
            }
        }
        r_vis = client.post("/api/visualize", json=vis_req)
        assert r_vis.status_code == 200
        vis_data = r_vis.json()
        assert vis_data["status"] == "success"
        assert vis_data["mode"] == mode
        assert vis_data["image"].startswith("data:image/png;base64,")

    # 3. Annotate with auto-labeling and custom landmark
    annot_req = {
        "render_id": render_id,
        "auto_label": True,
        "auto_label_x": 256.0,
        "palette": "colorblind_safe",
        "annotations": {
            "items": [
                {
                    "type": "dot",
                    "x": 256.0,
                    "y": 120.0,
                    "color": "#FFD700",
                    "label": "Foveal Pit Minimum",
                }
            ]
        }
    }
    r_ann = client.post("/api/annotate", json=annot_req)
    assert r_ann.status_code == 200
    ann_data = r_ann.json()
    items = ann_data["annotations"]["items"]
    assert len(items) >= 9  # Auto-labeled layers + 1 custom landmark
    assert any(it.get("label") == "Foveal Pit Minimum" for it in items)

    # 4. Measure morphology, layer thicknesses, and caliper distance
    meas_req = {
        "render_id": render_id,
        "include_profiles": True,
        "calipers": [
            {
                "id": "c1",
                "label": "Central Foveal Depth",
                "p1": {"x": 256.0, "y": 80.0},
                "p2": {"x": 256.0, "y": 280.0},
            }
        ]
    }
    r_meas = client.post("/api/measure", json=meas_req)
    assert r_meas.status_code == 200
    meas_data = r_meas.json()["measurements"]
    assert meas_data["central_retinal_thickness_um"] > 100.0
    assert "RNFL" in meas_data["layer_thicknesses"]
    assert len(meas_data["calipers"]) == 1
    caliper = meas_data["calipers"][0]
    assert caliper["pixel_distance"] == pytest.approx(200.0, abs=1.0)
    assert caliper["physical_distance_um"] > 0.0

    # 5. Export single image
    exp_req = {
        "render_id": render_id,
        "visualization": {"mode": "colored_layer", "palette": "colorblind_safe"},
        "annotations": ann_data["annotations"],
        "measurements": meas_data,
        "config": {
            "preset": "publication",
            "format": "png",
            "dpi": 300,
            "include_annotations": True,
            "include_measurements": True,
            "include_scale_bars": True,
        }
    }
    r_exp = client.post("/api/export", json=exp_req)
    assert r_exp.status_code == 200
    exp_data = r_exp.json()
    assert exp_data["format"] == "png"
    assert exp_data["file_size_bytes"] > 1000
    assert exp_data["metadata"]["synthetic_content"] is True
    assert exp_data["metadata"]["scene_name"] == "Normal Macula"


# ==============================================================================
# Workflow 2: Single-Pathology Scene (CME) Full Pipeline
# ==============================================================================

def test_workflow_single_pathology_cme():
    """Workflow: CME preset render -> hybrid visualize -> annotate -> measure fluid -> export."""
    cme = _build_cme()

    # 1. Render Final
    r_render = client.post("/api/render/final", json=cme.model_dump())
    assert r_render.status_code == 200
    render_data = r_render.json()
    render_id = render_data["render_id"]
    assert len(render_data["boundary_curves"]) >= 10
    assert len(render_data["layer_id_map"]) == 512

    # 2. Visualize in hybrid mode
    vis_req = {
        "render_id": render_id,
        "config": {
            "mode": "hybrid",
            "highlight_pathology": True,
            "palette": "clinical",
        }
    }
    r_vis = client.post("/api/visualize", json=vis_req)
    assert r_vis.status_code == 200
    assert r_vis.json()["mode"] == "hybrid"

    # 3. Annotate
    r_ann = client.post("/api/annotate", json={"render_id": render_id, "auto_label": True})
    assert r_ann.status_code == 200
    annotations = r_ann.json()["annotations"]

    # 4. Measure - check fluid region metrics
    r_meas = client.post("/api/measure", json={"render_id": render_id})
    assert r_meas.status_code == 200
    measurements = r_meas.json()["measurements"]
    assert measurements["central_retinal_thickness_um"] > 0
    # In CME, fluid regions are identified and quantified
    assert len(measurements["fluid_regions"]) >= 1
    fluid_0 = measurements["fluid_regions"][0]
    assert fluid_0["area_um2"] > 0
    assert fluid_0["width_um"] > 0
    assert fluid_0["height_um"] > 0

    # 5. Export with lecture preset (watermark enabled)
    exp_req = {
        "render_id": render_id,
        "visualization": {"mode": "hybrid"},
        "annotations": annotations,
        "measurements": measurements,
        "config": {
            "preset": "lecture",
            "format": "png",
            "watermark": True,
            "watermark_text": "SYNTHETIC — FOR EDUCATION ONLY",
        }
    }
    r_exp = client.post("/api/export", json=exp_req)
    assert r_exp.status_code == 200
    assert r_exp.json()["metadata"]["export_preset"] == "lecture"


# ==============================================================================
# Workflow 3: Multi-Pathology Scene Full Pipeline
# ==============================================================================

def test_workflow_multi_pathology_complex():
    """Workflow: Multi-pathology (Drusen + SRF + ERM) -> visualize -> annotate -> measure -> export."""
    scene = _build_normal_macula()
    scene.meta.name = "Triple Complex Pathology"
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(type="drusen", severity=0.6, size=0.1, count=5, location=0.35, seed_offset=1),
        PathologyInstance(type="subretinal_fluid", severity=0.7, size=0.3, count=1, location=0.55, seed_offset=2),
        PathologyInstance(type="epiretinal_membrane", severity=0.75, size=0.6, count=1, location=0.50, seed_offset=3),
    ]

    # Render
    r_render = client.post("/api/render/preview", json=scene.model_dump())
    assert r_render.status_code == 200
    render_id = r_render.json()["render_id"]

    # Visualize with custom per-layer styling overrides
    vis_req = {
        "render_id": render_id,
        "config": {
            "mode": "colored_layer",
            "palette": "educational",
            "layers": {
                "RPE": {"color": "#FF00FF", "opacity": 0.8, "visible": True},
                "RNFL": {"color": "#00FFFF", "opacity": 0.5, "visible": True},
            }
        }
    }
    r_vis = client.post("/api/visualize", json=vis_req)
    assert r_vis.status_code == 200

    # Annotate with numbered badges
    ann_req = {
        "render_id": render_id,
        "auto_label": False,
        "annotations": {
            "items": [
                {"type": "numbered_marker", "x": 180.0, "y": 300.0, "text": "1", "label": "Drusen"},
                {"type": "numbered_marker", "x": 280.0, "y": 280.0, "text": "2", "label": "SRF"},
                {"type": "numbered_marker", "x": 256.0, "y": 140.0, "text": "3", "label": "ERM"},
            ]
        }
    }
    r_ann = client.post("/api/annotate", json=ann_req)
    assert r_ann.status_code == 200
    ann_items = r_ann.json()["annotations"]["items"]
    assert len(ann_items) == 3

    # Measure
    r_meas = client.post("/api/measure", json={"render_id": render_id})
    assert r_meas.status_code == 200
    measurements = r_meas.json()["measurements"]

    # Export to SVG vector document
    exp_req = {
        "render_id": render_id,
        "visualization": vis_req["config"],
        "annotations": {"items": ann_items},
        "measurements": measurements,
        "config": {
            "preset": "custom",
            "format": "svg",
            "watermark": True,
        }
    }
    r_exp = client.post("/api/export", json=exp_req)
    assert r_exp.status_code == 200
    exp_data = r_exp.json()
    assert exp_data["format"] == "svg"
    assert exp_data["data"].startswith("data:image/svg+xml;base64,")
    svg_content = base64.b64decode(exp_data["data"].split("base64,")[-1]).decode("utf-8")
    assert "<svg" in svg_content
    assert "oct_vector_chrome" in svg_content


# ==============================================================================
# Workflow 4: Multi-Panel Composition Full Pipeline
# ==============================================================================

def test_workflow_multi_panel_composition():
    """Workflow: Compose figure with multiple distinct pathology panels and export."""
    # 1. Render normal control
    norm = _build_normal_macula()
    id_norm = client.post("/api/render/preview", json=norm.model_dump()).json()["render_id"]

    # 2. Render drusen
    drusen = _build_drusen()
    id_drusen = client.post("/api/render/preview", json=drusen.model_dump()).json()["render_id"]

    # 3. Render SRF
    srf = _build_srf()
    id_srf = client.post("/api/render/preview", json=srf.model_dump()).json()["render_id"]

    # 4. Render ERM
    erm = _build_erm()
    id_erm = client.post("/api/render/preview", json=erm.model_dump()).json()["render_id"]

    # Compose 2x2 figure
    comp_config = {
        "title": "Comparative Macular OCT Pathology Atlas",
        "subtitle": "High-Resolution Spectral-Domain Synthetic Cross-Sections",
        "layout": "2x2",
        "spacing": 16,
        "padding": 24,
        "show_legend": True,
        "legend_palette": "clinical",
        "show_metadata_block": True,
        "panels": [
            {
                "render_id": id_norm,
                "label": "A",
                "title": "Healthy Normal Control",
                "auto_label": True,
                "show_scale_bar": True,
            },
            {
                "render_id": id_drusen,
                "label": "B",
                "title": "Early Age-Related Macular Degeneration (Drusen)",
                "visualization": {"mode": "hybrid"},
                "show_scale_bar": True,
            },
            {
                "render_id": id_srf,
                "label": "C",
                "title": "Central Serous Chorioretinopathy (SRF)",
                "visualization": {"mode": "colored_layer", "palette": "colorblind_safe"},
                "show_scale_bar": True,
            },
            {
                "render_id": id_erm,
                "label": "D",
                "title": "Epiretinal Membrane (ERM)",
                "visualization": {"mode": "colored_layer", "palette": "educational"},
                "show_scale_bar": True,
            },
        ]
    }

    r_comp = client.post("/api/compose", json={"config": comp_config})
    assert r_comp.status_code == 200
    comp_data = r_comp.json()
    assert comp_data["status"] == "success"
    assert comp_data["panel_count"] == 4
    assert len(comp_data["panels"]) == 4
    assert comp_data["width"] > 1000
    assert comp_data["height"] > 1000
    assert comp_data["image"].startswith("data:image/png;base64,")

    # Verify panel layout info
    for p_info in comp_data["panels"]:
        assert p_info["width"] == 512
        assert p_info["height"] == 512
        assert p_info["x"] >= 0
        assert p_info["y"] >= 0

    # Export composed figure to high-resolution publication PDF
    exp_pdf_req = {
        "composition": comp_config,
        "config": {
            "preset": "a4",
            "format": "pdf",
            "dpi": 300,
        }
    }
    r_exp_pdf = client.post("/api/export", json=exp_pdf_req)
    assert r_exp_pdf.status_code == 200
    assert r_exp_pdf.json()["format"] == "pdf"
    assert r_exp_pdf.json()["mime_type"] == "application/pdf"
    assert r_exp_pdf.json()["file_size_bytes"] > 50000

    # Export composed figure to TIFF
    exp_tiff_req = {
        "composition": comp_config,
        "config": {
            "preset": "publication",
            "format": "tiff",
            "dpi": 300,
        }
    }
    r_exp_tiff = client.post("/api/export", json=exp_tiff_req)
    assert r_exp_tiff.status_code == 200
    assert r_exp_tiff.json()["format"] == "tiff"


# ==============================================================================
# Workflow 5: Every Export Preset Verification
# ==============================================================================

@pytest.mark.parametrize("preset_info", list_export_presets(), ids=lambda p: p.id)
def test_workflow_all_export_presets(preset_info):
    """Workflow: Verify every single export preset functions correctly and produces valid output."""
    normal = _build_normal_macula()
    r_render = client.post("/api/render/preview", json=normal.model_dump())
    assert r_render.status_code == 200
    render_id = r_render.json()["render_id"]

    exp_req = {
        "render_id": render_id,
        "visualization": {"mode": "colored_layer", "palette": "clinical"},
        "config": preset_info.config.model_dump(),
    }

    r_exp = client.post("/api/export", json=exp_req)
    assert r_exp.status_code == 200
    data = r_exp.json()

    assert data["status"] == "success"
    assert data["format"] == preset_info.config.format
    assert data["file_size_bytes"] > 0
    assert data["dpi"] == preset_info.config.dpi

    # Validate output payload decoding
    if data["format"] in ["png", "jpeg", "tiff"]:
        raw_b64 = data["data"].split("base64,")[-1]
        raw_bytes = base64.b64decode(raw_b64)
        img = Image.open(io.BytesIO(raw_bytes))
        assert img.width > 0
        assert img.height > 0
        if preset_info.config.width and preset_info.config.height:
            # Letterbox fit: both dimensions must not exceed the box,
            # and at least one must exactly hit its bound.
            assert img.width <= preset_info.config.width
            assert img.height <= preset_info.config.height
            assert (img.width == preset_info.config.width or
                    img.height == preset_info.config.height)
        elif preset_info.config.width:
            assert img.width == preset_info.config.width
        elif preset_info.config.height:
            assert img.height == preset_info.config.height
    elif data["format"] == "pdf":
        raw_b64 = data["data"].split("base64,")[-1]
        raw_bytes = base64.b64decode(raw_b64)
        assert raw_bytes.startswith(b"%PDF-")


# ==============================================================================
# Workflow 6: Project Lifecycle & Seed Reproducibility
# ==============================================================================

def test_workflow_project_persistence_and_reproducibility():
    """Workflow: Create project -> save -> load -> duplicate -> rename -> verify 100% seed reproducibility."""
    cme = _build_cme()
    initial_seed = cme.anatomy.render_seed

    # 1. Create project via API
    create_req = {
        "name": "Integration Test CME Project",
        "description": "Validation of complete project persistence lifecycle",
        "scene": cme.model_dump(),
        "visualization": {"mode": "hybrid", "palette": "clinical"},
        "annotations": {
            "items": [
                {"type": "arrow", "x": 200.0, "y": 150.0, "x2": 256.0, "y2": 220.0, "color": "#00FF00"}
            ]
        }
    }
    r_create = client.post("/api/projects", json=create_req)
    assert r_create.status_code == 201
    project = r_create.json()
    proj_id = project["id"]
    assert project["name"] == "Integration Test CME Project"

    # 2. Get project by ID
    r_get = client.get(f"/api/projects/{proj_id}")
    assert r_get.status_code == 200
    loaded = r_get.json()
    assert loaded["id"] == proj_id
    assert loaded["scene"]["anatomy"]["render_seed"] == initial_seed
    assert len(loaded["annotations"]["items"]) == 1

    # 3. Duplicate project
    r_dup = client.post(f"/api/projects/{proj_id}/duplicate?name=Duplicated%20CME%20Project")
    assert r_dup.status_code == 200
    duplicated = r_dup.json()
    dup_id = duplicated["id"]
    assert dup_id != proj_id
    assert duplicated["name"] == "Duplicated CME Project"
    assert duplicated["scene"]["anatomy"]["render_seed"] == initial_seed

    # 4. Rename duplicated project
    r_rename = client.patch(f"/api/projects/{dup_id}/rename", json={"name": "Renamed CME Project"})
    assert r_rename.status_code == 200
    assert r_rename.json()["name"] == "Renamed CME Project"

    # 5. Reproducibility test: rendering both projects must produce identical SHA-256
    r_render1 = client.post("/api/render/preview", json=loaded["scene"])
    r_render2 = client.post("/api/render/preview", json=duplicated["scene"])
    assert r_render1.status_code == 200
    assert r_render2.status_code == 200
    assert r_render1.json()["cache_key"] == r_render2.json()["cache_key"]
    assert r_render1.json()["image"] == r_render2.json()["image"]

    # 6. Clean up created projects
    client.delete(f"/api/projects/{proj_id}")
    client.delete(f"/api/projects/{dup_id}")
