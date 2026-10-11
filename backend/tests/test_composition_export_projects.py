"""Unit tests for Multi-Panel Composition, Multi-Format Export, Presets, Randomization, and Projects."""

import io
import json
import base64
import pytest
import numpy as np
from PIL import Image
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.schema import (
    SceneConfig,
    CompositionConfig,
    CompositionRequest,
    PanelConfig,
    ExportConfig,
    ExportRequest,
    RandomizeSceneRequest,
    PathologyVariantsRequest,
    ProjectCreateRequest,
    ProjectRenameRequest,
    Project,
)
from backend.app.presets import (
    list_pathology_presets,
    get_pathology_preset,
    randomize_scene,
    generate_pathology_variants,
)
from backend.app.export import (
    EXPORT_PRESETS,
    list_export_presets,
    get_export_preset,
)
from backend.app.projects import (
    list_projects,
    create_project,
    get_project,
    save_project,
    duplicate_project,
    rename_project,
    reset_project,
    delete_project,
)

client = TestClient(app)


# ==============================================================================
# 1. Preset Validity & Rendering Tests
# ==============================================================================

def test_all_pathology_presets_valid_and_renderable():
    """Verify that every curated pathology preset validates and renders cleanly."""
    presets = list_pathology_presets()
    assert len(presets) >= 9, f"Expected at least 9 pathology presets, got {len(presets)}"

    required_keys = ["normal", "cme", "srf", "drusen", "ped", "erm", "macular_hole", "lamellar_hole", "geographic_atrophy"]
    preset_ids = [p.id for p in presets]
    for rk in required_keys:
        assert rk in preset_ids, f"Required preset '{rk}' missing from presets list"

    # Test GET /api/presets/pathology
    resp = client.get("/api/presets/pathology")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 9

    # Test rendering preview of each preset
    for p in presets:
        # GET /api/presets/pathology/{preset_id}
        resp_p = client.get(f"/api/presets/pathology/{p.id}")
        assert resp_p.status_code == 200
        scene_dict = resp_p.json()
        scene_obj = SceneConfig(**scene_dict)
        assert scene_obj.acquisition.width == 512

        # Fast preview render
        render_resp = client.post("/api/render/preview", json=scene_dict)
        assert render_resp.status_code == 200
        r_data = render_resp.json()
        assert "render_id" in r_data
        assert r_data["image"].startswith("data:image/png;base64,")


def test_export_presets_list():
    """Verify export presets endpoint and preset schema."""
    resp = client.get("/api/presets/export")
    assert resp.status_code == 200
    presets = resp.json()
    preset_ids = [p["id"] for p in presets]
    for exp_id in ["lecture", "powerpoint", "a4", "publication", "web", "high_resolution", "low_resource"]:
        assert exp_id in preset_ids


# ==============================================================================
# 2. Randomization & Variants Tests
# ==============================================================================

def test_bounded_randomization_preserves_validity():
    """Verify randomize_scene generates schema-valid scenes within strict bounds."""
    for seed in [1, 42, 999, 12345]:
        rand_scene = randomize_scene(seed=seed, mode="all", variance=0.3)
        assert isinstance(rand_scene, SceneConfig)
        assert rand_scene.anatomy.render_seed == seed
        # Foveal bounds
        assert 0.0 <= rand_scene.anatomy.fovea_center <= rand_scene.acquisition.width * 2.0
        assert rand_scene.anatomy.fovea_pit_depth < rand_scene.acquisition.height
        # Display window
        assert rand_scene.realism.db_hi > rand_scene.realism.db_lo

    # Test API endpoint POST /api/presets/randomize
    req = RandomizeSceneRequest(seed=777, mode="balanced", variance=0.2)
    resp = client.post("/api/presets/randomize", json=req.model_dump())
    assert resp.status_code == 200
    res_scene = resp.json()
    assert res_scene["anatomy"]["render_seed"] == 777


def test_pathology_variants_generation():
    """Verify that pathology variant generator creates reproducible distinct variants."""
    variants = generate_pathology_variants("cme", count=4, base_seed=100)
    assert len(variants) == 4

    seeds = [v.anatomy.render_seed for v in variants]
    assert len(set(seeds)) == 4, "Each variant must have a unique concrete seed"

    # Second generation with same base_seed must be 100% deterministic
    variants_repeat = generate_pathology_variants("cme", count=4, base_seed=100)
    for v1, v2 in zip(variants, variants_repeat):
        assert v1.anatomy.render_seed == v2.anatomy.render_seed
        assert len(v1.pathology.instances) == len(v2.pathology.instances)
        assert v1.pathology.instances[0].severity == v2.pathology.instances[0].severity

    # Test API endpoint POST /api/presets/variants
    req = PathologyVariantsRequest(preset_id="drusen", count=3, base_seed=200)
    resp = client.post("/api/presets/variants", json=req.model_dump())
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["count"] == 3
    assert len(res_data["variants"]) == 3


