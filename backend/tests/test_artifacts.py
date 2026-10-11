"""Unit tests for optical and motion artifacts engine in OCTForge.

Tests coverage:
  1. Schema validation (ARTIFACT_TYPES, defaults, config)
  2. Per-artifact determinism (identical seed → identical bytes)
  3. Seed offset variation (different offset → different bytes)
  4. Per-artifact severity sensitivity (monotonic visible changes)
  5. Per-artifact location sensitivity (position shifting)
  6. Layer-ID map alignment and registration under geometric warps (motion)
  7. Multi-artifact composition (multiple artifacts simultaneous execution)
  8. Disabled artifacts flag bypass
"""

import pytest
import numpy as np

from backend.app.schema import (
    SceneConfig,
    ArtifactsConfig,
    ArtifactInstance,
    ARTIFACT_TYPES,
)
from backend.app.rendering.forward_model import render_scene, LAYER_IDS
from backend.app.rendering.artifacts import (
    APPLICATION_ORDER,
    _DISPATCH,
    apply_all_artifacts,
    apply_motion_artifact,
    apply_dropout_artifact,
    apply_shadowing_artifact,
    apply_saturation_artifact,
    apply_mirror_artifact,
    apply_poor_signal_artifact,
    apply_banding_artifact,
)


# ==============================================================================
# SECTION 1: Schema & Dispatch Validation
# ==============================================================================

def test_artifact_types_count():
    """Verify all 7 artifact types are defined and unique."""
    assert len(ARTIFACT_TYPES) == 7
    expected = {"motion", "dropout", "shadowing", "saturation", "mirror", "poor_signal", "banding"}
    assert set(ARTIFACT_TYPES) == expected


def test_dispatch_table_complete():
    """Verify all artifact types have registered handlers in _DISPATCH."""
    for art_type in ARTIFACT_TYPES:
        assert art_type in _DISPATCH, f"Missing dispatch handler for {art_type}"


def test_application_order_complete():
    """Verify all artifact types are included in APPLICATION_ORDER."""
    assert len(APPLICATION_ORDER) == len(ARTIFACT_TYPES)
    assert set(APPLICATION_ORDER) == set(ARTIFACT_TYPES)
    # Motion must be first so subsequent passes operate on warped coordinates
    assert APPLICATION_ORDER[0] == "motion"


def test_artifact_instance_defaults():
    """Verify default parameters of ArtifactInstance."""
    inst = ArtifactInstance(type="motion")
    assert inst.type == "motion"
    assert inst.severity == 0.50
    assert inst.location == 0.50
    assert inst.size == 0.15
    assert inst.count == 1
    assert inst.extent == 0.50
    assert inst.asymmetry == 0.00
    assert inst.reflectivity == 0.50
    assert inst.seed_offset == 0


def test_artifacts_config_defaults():
    """Verify ArtifactsConfig defaults."""
    cfg = ArtifactsConfig()
    assert cfg.enabled is False
    assert cfg.instances == []


# ==============================================================================
# SECTION 2: Per-Artifact Determinism
# ==============================================================================

@pytest.mark.parametrize("art_type", ARTIFACT_TYPES)
def test_artifact_determinism(art_type):
    """Verify identical SceneConfig produces identical pixels and layer maps for every artifact."""
    scene1 = SceneConfig()
    scene1.artifacts.enabled = True
    scene1.artifacts.instances = [ArtifactInstance(type=art_type, severity=0.6, location=0.45)]

    scene2 = SceneConfig()
    scene2.artifacts.enabled = True
    scene2.artifacts.instances = [ArtifactInstance(type=art_type, severity=0.6, location=0.45)]

    pix1, map1, _, _ = render_scene(scene1, preview=False)
    pix2, map2, _, _ = render_scene(scene2, preview=False)

    assert np.array_equal(pix1, pix2), f"Determinism failed for artifact {art_type}"
    assert np.array_equal(map1, map2), f"Layer map determinism failed for artifact {art_type}"


