"""Unit tests for backend API endpoints and forward model."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.schema import SceneConfig
from backend.app.rendering.forward_model import render_scene, LAYER_IDS

client = TestClient(app)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _default_payload():
    """Return a minimal valid SceneConfig payload."""
    return {
        "meta": {"name": "Test Scene"},
        "anatomy": {
            "fovea_center": 256.0,
            "fovea_pit_depth": 80.0,
            "fovea_pit_width": 0.30,
            "fovea_depth": 80.0,
            "total_retina_thickness": 250.0,
            "vitreous_reflectivity": 0.05,
            "nfl_reflectivity": 0.85,
            "ipl_reflectivity": 0.60,
            "inl_reflectivity": 0.30,
            "opl_reflectivity": 0.65,
            "onl_reflectivity": 0.20,
            "rpe_reflectivity": 0.95,
            "choroid_reflectivity": 0.40,
            "render_seed": 42,
        },
        "pathology": {"enabled": False, "drusen": [], "fluid_pockets": []},
        "acquisition": {"width": 256, "height": 256, "snr_db": 25.0},
        "realism": {
            "speckle_noise_level": 0.30,
            "attenuation_factor": 0.002,
            "db_lo": -35.0,
            "db_hi": -2.0,
            "display_gamma": 1.55,
            "speckle_avg_count": 3,
        },
        "artifacts": {"enabled": False, "shadowing": []},
    }


# ─── Health ───────────────────────────────────────────────────────────────────

def test_health_endpoint():
    """Test GET /api/health returns 200 and expected status."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.0.0"


# ─── Preview endpoint ─────────────────────────────────────────────────────────