# ==============================================================================
# 3. Figure Composition Tests
# ==============================================================================

def test_multi_panel_composition_layouts():
    """Verify composition engine across layouts: single, 1x2 (A-B), 1x3 (A-B-C), 2x2 (A-B-C-D)."""
    # 1. Render two scenes to get render_ids
    scene_norm = get_pathology_preset("normal")
    scene_cme = get_pathology_preset("cme")

    r1 = client.post("/api/render/preview", json=scene_norm.model_dump()).json()["render_id"]
    r2 = client.post("/api/render/preview", json=scene_cme.model_dump()).json()["render_id"]

    # Test 2-panel layout (A-B / 1x2)
    comp_cfg = CompositionConfig(
        title="Comparative Macular Pathology",
        subtitle="Normal control vs Cystoid Macular Edema",
        layout="A-B",
        show_legend=True,
        show_metadata_block=True,
        panels=[
            PanelConfig(render_id=r1, label="A", title="Normal Control", auto_label=True),
            PanelConfig(render_id=r2, label="B", title="Cystoid Edema", show_scale_bar=True),
        ]
    )

    req = CompositionRequest(config=comp_cfg)
    resp = client.post("/api/compose", json=req.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["panel_count"] == 2
    assert len(data["panels"]) == 2

    # Check panel 0 and panel 1 coordinates
    p0 = data["panels"][0]
    p1 = data["panels"][1]
    assert p0["label"] == "A"
    assert p1["label"] == "B"
    assert p0["y"] == p1["y"], "Panels in 1x2 layout must align horizontally"
    assert p1["x"] > p0["x"], "Panel B must be placed to the right of Panel A"

    # Decode and verify resulting PNG image
    raw_b64 = data["image"].split("base64,")[-1]
    img = Image.open(io.BytesIO(base64.b64decode(raw_b64)))
    assert img.width > 1000
    assert img.height > 500


def test_4_panel_grid_composition():
    """Verify 4-panel 2x2 grid composition."""
    scene = get_pathology_preset("normal")
    r_id = client.post("/api/render/preview", json=scene.model_dump()).json()["render_id"]

    comp_cfg = CompositionConfig(
        layout="2x2",
        panels=[
            PanelConfig(render_id=r_id, label="A"),
            PanelConfig(render_id=r_id, label="B"),
            PanelConfig(render_id=r_id, label="C"),
            PanelConfig(render_id=r_id, label="D"),
        ]
    )
    req = CompositionRequest(config=comp_cfg)
    resp = client.post("/api/compose", json=req.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["panels"]) == 4

    p0, p1, p2, p3 = data["panels"]
    # Row 0: p0, p1 (same y)
    assert p0["y"] == p1["y"]
    # Row 1: p2, p3 (same y, lower down)
    assert p2["y"] == p3["y"]
    assert p2["y"] > p0["y"]
    # Col 0: p0, p2 (same x)
    assert p0["x"] == p2["x"]
    # Col 1: p1, p3 (same x)
    assert p1["x"] == p3["x"]


# ==============================================================================
# 4. Multi-Format Export Tests
# ==============================================================================

@pytest.mark.parametrize("fmt,preset,exp_mime", [
    ("png", "high_resolution", "image/png"),
    ("jpeg", "web", "image/jpeg"),
    ("tiff", "publication", "image/tiff"),
    ("pdf", "a4", "application/pdf"),
    ("svg", "custom", "image/svg+xml"),
])
def test_export_formats_and_presets(fmt, preset, exp_mime):
    """Test raster, print, and vector export across PNG, JPEG, TIFF, PDF, and SVG formats."""
    scene = get_pathology_preset("srf")
    r_id = client.post("/api/render/preview", json=scene.model_dump()).json()["render_id"]

    exp_cfg = ExportConfig(
        preset=preset,
        format=fmt,
        dpi=300,
        watermark=True,
    )

    req = ExportRequest(
        render_id=r_id,
        config=exp_cfg,
    )

    resp = client.post("/api/export", json=req.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == fmt
    assert data["mime_type"] == exp_mime
    assert data["file_size_bytes"] > 0
    assert data["data"].startswith(f"data:{exp_mime};base64,")

    # Metadata checks
    meta = data["metadata"]
    assert meta["synthetic_content"] is True
    assert "SYNTHETIC — FOR EDUCATION ONLY" in meta["synthetic_notice"]
    assert meta["render_id"] == r_id
    assert meta["render_seed"] == scene.anatomy.render_seed

    # Binary validity checks
    raw_b64 = data["data"].split("base64,")[-1]
    file_bytes = base64.b64decode(raw_b64)

    if fmt == "png":
        img = Image.open(io.BytesIO(file_bytes))
        assert img.format == "PNG"
        # Check embedded tEXt chunk
        assert "SYNTHETIC — FOR EDUCATION ONLY" in img.info.get("SyntheticNotice", "") or "SYNTHETIC" in img.info.get("Comment", "")

    elif fmt == "jpeg":
        img = Image.open(io.BytesIO(file_bytes))
        assert img.format == "JPEG"

    elif fmt == "tiff":
        img = Image.open(io.BytesIO(file_bytes))
        assert img.format == "TIFF"

    elif fmt == "pdf":
        assert file_bytes.startswith(b"%PDF")

    elif fmt == "svg":
        svg_text = file_bytes.decode("utf-8")
        assert "<svg" in svg_text
        assert "<image" in svg_text
        assert "SYNTHETIC — FOR EDUCATION ONLY" in svg_text


def test_export_determinism_preservation():
    """Verify that exporting an image does not mutate the cached render entry or pixel array."""
    scene = get_pathology_preset("drusen")
    r_data = client.post("/api/render/final", json=scene.model_dump()).json()
    r_id = r_data["render_id"]
    orig_b64 = r_data["image"]

    # Export to TIFF
    exp_req = ExportRequest(render_id=r_id, config=ExportConfig(format="tiff", dpi=600))
    exp_resp = client.post("/api/export", json=exp_req.model_dump())
    assert exp_resp.status_code == 200

    # Retrieve render entry via preview endpoint (must hit cache and match identical b64)
    r_check = client.post("/api/render/final", json=scene.model_dump()).json()
    assert r_check["render_id"] == r_id
    assert r_check["image"] == orig_b64
    assert r_check["cached"] is True


# ==============================================================================
# 5. Local Project Persistence Tests
# ==============================================================================

def test_project_crud_lifecycle():
    """Test full project CRUD: create, get, list, save, duplicate, rename, reset, delete."""
    scene = get_pathology_preset("erm")

    # 1. Create project
    create_req = ProjectCreateRequest(
        name="ERM Patient Study",
        description="Pre-operative epiretinal membrane evaluation",
        scene=scene,
    )
    resp_create = client.post("/api/projects", json=create_req.model_dump())
    assert resp_create.status_code == 201
    proj_data = resp_create.json()
    p_id = proj_data["id"]
    assert proj_data["name"] == "ERM Patient Study"
    assert proj_data["octforge_version"] == "1.0.0"

    try:
        # 2. Get project
        resp_get = client.get(f"/api/projects/{p_id}")
        assert resp_get.status_code == 200
        loaded = resp_get.json()
        assert loaded["id"] == p_id
        assert loaded["name"] == "ERM Patient Study"
        assert loaded["scene"]["meta"]["name"] == scene.meta.name

        # 3. List projects
        resp_list = client.get("/api/projects")
        assert resp_list.status_code == 200
        items = resp_list.json()
        matching = [it for it in items if it["id"] == p_id]
        assert len(matching) == 1
        assert matching[0]["name"] == "ERM Patient Study"

        # 4. Update / Save project
        loaded["description"] = "Updated clinical notes"
        resp_put = client.put(f"/api/projects/{p_id}", json=loaded)
        assert resp_put.status_code == 200
        assert resp_put.json()["description"] == "Updated clinical notes"

        # 5. Rename project
        rename_req = ProjectRenameRequest(name="ERM Study (Renamed)", description="New desc")
        resp_rename = client.patch(f"/api/projects/{p_id}/rename", json=rename_req.model_dump())
        assert resp_rename.status_code == 200
        assert resp_rename.json()["name"] == "ERM Study (Renamed)"

        # 6. Duplicate project
        resp_dup = client.post(f"/api/projects/{p_id}/duplicate")
        assert resp_dup.status_code == 200
        dup_data = resp_dup.json()
        dup_id = dup_data["id"]
        assert dup_id != p_id
        assert "Copy" in dup_data["name"]

        # Clean up duplicated project
        client.delete(f"/api/projects/{dup_id}")

        # 7. Reset project
        resp_reset = client.post(f"/api/projects/{p_id}/reset")
        assert resp_reset.status_code == 200
        assert resp_reset.json()["scene"]["pathology"]["enabled"] is False

    finally:
        # 8. Delete project
        resp_del = client.delete(f"/api/projects/{p_id}")
        assert resp_del.status_code == 200

        # Verify 404 after deletion
        assert client.get(f"/api/projects/{p_id}").status_code == 404


def test_project_round_trip_identical_render():
    """Verify save -> load -> render produces pixel-identical output."""
    scene = get_pathology_preset("macular_hole")
    render_1 = client.post("/api/render/final", json=scene.model_dump()).json()["image"]

    # Save to project
    create_req = ProjectCreateRequest(name="FTMH Repro Test", scene=scene)
    proj = client.post("/api/projects", json=create_req.model_dump()).json()
    p_id = proj["id"]

    try:
        # Load project
        loaded_proj = client.get(f"/api/projects/{p_id}").json()
        loaded_scene = loaded_proj["scene"]

        # Render loaded scene
        render_2 = client.post("/api/render/final", json=loaded_scene).json()["image"]
        assert render_1 == render_2, "Render of loaded project must be 100% pixel-identical"
    finally:
        client.delete(f"/api/projects/{p_id}")


def test_export_caliper_measurements_and_scale_bars_toggles():
    """Verify caliper measurements and scale bars are strictly togglable in export."""
    from backend.app.schema import AnnotationSet, AnnotationItem

    scene = get_pathology_preset("normal")
    r_id = client.post("/api/render/preview", json=scene.model_dump()).json()["render_id"]

    annotations = AnnotationSet(
        items=[
            AnnotationItem(
                id="cal_1",
                type="caliper",
                x=100.0,
                y=150.0,
                x2=200.0,
                y2=250.0,
                color="#FFEB3B",
                stroke_width=2.0,
            ),
            AnnotationItem(
                id="label_1",
                type="text",
                x=100.0,
                y=50.0,
                text="Fovea Centralis",
                color="#00BCD4",
            ),
        ]
    )

    # 1. Export SVG with calipers and scale bars enabled
    exp_cfg_all = ExportConfig(
        format="svg",
        include_annotations=True,
        include_measurements=True,
        include_scale_bars=True,
    )
    req_all = ExportRequest(
        render_id=r_id,
        annotations=annotations,
        config=exp_cfg_all,
    )
    resp_all = client.post("/api/export", json=req_all.model_dump())
    assert resp_all.status_code == 200
    svg_all = base64.b64decode(resp_all.json()["data"].split("base64,")[-1]).decode("utf-8")

    assert 'id="cal_1"' in svg_all, "Caliper must appear in SVG when include_measurements is True"
    assert 'id="scale_bars"' in svg_all, "Scale bars must appear in SVG when include_scale_bars is True"
    assert "Fovea Centralis" in svg_all, "Label must appear in SVG when include_annotations is True"

    # 2. Export SVG with calipers disabled, scale bars enabled
    exp_cfg_no_cal = ExportConfig(
        format="svg",
        include_annotations=True,
        include_measurements=False,
        include_scale_bars=True,
    )
    req_no_cal = ExportRequest(
        render_id=r_id,
        annotations=annotations,
        config=exp_cfg_no_cal,
    )
    resp_no_cal = client.post("/api/export", json=req_no_cal.model_dump())
    assert resp_no_cal.status_code == 200
    svg_no_cal = base64.b64decode(resp_no_cal.json()["data"].split("base64,")[-1]).decode("utf-8")

    assert 'id="cal_1"' not in svg_no_cal, "Caliper must NOT appear in SVG when include_measurements is False"
    assert 'id="scale_bars"' in svg_no_cal, "Scale bars must appear in SVG when include_scale_bars is True"
    assert "Fovea Centralis" in svg_no_cal

    # 3. Export SVG with scale bars disabled, calipers enabled
    exp_cfg_no_scale = ExportConfig(
        format="svg",
        include_annotations=True,
        include_measurements=True,
        include_scale_bars=False,
    )
    req_no_scale = ExportRequest(
        render_id=r_id,
        annotations=annotations,
        config=exp_cfg_no_scale,
    )
    resp_no_scale = client.post("/api/export", json=req_no_scale.model_dump())
    assert resp_no_scale.status_code == 200
    svg_no_scale = base64.b64decode(resp_no_scale.json()["data"].split("base64,")[-1]).decode("utf-8")

    assert 'id="cal_1"' in svg_no_scale
    assert 'id="scale_bars"' not in svg_no_scale, "Scale bars must NOT appear in SVG when include_scale_bars is False"
    assert "Fovea Centralis" in svg_no_scale

    # 4. Export Raster PNG with calipers and scale bars (even without client passing measurements object)
    exp_cfg_png = ExportConfig(
        format="png",
        include_annotations=True,
        include_measurements=True,
        include_scale_bars=True,
    )
    req_png = ExportRequest(
        render_id=r_id,
        annotations=annotations,
        measurements=None,  # Not supplied by client, server must auto-derive scale bars!
        config=exp_cfg_png,
    )
    resp_png = client.post("/api/export", json=req_png.model_dump())
    assert resp_png.status_code == 200
    assert resp_png.json()["format"] == "png"
    assert resp_png.json()["file_size_bytes"] > 0