@pytest.mark.parametrize("art_type", ARTIFACT_TYPES)
def test_artifact_seed_offset_variation(art_type):
    """Verify different seed_offsets produce different output for stochastic artifacts."""
    scene1 = SceneConfig()
    scene1.artifacts.enabled = True
    scene1.artifacts.instances = [ArtifactInstance(type=art_type, severity=0.8, seed_offset=10)]

    scene2 = SceneConfig()
    scene2.artifacts.enabled = True
    scene2.artifacts.instances = [ArtifactInstance(type=art_type, severity=0.8, seed_offset=999)]

    pix1, _, _, _ = render_scene(scene1, preview=False)
    pix2, _, _, _ = render_scene(scene2, preview=False)

    # Some artifacts like deterministic warp or dropout may be largely geometric,
    # but stochastic noise/banding elements or positions vary
    diff = np.abs(pix1.astype(float) - pix2.astype(float)).sum()
    if art_type in ["banding", "poor_signal", "motion"]:
        assert diff > 0, f"Seed offset had no effect on stochastic artifact {art_type}"


# ==============================================================================
# SECTION 3: Per-Artifact Visible Effect & Severity Sensitivity
# ==============================================================================

def test_motion_severity_effect():
    """Verify motion artifact severity increases lateral and axial warp difference."""
    base_scene = SceneConfig()
    pix_base, map_base, _, _ = render_scene(base_scene, preview=False)

    scene_low = SceneConfig()
    scene_low.artifacts.enabled = True
    scene_low.artifacts.instances = [ArtifactInstance(type="motion", severity=0.2, location=0.5)]
    pix_low, map_low, _, _ = render_scene(scene_low, preview=False)

    scene_high = SceneConfig()
    scene_high.artifacts.enabled = True
    scene_high.artifacts.instances = [ArtifactInstance(type="motion", severity=0.9, location=0.5)]
    pix_high, map_high, _, _ = render_scene(scene_high, preview=False)

    diff_low = np.abs(pix_low.astype(float) - pix_base.astype(float)).mean()
    diff_high = np.abs(pix_high.astype(float) - pix_base.astype(float)).mean()

    assert diff_low > 0.5
    assert diff_high > diff_low * 1.5, "Higher motion severity should create significantly larger pixel difference"


def test_dropout_severity_effect():
    """Verify signal dropout decreases intensity in the target column range."""
    col = 256
    loc = 0.5

    scene_base = SceneConfig()
    pix_base, _, _, _ = render_scene(scene_base, preview=False)

    scene_drop = SceneConfig()
    scene_drop.artifacts.enabled = True
    scene_drop.artifacts.instances = [ArtifactInstance(type="dropout", severity=0.95, location=loc, size=0.1)]
    pix_drop, map_drop, _, _ = render_scene(scene_drop, preview=False)

    # In retinal region around col 256, dropout should be much darker
    mean_base_col = pix_base[:, col - 5:col + 5].mean()
    mean_drop_col = pix_drop[:, col - 5:col + 5].mean()

    assert mean_drop_col < mean_base_col * 0.4, "Dropout should attenuate signal strongly in target column"
    # Verify destroyed layer-ID map in complete dropout zone
    assert (map_drop[:, col] == -1).any(), "Destructive dropout should mark layer_id_map undefined (-1)"


def test_shadowing_severity_effect():
    """Verify media shadowing creates a Beer-Lambert attenuation column beneath opacity."""
    scene_base = SceneConfig()
    pix_base, _, _, _ = render_scene(scene_base, preview=False)

    scene_shad = SceneConfig()
    scene_shad.artifacts.enabled = True
    scene_shad.artifacts.instances = [ArtifactInstance(type="shadowing", severity=0.85, location=0.5, size=0.15)]
    pix_shad, _, _, _ = render_scene(scene_shad, preview=False)

    col = int(0.5 * scene_base.acquisition.width)
    # Deep retinal & choroidal rows below the opacity (e.g. z > 200)
    shadow_base = pix_base[200:400, col - 10:col + 10].mean()
    shadow_shad = pix_shad[200:400, col - 10:col + 10].mean()

    assert shadow_shad < shadow_base * 0.7, "Media shadowing must darken tissue below the opacity"