def test_render_preview_endpoint():
    """Test POST /api/render/preview with default valid SceneConfig."""
    payload = _default_payload()
    response = client.post("/api/render/preview", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["image"].startswith("data:image/png;base64,")
    assert "cache_key" in data
    assert data["width"] == 256
    assert data["height"] == 256


def test_render_preview_lru_cache():
    """Second call with identical payload should return a cache hit."""
    payload = _default_payload()
    r1 = client.post("/api/render/preview", json=payload)
    assert r1.status_code == 200

    r2 = client.post("/api/render/preview", json=payload)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["cached"] is True
    assert d2["cache_key"] == r1.json()["cache_key"]


# ─── Final render endpoint ────────────────────────────────────────────────────

def test_render_final_endpoint():
    """POST /api/render/final returns image + boundary_curves + vessel_info + layer_id_map."""
    payload = _default_payload()
    response = client.post("/api/render/final", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "success"
    assert data["image"].startswith("data:image/png;base64,")

    # Boundary curves: must contain all expected keys, each with width values
    curves = data["boundary_curves"]
    expected_keys = {"ILM", "RNFL_bot", "GCL_bot", "IPL_bot", "INL_bot",
                     "OPL_bot", "ONL_bot", "EZ_bot", "RPE_bot", "BM", "CSI"}
    assert expected_keys == set(curves.keys())
    for key, vals in curves.items():
        assert len(vals) == 256, f"Boundary {key} should have width=256 values"

    # Vessel info: expect at least one vessel
    vessels = data["vessel_info"]
    assert len(vessels) >= 1
    for v in vessels:
        assert "x_center" in v
        assert "radius_x" in v
        assert "z_center" in v
        assert "shadow_tau" in v

    # Layer ID map dimensions
    layer_map = data["layer_id_map"]
    assert len(layer_map) == 256         # height rows
    assert len(layer_map[0]) == 256      # width cols


def test_render_final_boundary_ordering():
    """Boundaries must be monotonically non-decreasing (non-crossing)."""
    payload = _default_payload()
    response = client.post("/api/render/final", json=payload)
    assert response.status_code == 200
    curves = response.json()["boundary_curves"]

    ordered_keys = ["ILM", "RNFL_bot", "GCL_bot", "IPL_bot", "INL_bot",
                    "OPL_bot", "ONL_bot", "EZ_bot", "RPE_bot", "BM", "CSI"]
    prev = None
    for key in ordered_keys:
        arr = np.array(curves[key])
        if prev is not None:
            assert np.all(arr >= prev - 0.5), (
                f"Boundary {key} crosses the preceding boundary (found values below it)"
            )
        prev = arr


# ─── Forward model unit tests ─────────────────────────────────────────────────

def _make_scene(seed=42, width=128, height=256, **anatomy_kwargs):
    """Build a small SceneConfig for fast unit tests."""
    data = {
        "anatomy": {
            "render_seed": seed,
            **anatomy_kwargs,
        },
        "acquisition": {"width": width, "height": height},
        "realism": {"speckle_avg_count": 2},
    }
    return SceneConfig.model_validate(data)


def test_determinism_same_seed():
    """Identical SceneConfig (same seed) must produce byte-identical pixel arrays."""
    scene = _make_scene(seed=7)
    pixels_a, _, _, _ = render_scene(scene, preview=False)
    pixels_b, _, _, _ = render_scene(scene, preview=False)
    assert np.array_equal(pixels_a, pixels_b), (
        "render_scene is not deterministic: identical seed → different output"
    )


def test_different_seeds_differ():
    """Different seeds should produce different images."""
    scene_a = _make_scene(seed=1)
    scene_b = _make_scene(seed=2)
    px_a, _, _, _ = render_scene(scene_a, preview=False)
    px_b, _, _, _ = render_scene(scene_b, preview=False)
    assert not np.array_equal(px_a, px_b), (
        "Different seeds produced identical images"
    )


def test_foveal_pit_depth_effect():
    """Increasing fovea_pit_depth should change the output image (visibly alter shape)."""
    scene_shallow = _make_scene(seed=42, fovea_pit_depth=10.0)
    scene_deep    = _make_scene(seed=42, fovea_pit_depth=150.0)
    px_s, _, _, _ = render_scene(scene_shallow, preview=False)
    px_d, _, _, _ = render_scene(scene_deep,    preview=False)
    # Images must differ
    assert not np.array_equal(px_s, px_d)


def test_rnfl_thickness_shifts_boundary():
    """Increasing RNFL thickness should move the RNFL_bot boundary downward."""
    from backend.app.rendering.forward_model import build_boundaries
    import numpy as np

    def _make_anatomy(rnfl_t):
        scene = _make_scene(seed=0, rnfl_thickness=rnfl_t)
        return scene.anatomy

    rng_thin  = np.random.default_rng(0)
    rng_thick = np.random.default_rng(0)
    curves_thin  = build_boundaries(128, 256, _make_anatomy(10.0), rng_thin)
    curves_thick = build_boundaries(128, 256, _make_anatomy(60.0), rng_thick)

    # RNFL_bot should be deeper (higher pixel value) for thicker RNFL
    mean_thin  = np.mean(curves_thin["RNFL_bot"])
    mean_thick = np.mean(curves_thick["RNFL_bot"])
    assert mean_thick > mean_thin, (
        f"Thicker RNFL did not push RNFL_bot deeper: thin={mean_thin:.1f} thick={mean_thick:.1f}"
    )


def test_layer_id_map_coverage():
    """Layer ID map must contain VITREOUS, RNFL, RPE, CHOROID, and SCLERA pixels.

    Uses a 640px-tall image to ensure the full retina + choroid + sclera fits
    within the image frame.
    """
    scene = _make_scene(seed=42, width=128, height=640)
    _, layer_map, _, _ = render_scene(scene, preview=False)
    present_ids = set(np.unique(layer_map).tolist())
    required = {LAYER_IDS["VITREOUS"], LAYER_IDS["RNFL"], LAYER_IDS["RPE"],
                LAYER_IDS["CHOROID"], LAYER_IDS["SCLERA"]}
    assert required.issubset(present_ids), (
        f"Missing layer IDs in map. Required {required}, got {present_ids}"
    )



def test_vessel_shadow_darker():
    """Pixels directly below a vessel should be darker than pixels beside it."""
    scene = _make_scene(seed=42, width=256, height=512)
    pixels, _, _, vessel_info = render_scene(scene, preview=False)
    assert len(vessel_info) >= 1, "Expected at least one vessel"

    v = vessel_info[0]
    xv  = int(v["x_center"])
    bot = int(v["bot_z"])

    # Sample brightness in a column beneath the vessel vs beside it
    below_rows = slice(bot + 20, min(bot + 80, 512))
    xv_col  = np.clip(xv, 5, 250)
    side_col = np.clip(xv + int(v["radius_x"] * 2.5), 5, 250)

    shadow_mean = float(pixels[below_rows, xv_col].mean())
    beside_mean = float(pixels[below_rows, side_col].mean())

    assert shadow_mean < beside_mean, (
        f"Vessel shadow not detected: shadow_mean={shadow_mean:.1f}, "
        f"beside_mean={beside_mean:.1f}"
    )


def test_output_shape():
    """Render output shape matches acquisition dimensions."""
    scene = _make_scene(seed=0, width=192, height=320)
    pixels, layer_map, curves, vessel_info = render_scene(scene, preview=False)
    assert pixels.shape    == (320, 192)
    assert layer_map.shape == (320, 192)
    for key, arr in curves.items():
        assert arr.shape == (192,), f"Boundary {key} wrong shape"


def test_boundaries_non_crossing():
    """All boundaries must be pairwise non-decreasing (no crossing)."""
    scene = _make_scene(seed=99, width=256, height=512)
    _, _, curves, _ = render_scene(scene, preview=False)

    ordered = ["ILM", "RNFL_bot", "GCL_bot", "IPL_bot", "INL_bot",
               "OPL_bot", "ONL_bot", "EZ_bot", "RPE_bot", "BM", "CSI"]
    prev = None
    for key in ordered:
        arr = curves[key]
        if prev is not None:
            violations = int(np.sum(arr < prev - 0.5))
            assert violations == 0, (
                f"Boundary {key} crosses preceding boundary in {violations} columns"
            )
        prev = arr
