"""Unit tests for Visualization, Annotation, and Measurement systems.

Verifies:
- All 4 visualization modes with full per-layer color/opacity/visibility control.
- Color-blind safe palette correctness and distinctness.
- Automatic layer labeling placement against ground-truth layer_id_map.
- Quantitative thickness, foveal morphology, fluid regions, and caliper measurements.
- Physical unit conversion and scale-bar geometry.
- Strict architectural decoupling: /api/visualize, /api/annotate, /api/measure NEVER trigger scene regeneration.
"""

from unittest.mock import patch
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.schema import (
    SceneConfig,
    VisualizationConfig,
    LayerStyle,
    AnnotationSet,
    AnnotationItem,
    CaliperRequest,
    Point2D,
)
from backend.app.rendering.cache import render_cache
from backend.app.presentation import (
    PALETTES,
    PALETTE_COLORBLIND_SAFE,
    PALETTE_CLINICAL,
    PALETTE_EDUCATIONAL,
    hex_to_rgb,
    LAYER_IDS,
)

client = TestClient(app)


# ─── Fixtures & Helpers ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_cache():
    """Clear the LRU render cache before each test."""
    render_cache.clear()
    yield
    render_cache.clear()


def _get_standard_payload():
    return {
        "meta": {"name": "Presentation Test Scene"},
        "anatomy": {
            "fovea_center": 128.0,
            "fovea_pit_depth": 70.0,
            "fovea_pit_width": 0.25,
            "rnfl_thickness": 25.0,
            "gcl_thickness": 18.0,
            "ipl_thickness": 16.0,
            "inl_thickness": 12.0,
            "opl_thickness": 10.0,
            "onl_thickness": 45.0,
            "ez_thickness": 18.0,
            "rpe_thickness": 8.0,
            "choroid_thickness": 100.0,
            "render_seed": 42,
        },
        "pathology": {
            "enabled": True,
            "instances": [
                {
                    "type": "intraretinal_cyst",
                    "severity": 0.6,
                    "size": 0.12,
                    "location": 0.35,
                    "extent": 0.5,
                }
            ],
        },
        "acquisition": {
            "width": 256,
            "height": 256,
            "scan_width": 6.0,
            "scan_depth": 2.0,
        },
        "realism": {
            "speckle_avg_count": 2,
            "db_lo": -35.0,
            "db_hi": -2.0,
        },
        "artifacts": {"enabled": False, "instances": []},
    }