def test_saturation_severity_effect():
    """Verify saturation artifact forces pixels to maximum display value (255)."""
    scene_sat = SceneConfig()
    scene_sat.artifacts.enabled = True
    scene_sat.artifacts.instances = [ArtifactInstance(type="saturation", severity=0.9, location=0.5, extent=0.3)]
    pix_sat, _, _, _ = render_scene(scene_sat, preview=False)

    # There should be saturated 255 pixels around the core
    assert (pix_sat == 255).sum() > 20, "Saturation artifact must create hard-clipped 255 pixels"


def test_mirror_severity_effect():
    """Verify mirror artifact superimposes reflected conjugate signal above zero-OPD."""
    scene_base = SceneConfig()
    pix_base, _, _, _ = render_scene(scene_base, preview=False)

    scene_mir = SceneConfig()
    scene_mir.artifacts.enabled = True
    scene_mir.artifacts.instances = [ArtifactInstance(type="mirror", severity=0.8, location=0.25, reflectivity=0.8)]
    pix_mir, _, _, _ = render_scene(scene_mir, preview=False)

    # In upper vitreous region (z < 100), base is dark while mirror has ghost reflection
    vitreous_base = pix_base[:80, :].mean()
    vitreous_mir = pix_mir[:80, :].mean()

    assert vitreous_mir > vitreous_base + 5.0, "Mirror artifact must create bright conjugate signal in upper rows"


def test_poor_signal_severity_effect():
    """Verify poor signal reduces overall retinal signal while raising noise."""
    scene_base = SceneConfig()
    pix_base, _, _, _ = render_scene(scene_base, preview=False)

    scene_poor = SceneConfig()
    scene_poor.artifacts.enabled = True
    scene_poor.artifacts.instances = [ArtifactInstance(type="poor_signal", severity=0.8, size=1.0)]
    pix_poor, _, _, _ = render_scene(scene_poor, preview=False)

    # Retinal core brightness should be reduced
    retina_base = pix_base[100:250, :].mean()
    retina_poor = pix_poor[100:250, :].mean()

    assert retina_poor < retina_base * 0.6, "Poor signal must degrade tissue brightness"


def test_banding_severity_effect():
    """Verify gain banding creates row-wise variation in the affected zone."""
    scene_base = SceneConfig()
    pix_base, _, _, _ = render_scene(scene_base, preview=False)

    scene_band = SceneConfig()
    scene_band.artifacts.enabled = True
    scene_band.artifacts.instances = [ArtifactInstance(type="banding", severity=0.85, location=0.5, size=0.3)]
    pix_band, _, _, _ = render_scene(scene_band, preview=False)

    col = 256
    diff_col = np.abs(pix_band[:, col].astype(float) - pix_base[:, col].astype(float)).mean()
    assert diff_col > 1.0, "Banding must introduce visible row-wise variation"


# ==============================================================================
# SECTION 4: Location Sensitivity
# ==============================================================================

@pytest.mark.parametrize("art_type", ["dropout", "shadowing", "saturation", "banding", "motion"])
def test_artifact_location_sensitivity(art_type):
    """Verify shifting location shifts the affected pixel columns."""
    scene_left = SceneConfig()
    scene_left.artifacts.enabled = True
    scene_left.artifacts.instances = [ArtifactInstance(type=art_type, severity=0.8, location=0.25, size=0.1)]
    pix_left, _, _, _ = render_scene(scene_left, preview=False)

    scene_right = SceneConfig()
    scene_right.artifacts.enabled = True
    scene_right.artifacts.instances = [ArtifactInstance(type=art_type, severity=0.8, location=0.75, size=0.1)]
    pix_right, _, _, _ = render_scene(scene_right, preview=False)

    # Left vs right halves of the images should have distinct differences
    left_diff = np.abs(pix_left[:, :150].astype(float) - pix_right[:, :150].astype(float)).sum()
    right_diff = np.abs(pix_left[:, 350:].astype(float) - pix_right[:, 350:].astype(float)).sum()

    assert left_diff > 0, f"Location shift failed for {art_type} on left half"
    assert right_diff > 0, f"Location shift failed for {art_type} on right half"


