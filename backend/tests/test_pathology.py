"""Comprehensive unit tests for the pathology rendering engine.

Tests cover:
  - Per-type determinism (identical inputs → identical output)
  - Parameter sensitivity (severity/size/count produce monotonic measurable effects)
  - Multi-pathology composition (two pathologies applied together don't crash/cancel)
  - Location/distribution correctness
  - Boundary non-crossing after pathology application
  - Schema validation
"""

import numpy as np
import pytest

from backend.app.schema import SceneConfig, PathologyInstance, PathologyConfig, PATHOLOGY_TYPES
from backend.app.rendering.forward_model import render_scene, build_boundaries, LAYER_IDS
from backend.app.rendering.pathology import (
    apply_all_pathologies,
    apply_drusen,
    apply_intraretinal_cyst,
    apply_epiretinal_membrane,
    apply_hyperreflective_foci,
    apply_macular_hole,
    apply_subretinal_fluid,
    _instance_rng,
    APPLICATION_ORDER,
    _DISPATCH,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_scene(seed=42, width=128, height=256, pathology_instances=None, **anatomy_kwargs):
    """Build a small SceneConfig for fast unit tests, optionally with pathology."""
    data = {
        "anatomy": {
            "render_seed": seed,
            **anatomy_kwargs,
        },
        "acquisition": {"width": width, "height": height},
        "realism": {"speckle_avg_count": 2},
    }
    if pathology_instances is not None:
        data["pathology"] = {
            "enabled": True,
            "instances": [inst if isinstance(inst, dict) else inst.model_dump()
                          for inst in pathology_instances],
        }
    else:
        data["pathology"] = {"enabled": False, "instances": []}
    return SceneConfig.model_validate(data)


def _make_instance(ptype, **kwargs):
    """Create a PathologyInstance dict with defaults."""
    return {"type": ptype, **kwargs}


def _render_with_pathology(instances, seed=42, width=128, height=256, **kw):
    """Render a scene with the given pathology instances, return full result."""
    scene = _make_scene(seed=seed, width=width, height=height,
                        pathology_instances=instances, **kw)
    return render_scene(scene, preview=True)


def _render_normal(seed=42, width=128, height=256, **kw):
    """Render a normal (no pathology) scene."""
    scene = _make_scene(seed=seed, width=width, height=height, **kw)
    return render_scene(scene, preview=True)


def _affected_area(pixels_normal, pixels_pathology, threshold=3):
    """Count pixels that differ by more than `threshold` intensity levels."""
    diff = np.abs(pixels_pathology.astype(int) - pixels_normal.astype(int))
    return int(np.sum(diff > threshold))


# ─── Schema Validation ──────────────────────────────────────────────────────

class TestPathologySchema:

    def test_pathology_types_list_complete(self):
        """PATHOLOGY_TYPES list should have all 15 types."""
        assert len(PATHOLOGY_TYPES) == 15

    def test_pathology_instance_defaults(self):
        """PathologyInstance should have sensible defaults for all fields."""
        inst = PathologyInstance(type="drusen")
        assert inst.severity == 0.5
        assert inst.size == 0.10
        assert inst.count == 1
        assert inst.location == 0.5
        assert inst.distribution == 0.15
        assert inst.reflectivity == 0.5
        assert inst.extent == 0.5
        assert inst.asymmetry == 0.0
        assert inst.seed_offset == 0

    def test_pathology_config_backward_compat(self):
        """PathologyConfig should still accept legacy drusen/fluid_pockets fields."""
        config = PathologyConfig(
            enabled=False,
            drusen=[{"size": 10}],
            fluid_pockets=[],
            instances=[],
        )
        assert len(config.drusen) == 1

    def test_scene_with_pathology_instances(self):
        """SceneConfig should accept pathology instances."""
        scene = _make_scene(pathology_instances=[
            _make_instance("drusen", severity=0.7),
            _make_instance("epiretinal_membrane", severity=0.3),
        ])
        assert scene.pathology.enabled is True
        assert len(scene.pathology.instances) == 2
        assert scene.pathology.instances[0].type == "drusen"
        assert scene.pathology.instances[1].severity == 0.3

    def test_all_types_in_dispatch(self):
        """Every PATHOLOGY_TYPES entry should have a handler in _DISPATCH."""
        for ptype in PATHOLOGY_TYPES:
            assert ptype in _DISPATCH, f"No handler for pathology type: {ptype}"

    def test_all_types_in_application_order(self):
        """Every PATHOLOGY_TYPES entry should be in APPLICATION_ORDER."""
        for ptype in PATHOLOGY_TYPES:
            assert ptype in APPLICATION_ORDER, f"Type {ptype} missing from APPLICATION_ORDER"


# ─── Per-Type Determinism ────────────────────────────────────────────────────

class TestDeterminism:

    @pytest.mark.parametrize("ptype", PATHOLOGY_TYPES)
    def test_determinism_per_type(self, ptype):
        """Each pathology type should produce identical output with same seed."""
        instances = [_make_instance(ptype, severity=0.6, seed_offset=100)]
        px_a, _, _, _ = _render_with_pathology(instances, seed=7)
        px_b, _, _, _ = _render_with_pathology(instances, seed=7)
        assert np.array_equal(px_a, px_b), (
            f"Pathology type '{ptype}' is not deterministic"
        )

    def test_different_seed_offset_differs(self):
        """Different seed_offsets should produce different results."""
        inst_a = [_make_instance("drusen", severity=0.8, seed_offset=0)]
        inst_b = [_make_instance("drusen", severity=0.8, seed_offset=999)]
        px_a, _, _, _ = _render_with_pathology(inst_a, seed=42)
        px_b, _, _, _ = _render_with_pathology(inst_b, seed=42)
        assert not np.array_equal(px_a, px_b), (
            "Different seed_offsets produced identical output"
        )


# ─── Parameter Sensitivity ──────────────────────────────────────────────────

class TestParameterSensitivity:

    def test_severity_monotonic_drusen(self):
        """Increasing drusen severity should increase affected area."""
        px_normal, _, _, _ = _render_normal()
        area_low = _affected_area(
            px_normal,
            _render_with_pathology([_make_instance(
                "drusen", severity=0.2, count=5, distribution=0.3)])[0]
        )
        area_high = _affected_area(
            px_normal,
            _render_with_pathology([_make_instance(
                "drusen", severity=0.9, count=5, distribution=0.3)])[0]
        )
        assert area_high > area_low, (
            f"Higher drusen severity should affect more area: low={area_low} high={area_high}"
        )

    def test_size_monotonic_cysts(self):
        """Larger cyst size should affect more pixels."""
        px_normal, _, _, _ = _render_normal()
        area_small = _affected_area(
            px_normal,
            _render_with_pathology([_make_instance("intraretinal_cyst", size=0.03, severity=0.7)])[0]
        )
        area_large = _affected_area(
            px_normal,
            _render_with_pathology([_make_instance("intraretinal_cyst", size=0.15, severity=0.7)])[0]
        )
        assert area_large > area_small, (
            f"Larger cyst size should affect more area: small={area_small} large={area_large}"
        )

    def test_count_monotonic_hyperreflective_foci(self):
        """More hyperreflective foci should affect more pixels."""
        px_normal, _, _, _ = _render_normal()
        area_few = _affected_area(
            px_normal,
            _render_with_pathology([_make_instance("hyperreflective_foci", count=2, severity=0.8)])[0]
        )
        area_many = _affected_area(
            px_normal,
            _render_with_pathology([_make_instance("hyperreflective_foci", count=20, severity=0.8)])[0]
        )
        assert area_many > area_few, (
            f"More foci should affect more area: few={area_few} many={area_many}"
        )

    def test_severity_monotonic_atrophy(self):
        """Higher geographic atrophy severity should produce more change."""
        px_normal, _, _, _ = _render_normal()
        area_mild = _affected_area(
            px_normal,
            _render_with_pathology([_make_instance("geographic_atrophy", severity=0.2, size=0.2)])[0]
        )
        area_severe = _affected_area(
            px_normal,
            _render_with_pathology([_make_instance("geographic_atrophy", severity=0.9, size=0.2)])[0]
        )
        assert area_severe > area_mild, (
            f"Higher atrophy severity should affect more: mild={area_mild} severe={area_severe}"
        )

    def test_severity_monotonic_erm(self):
        """Higher ERM severity should produce more change (brighter/thicker membrane)."""
        px_normal, _, _, _ = _render_normal()
        area_mild = _affected_area(
            px_normal,
            _render_with_pathology([_make_instance("epiretinal_membrane", severity=0.1, size=0.3)])[0]
        )
        area_severe = _affected_area(
            px_normal,
            _render_with_pathology([_make_instance("epiretinal_membrane", severity=1.0, size=0.3)])[0]
        )
        assert area_severe > area_mild, (
            f"Higher ERM severity should affect more: mild={area_mild} severe={area_severe}"
        )


# ─── Location / Distribution Correctness ─────────────────────────────────────

class TestLocationDistribution:

    def test_drusen_location_left(self):
        """Drusen at location=0.2 should primarily affect the left side of the image."""
        px_normal, _, _, _ = _render_normal()
        px_path, _, _, _ = _render_with_pathology([
            _make_instance("drusen", severity=0.8, count=3, location=0.2, distribution=0.05)
        ])
        diff = np.abs(px_path.astype(int) - px_normal.astype(int))
        left_half = diff[:, :64].sum()
        right_half = diff[:, 64:].sum()
        assert left_half > right_half, (
            f"Drusen at location=0.2 should affect left more: left={left_half} right={right_half}"
        )

    def test_drusen_location_right(self):
        """Drusen at location=0.8 should primarily affect the right side."""
        px_normal, _, _, _ = _render_normal()
        px_path, _, _, _ = _render_with_pathology([
            _make_instance("drusen", severity=0.8, count=3, location=0.8, distribution=0.05)
        ])
        diff = np.abs(px_path.astype(int) - px_normal.astype(int))
        left_half = diff[:, :64].sum()
        right_half = diff[:, 64:].sum()
        assert right_half > left_half, (
            f"Drusen at location=0.8 should affect right more: left={left_half} right={right_half}"
        )

    def test_srf_centered(self):
        """SRF at location=0.5 should produce changes concentrated in the center."""
        px_normal, _, _, _ = _render_normal()
        px_path, _, _, _ = _render_with_pathology([
            _make_instance("subretinal_fluid", severity=0.7, location=0.5, size=0.1)
        ])
        diff = np.abs(px_path.astype(int) - px_normal.astype(int))
        center = diff[:, 48:80].sum()
        edges = diff[:, :32].sum() + diff[:, 96:].sum()
        assert center > edges, (
            f"Centered SRF should affect center more: center={center} edges={edges}"
        )


# ─── Multi-Pathology Composition ────────────────────────────────────────────

class TestComposition:

    def test_drusen_plus_erm_no_crash(self):
        """Drusen + ERM applied together should not crash."""
        instances = [
            _make_instance("drusen", severity=0.6, count=3, seed_offset=0),
            _make_instance("epiretinal_membrane", severity=0.5, size=0.4, seed_offset=100),
        ]
        px, layer_map, curves, vinfo = _render_with_pathology(instances)
        assert px.shape == (256, 128)
        assert px.dtype == np.uint8

    def test_drusen_plus_erm_differs_from_each_alone(self):
        """Combined pathology should differ from each individual pathology."""
        px_drusen, _, _, _ = _render_with_pathology([
            _make_instance("drusen", severity=0.6, count=3, seed_offset=0)
        ])
        px_erm, _, _, _ = _render_with_pathology([
            _make_instance("epiretinal_membrane", severity=0.5, size=0.4, seed_offset=100)
        ])
        px_both, _, _, _ = _render_with_pathology([
            _make_instance("drusen", severity=0.6, count=3, seed_offset=0),
            _make_instance("epiretinal_membrane", severity=0.5, size=0.4, seed_offset=100),
        ])
        assert not np.array_equal(px_both, px_drusen), "Combined should differ from drusen alone"
        assert not np.array_equal(px_both, px_erm), "Combined should differ from ERM alone"

    def test_triple_pathology_composition(self):
        """Three pathologies together should not crash and should affect the image."""
        px_normal, _, _, _ = _render_normal()
        instances = [
            _make_instance("drusen", severity=0.5, count=2, seed_offset=0),
            _make_instance("intraretinal_cyst", severity=0.6, count=3, seed_offset=50),
            _make_instance("epiretinal_membrane", severity=0.4, size=0.3, seed_offset=100),
        ]
        px_path, _, _, _ = _render_with_pathology(instances)
        assert px_path.shape == px_normal.shape
        affected = _affected_area(px_normal, px_path)
        assert affected > 50, f"Triple pathology should visibly affect the image: affected={affected}"

    def test_srf_plus_drusen_composition(self):
        """SRF + drusen together should not corrupt shared boundaries."""
        instances = [
            _make_instance("drusen", severity=0.5, count=2, location=0.3, seed_offset=0),
            _make_instance("subretinal_fluid", severity=0.6, location=0.7, seed_offset=50),
        ]
        px, layer_map, curves, _ = _render_with_pathology(instances)
        assert px.shape == (256, 128)
        # Verify boundaries remain ordered
        ordered = ["ILM", "RNFL_bot", "GCL_bot", "IPL_bot", "INL_bot",
                    "OPL_bot", "ONL_bot", "EZ_bot", "RPE_bot", "BM", "CSI"]
        prev = None
        for key in ordered:
            arr = curves[key]
            if prev is not None:
                violations = int(np.sum(arr < prev - 1.0))
                assert violations < arr.shape[0] * 0.1, (
                    f"Too many boundary violations at {key} after SRF+drusen: {violations}"
                )
            prev = arr

    def test_all_types_simultaneously(self):
        """All 15 pathology types applied simultaneously should not crash."""
        instances = []
        for i, ptype in enumerate(PATHOLOGY_TYPES):
            instances.append(_make_instance(
                ptype, severity=0.3, size=0.08, count=2,
                location=0.1 + 0.8 * i / len(PATHOLOGY_TYPES),
                seed_offset=i * 100,
            ))
        px, _, _, _ = _render_with_pathology(instances, width=256, height=512)
        assert px.shape == (512, 256)
        assert px.dtype == np.uint8


# ─── Per-Type Basic Functionality ────────────────────────────────────────────

class TestPerTypeBasic:

    # Type-specific parameter overrides for visible-change test.
    # Some pathologies need tuned parameters to produce visible effects in a small image.
    _TYPE_PARAMS = {
        "drusen":              {"severity": 0.9, "size": 0.2, "count": 5, "distribution": 0.3},
        "rpe_irregularity":    {"severity": 0.9, "size": 0.2, "distribution": 0.4},
        "retinal_thinning":    {"severity": 0.9, "size": 0.3, "extent": 0.0},
        "rpe_attenuation":     {"severity": 0.9, "size": 0.3},
        "geographic_atrophy":  {"severity": 0.9, "size": 0.3},
    }

    @pytest.mark.parametrize("ptype", PATHOLOGY_TYPES)
    def test_type_produces_visible_change(self, ptype):
        """Each pathology type should visibly change the image when enabled."""
        px_normal, _, _, _ = _render_normal()
        params = {"severity": 0.8, "size": 0.15, "count": 3}
        params.update(self._TYPE_PARAMS.get(ptype, {}))
        instances = [_make_instance(ptype, **params)]
        px_path, _, _, _ = _render_with_pathology(instances)
        affected = _affected_area(px_normal, px_path, threshold=2)
        assert affected > 10, (
            f"Pathology type '{ptype}' should produce visible changes: affected={affected}"
        )

    def test_macular_hole_removes_tissue(self):
        """Macular hole should create dark region at center."""
        px_normal, _, _, _ = _render_normal()
        px_path, _, _, _ = _render_with_pathology([
            _make_instance("macular_hole", severity=0.9, size=0.15, location=0.5)
        ])
        # Center column should be darker (tissue removed → vitreous signal)
        center_col = 64  # width=128, center=64
        normal_center_brightness = px_normal[50:200, center_col - 3:center_col + 3].mean()
        path_center_brightness = px_path[50:200, center_col - 3:center_col + 3].mean()
        # The hole region should be darker
        assert path_center_brightness < normal_center_brightness, (
            f"Macular hole should darken center: normal={normal_center_brightness:.1f} "
            f"path={path_center_brightness:.1f}"
        )

    def test_erm_adds_bright_line(self):
        """ERM should add brightness above the ILM."""
        px_normal, _, curves_n, _ = _render_normal()
        px_path, _, curves_p, _ = _render_with_pathology([
            _make_instance("epiretinal_membrane", severity=0.8, size=0.5, location=0.5,
                           reflectivity=0.9)
        ])
        # Look at region above the ILM — should be brighter with ERM
        center_col = 64
        ilm_z = int(curves_n["ILM"][center_col])
        above_ilm_region = slice(max(0, ilm_z - 15), max(0, ilm_z - 2))
        normal_above = px_normal[above_ilm_region, center_col - 5:center_col + 5].mean()
        path_above = px_path[above_ilm_region, center_col - 5:center_col + 5].mean()
        assert path_above > normal_above, (
            f"ERM should brighten area above ILM: normal={normal_above:.1f} path={path_above:.1f}"
        )


# ─── Boundary Integrity ─────────────────────────────────────────────────────

class TestBoundaryIntegrity:

    @pytest.mark.parametrize("ptype", ["drusen", "subretinal_fluid",
                                        "pigment_epithelial_detachment",
                                        "cystoid_macular_edema",
                                        "retinal_thinning"])
    def test_boundaries_reasonable_after_pathology(self, ptype):
        """Boundaries should remain within reasonable bounds after pathology."""
        height = 512  # Use a taller image so all layers fit
        instances = [_make_instance(ptype, severity=0.7, size=0.2)]
        _, _, curves, _ = _render_with_pathology(instances, width=128, height=height)
        for key, arr in curves.items():
            assert np.all(arr > -100), f"{key} has values too far above image after {ptype}"
            assert np.all(arr < height + 200), f"{key} has values too far below image after {ptype}"


# ─── Disabled Pathology Should Not Change Output ─────────────────────────────

class TestDisabledPathology:

    def test_disabled_pathology_no_change(self):
        """When pathology.enabled=False, output should match no-pathology render."""
        scene_normal = _make_scene(seed=42)
        scene_disabled = SceneConfig.model_validate({
            "anatomy": {"render_seed": 42},
            "acquisition": {"width": 128, "height": 256},
            "realism": {"speckle_avg_count": 2},
            "pathology": {
                "enabled": False,
                "instances": [
                    {"type": "drusen", "severity": 0.8},
                ],
            },
        })
        px_normal, _, _, _ = render_scene(scene_normal, preview=True)
        px_disabled, _, _, _ = render_scene(scene_disabled, preview=True)
        assert np.array_equal(px_normal, px_disabled), (
            "Disabled pathology should not change the output"
        )

    def test_empty_instances_no_change(self):
        """Enabled but empty instances list should not change output."""
        scene_normal = _make_scene(seed=42)
        scene_empty = SceneConfig.model_validate({
            "anatomy": {"render_seed": 42},
            "acquisition": {"width": 128, "height": 256},
            "realism": {"speckle_avg_count": 2},
            "pathology": {"enabled": True, "instances": []},
        })
        px_normal, _, _, _ = render_scene(scene_normal, preview=True)
        px_empty, _, _, _ = render_scene(scene_empty, preview=True)
        assert np.array_equal(px_normal, px_empty), (
            "Empty instances list should not change the output"
        )