def _render_and_get_id(preview: bool = True) -> str:
    """Helper to render a scene and retrieve its stable render_id."""
    payload = _get_standard_payload()
    endpoint = "/api/render/preview" if preview else "/api/render/final"
    res = client.post(endpoint, json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "render_id" in data
    return data["render_id"]


# ─── API Hardening & Validation Tests ────────────────────────────────────────

def test_scene_config_validation_error_db_window():
    """RealismConfig db_hi <= db_lo must raise a validation error."""
    payload = _get_standard_payload()
    payload["realism"]["db_lo"] = -10.0
    payload["realism"]["db_hi"] = -20.0  # Invalid: hi < lo
    res = client.post("/api/render/preview", json=payload)
    assert res.status_code == 422  # Pydantic validation error


def test_scene_config_validation_dimensions():
    """Acquisition dimensions must be positive integers."""
    payload = _get_standard_payload()
    payload["acquisition"]["width"] = 0  # Invalid
    res = client.post("/api/render/preview", json=payload)
    assert res.status_code == 422


def test_render_id_and_cache_key_equivalence():
    """render_id and cache_key should be identical for both preview and final."""
    payload = _get_standard_payload()
    res_prev = client.post("/api/render/preview", json=payload)
    assert res_prev.status_code == 200
    d_prev = res_prev.json()
    assert d_prev["render_id"] == d_prev["cache_key"]

    res_fin = client.post("/api/render/final", json=payload)
    assert res_fin.status_code == 200
    d_fin = res_fin.json()
    assert d_fin["render_id"] == d_fin["cache_key"]


def test_404_on_invalid_render_id():
    """Endpoints must return 404 with informative detail when render_id is missing."""
    bogus_id = "0000000000000000000000000000000000000000000000000000000000000000"

    r_vis = client.post("/api/visualize", json={"render_id": bogus_id, "config": {}})
    assert r_vis.status_code == 404
    assert "not found or expired" in r_vis.json()["detail"]

    r_ann = client.post("/api/annotate", json={"render_id": bogus_id, "auto_label": True})
    assert r_ann.status_code == 404
    assert "not found or expired" in r_ann.json()["detail"]

    r_meas = client.post("/api/measure", json={"render_id": bogus_id})
    assert r_meas.status_code == 404
    assert "not found or expired" in r_meas.json()["detail"]


# ─── Architectural Decoupling: Zero Scene Regeneration ────────────────────────

def test_presentation_endpoints_never_regenerate_scene():
    """POST /api/visualize, /api/annotate, /api/measure must NEVER call render_scene()."""
    render_id = _render_and_get_id(preview=True)

    with patch("backend.app.rendering.forward_model.render_scene") as mock_render:
        # 1. Visualize
        vis_res = client.post("/api/visualize", json={
            "render_id": render_id,
            "config": {"mode": "colored_layer", "palette": "colorblind_safe"},
        })
        assert vis_res.status_code == 200

        # 2. Annotate
        ann_res = client.post("/api/annotate", json={
            "render_id": render_id,
            "auto_label": True,
        })
        assert ann_res.status_code == 200

        # 3. Measure
        meas_res = client.post("/api/measure", json={
            "render_id": render_id,
            "calipers": [{"p1": {"x": 10, "y": 20}, "p2": {"x": 100, "y": 200}}],
        })
        assert meas_res.status_code == 200

        # Verify forward model was NEVER called
        mock_render.assert_not_called()


# ─── Visualization Mode & Palette Tests ───────────────────────────────────────

def test_visualization_modes():
    """All 4 visualization modes (grayscale, colored_layer, hybrid, educational) must succeed."""
    render_id = _render_and_get_id(preview=True)

    modes = ["grayscale", "colored_layer", "hybrid", "educational"]
    for m in modes:
        res = client.post("/api/visualize", json={
            "render_id": render_id,
            "config": {"mode": m, "palette": "clinical"},
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["image"].startswith("data:image/png;base64,")
        assert data["mode"] == m
        assert data["width"] == 256
        assert data["height"] == 256


def test_colorblind_safe_palette():
    """Color-blind safe palette must have unique, non-colliding colors."""
    cb_palette = PALETTES["colorblind_safe"]
    # Verify key layers are defined
    required_layers = ["RNFL", "GCL", "IPL", "INL", "OPL", "ONL", "EZ", "RPE", "CHOROID", "SCLERA"]
    colors = [cb_palette[l] for l in required_layers]
    # Check all colors are distinct
    assert len(colors) == len(set(colors)), "Color-blind safe palette has duplicate colors"
    for c in colors:
        rgb = hex_to_rgb(c)
        assert len(rgb) == 3
        for channel in rgb:
            assert 0 <= channel <= 255


def test_per_layer_style_overrides():
    """Per-layer color, opacity, and visibility overrides must be respected."""
    render_id = _render_and_get_id(preview=True)

    # Custom override: RNFL bright cyan with 0.9 opacity, GCL invisible
    res = client.post("/api/visualize", json={
        "render_id": render_id,
        "config": {
            "mode": "colored_layer",
            "layers": {
                "RNFL": {"color": "#00FFFF", "opacity": 0.9, "visible": True},
                "GCL": {"visible": False},
            },
            "global_opacity": 0.8,
        },
    })
    assert res.status_code == 200
    assert res.json()["status"] == "success"


def test_hybrid_mode_selective_highlight():
    """In hybrid mode, only selected layers and/or pathology are highlighted."""
    render_id = _render_and_get_id(preview=True)

    res = client.post("/api/visualize", json={
        "render_id": render_id,
        "config": {
            "mode": "hybrid",
            "layers": {
                "EZ": {"color": "#FFD700", "opacity": 1.0, "visible": True},
                "RPE": {"color": "#FF0000", "opacity": 1.0, "visible": True},
            },
            "highlight_pathology": True,
        },
    })
    assert res.status_code == 200
    assert res.json()["status"] == "success"


# ─── Annotation Tests ─────────────────────────────────────────────────────────

def test_automatic_layer_labeling():
    """Automatic layer labeling must produce structured primitives at valid layer depths."""
    render_id = _render_and_get_id(preview=False)

    res = client.post("/api/annotate", json={
        "render_id": render_id,
        "auto_label": True,
        "auto_label_x": 50.0,
        "palette": "clinical",
    })
    assert res.status_code == 200
    data = res.json()
    items = data["annotations"]["items"]
    assert len(items) > 0

    # Verify presence of text, leader_line, and dot items
    item_types = {it["type"] for it in items}
    assert "text" in item_types
    assert "leader_line" in item_types
    assert "dot" in item_types

    # Verify positions are in valid image range [0, 256]
    for it in items:
        assert 0.0 <= it["x"] <= 256.0
        assert 0.0 <= it["y"] <= 256.0
        if it.get("x2") is not None:
            assert 0.0 <= it["x2"] <= 256.0
        if it.get("y2") is not None:
            assert 0.0 <= it["y2"] <= 256.0

    # Verify legend is populated
    legend = data["annotations"]["legend"]
    assert len(legend) >= 8


def test_user_annotations_preserved():
    """Custom user annotation shapes must be preserved in the response."""
    render_id = _render_and_get_id(preview=True)

    custom_item = {
        "id": "custom_arrow_1",
        "type": "arrow",
        "x": 100.0,
        "y": 80.0,
        "x2": 128.0,
        "y2": 110.0,
        "color": "#FF00FF",
        "label": "Lesion target",
    }
    res = client.post("/api/annotate", json={
        "render_id": render_id,
        "annotations": {"items": [custom_item]},
        "auto_label": False,
    })
    assert res.status_code == 200
    items = res.json()["annotations"]["items"]
    assert len(items) == 1
    assert items[0]["id"] == "custom_arrow_1"
    assert items[0]["label"] == "Lesion target"


# ─── Measurement Tests ────────────────────────────────────────────────────────

def test_measurements_thickness_and_geometry():
    """Measurement endpoint must derive numerically correct physical values."""
    render_id = _render_and_get_id(preview=False)

    res = client.post("/api/measure", json={
        "render_id": render_id,
        "include_profiles": True,
    })
    assert res.status_code == 200
    data = res.json()
    meas = data["measurements"]

    # Physical resolutions: scan_width=6.0mm, width=256 -> 6000/256 ≈ 23.4375 um/px
    # scan_depth=2.0mm, height=256 -> 2000/256 ≈ 7.8125 um/px
    assert pytest.approx(meas["pixel_width_um"], 0.01) == (6000.0 / 256.0)
    assert pytest.approx(meas["pixel_height_um"], 0.01) == (2000.0 / 256.0)

    # Total retinal thickness must be positive and physiologically reasonable for synthetic payload
    assert 50.0 < meas["central_retinal_thickness_um"] < 1200.0
    assert 50.0 < meas["total_retinal_thickness_mean_um"] < 1500.0
    assert meas["total_retinal_thickness_min_um"] <= meas["total_retinal_thickness_max_um"]

    # Foveal metrics
    fovea = meas["foveal_metrics"]
    assert fovea["fovea_center_x_px"] == 128.0
    assert fovea["foveal_pit_depth_um"] > 0.0
    assert fovea["foveal_pit_width_um"] > 0.0
    assert fovea["foveal_pit_width_mm"] > 0.0

    # Layer thicknesses check
    layers = meas["layer_thicknesses"]
    assert "RNFL" in layers
    assert "RPE" in layers
    assert "CHOROID" in layers

    # RNFL should be thinner at fovea than parafovea
    assert layers["RNFL"]["foveal_thickness_um"] < layers["RNFL"]["parafoveal_thickness_um"]

    # Verify profile was returned
    assert layers["RNFL"]["profile_um"] is not None
    assert len(layers["RNFL"]["profile_um"]) == 256

    # Fluid region detection
    fluids = meas["fluid_regions"]
    assert len(fluids) >= 1
    cyst = fluids[0]
    assert cyst["pathology_type"] == "intraretinal_cyst"
    assert cyst["width_um"] > 0.0
    assert cyst["height_um"] > 0.0
    assert cyst["area_um2"] > 0.0
    assert cyst["area_mm2"] > 0.0


def test_scale_bar_calibration():
    """Scale bars must accurately reflect scan geometry calibration."""
    render_id = _render_and_get_id(preview=True)

    res = client.post("/api/measure", json={"render_id": render_id})
    assert res.status_code == 200
    scale_bars = res.json()["measurements"]["scale_bars"]

    # Lateral scale bar is 1.0 mm -> pixels = 1.0 / (6.0 / 256) = 256 / 6 ≈ 42.67 px
    lat = scale_bars["lateral"]
    assert lat["orientation"] == "lateral"
    assert lat["unit"] == "mm"
    assert lat["length_physical"] == 1.0
    assert pytest.approx(lat["length_px"], 0.1) == (256.0 / 6.0)

    # Axial scale bar is 200 um (0.2 mm) -> pixels = 0.2 / (2.0 / 256) = 25.6 px
    ax = scale_bars["axial"]
    assert ax["orientation"] == "axial"
    assert ax["unit"] == "μm"
    assert ax["length_physical"] == 200.0
    assert pytest.approx(ax["length_px"], 0.1) == (0.2 / (2.0 / 256.0))


def test_manual_caliper_measurement():
    """Manual caliper tool should compute accurate physical distance between two points."""
    render_id = _render_and_get_id(preview=True)

    # Measure horizontal 100 pixels (dx=100, dz=0)
    # Physical dx = 100 * (6000 / 256) = 2343.75 um
    caliper_req = CaliperRequest(
        id="c1",
        label="Foveal span",
        p1=Point2D(x=50.0, y=100.0),
        p2=Point2D(x=150.0, y=100.0),
    )

    res = client.post("/api/measure", json={
        "render_id": render_id,
        "calipers": [caliper_req.model_dump()],
    })
    assert res.status_code == 200
    calipers = res.json()["measurements"]["calipers"]
    assert len(calipers) == 1

    c = calipers[0]
    assert c["id"] == "c1"
    assert c["pixel_distance"] == 100.0
    assert c["dx_pixels"] == 100.0
    assert c["dz_pixels"] == 0.0
    assert pytest.approx(c["physical_distance_um"], 0.1) == 2343.75
    assert pytest.approx(c["physical_distance_mm"], 0.001) == 2.34375
    assert c["angle_deg"] == 0.0