# ==============================================================================
# SECTION 5: Layer-ID Map Registration & Alignment
# ==============================================================================

def test_motion_layer_id_map_registration():
    """Verify motion artifact warps layer_id_map in exact alignment with the warped pixels."""
    scene_base = SceneConfig()
    pix_base, map_base, _, _ = render_scene(scene_base, preview=False)

    scene_motion = SceneConfig()
    scene_motion.artifacts.enabled = True
    # Strong motion saccade at location=0.5 with positive axial displacement
    scene_motion.artifacts.instances = [
        ArtifactInstance(type="motion", severity=0.8, location=0.5, size=0.08, asymmetry=1.0)
    ]
    pix_mot, map_mot, _, _ = render_scene(scene_motion, preview=False)

    # Before transition (col 100), coordinates and layer IDs should match baseline
    assert np.array_equal(map_mot[:, 100], map_base[:, 100]), "Columns before motion saccade must match baseline"

    # After transition (col 400), layers are axially shifted downward (deeper z)
    # The RPE layer (ID 8) in base vs motion should be shifted
    rpe_z_base = np.where(map_base[:, 400] == LAYER_IDS["RPE"])[0]
    rpe_z_mot = np.where(map_mot[:, 400] == LAYER_IDS["RPE"])[0]

    assert len(rpe_z_base) > 0 and len(rpe_z_mot) > 0
    # In motion saccade with positive asymmetry, RPE depth should be shifted downward (greater z)
    assert rpe_z_mot.mean() > rpe_z_base.mean(), "RPE layer ID map must shift downward following motion warp"


def test_unaffected_regions_layer_id_map_preservation():
    """Verify non-geometric artifacts preserve exact layer-ID map."""
    scene_base = SceneConfig()
    _, map_base, _, _ = render_scene(scene_base, preview=False)

    scene_shad = SceneConfig()
    scene_shad.artifacts.enabled = True
    scene_shad.artifacts.instances = [
        ArtifactInstance(type="shadowing", severity=0.7, location=0.5),
        ArtifactInstance(type="banding", severity=0.6, location=0.3),
        ArtifactInstance(type="saturation", severity=0.8, location=0.8),
    ]
    _, map_shad, _, _ = render_scene(scene_shad, preview=False)

    # Shadowing, banding, saturation do not displace tissue, so layer_id_map is identical
    assert np.array_equal(map_base, map_shad), "Non-geometric artifacts must preserve layer_id_map exactly"


# ==============================================================================
# SECTION 6: Multi-Artifact Composition & Disabled Bypass
# ==============================================================================

def test_all_seven_artifacts_simultaneous():
    """Verify applying all 7 artifact types simultaneously executes cleanly and deterministically."""
    scene = SceneConfig()
    scene.artifacts.enabled = True
    scene.artifacts.instances = [
        ArtifactInstance(type=t, severity=0.4, location=0.15 + i * 0.12, seed_offset=i * 100)
        for i, t in enumerate(ARTIFACT_TYPES)
    ]

    pix1, map1, curves1, _ = render_scene(scene, preview=False)
    pix2, map2, curves2, _ = render_scene(scene, preview=False)

    assert pix1.shape == (512, 512)
    assert map1.shape == (512, 512)
    assert np.array_equal(pix1, pix2)
    assert np.array_equal(map1, map2)


def test_artifacts_disabled_flag():
    """Verify that enabled=False bypasses all artifact execution."""
    scene_disabled = SceneConfig()
    scene_disabled.artifacts.enabled = False
    scene_disabled.artifacts.instances = [
        ArtifactInstance(type="motion", severity=0.9),
        ArtifactInstance(type="dropout", severity=0.9),
    ]

    scene_empty = SceneConfig()
    scene_empty.artifacts.enabled = True
    scene_empty.artifacts.instances = []

    pix_dis, map_dis, _, _ = render_scene(scene_disabled, preview=False)
    pix_emp, map_emp, _, _ = render_scene(scene_empty, preview=False)

    assert np.array_equal(pix_dis, pix_emp), "Disabled artifacts must produce identical output to empty instances"
