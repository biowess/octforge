"""Unit tests for Acquisition and Realism controls and Preview-vs-Final performance.

Tests coverage:
  1. Acquisition geometry & sampling parameters
  2. Signal strength & optical resolution scaling
  3. Realism parameters (speckle amount, contrast, gamma, blur, noise floor, nonuniformity, boundary uncertainty)
  4. Preview vs Final quality split & timing benchmark
"""

import time
import pytest
import numpy as np

from backend.app.schema import SceneConfig
from backend.app.rendering.forward_model import render_scene, build_speckle, to_gray


# ==============================================================================
# SECTION 1: Acquisition Parameter Sensitivity
# ==============================================================================

def test_signal_strength_sensitivity():
    """Verify signal_strength scales optical signal before noise floor."""
    scene_low = SceneConfig()
    scene_low.acquisition.signal_strength = 2.0
    pix_low, _, _, _ = render_scene(scene_low, preview=False)

    scene_high = SceneConfig()
    scene_high.acquisition.signal_strength = 10.0
    pix_high, _, _, _ = render_scene(scene_high, preview=False)

    # High signal strength should produce brighter retina
    mean_low = pix_low[100:250, :].mean()
    mean_high = pix_high[100:250, :].mean()
    assert mean_high > mean_low, "Higher signal strength must produce brighter retinal intensity"


def test_optical_resolution_blur_scaling():
    """Verify axial and lateral resolution settings affect PSF blur."""
    scene_sharp = SceneConfig()
    scene_sharp.acquisition.axial_resolution = 1.5
    scene_sharp.acquisition.lateral_resolution = 3.0
    pix_sharp, _, _, _ = render_scene(scene_sharp, preview=False)

    scene_blurred = SceneConfig()
    scene_blurred.acquisition.axial_resolution = 12.0
    scene_blurred.acquisition.lateral_resolution = 35.0
    pix_blurred, _, _, _ = render_scene(scene_blurred, preview=False)

    # Blurrier image has lower gradient / edges
    grad_sharp = np.abs(np.diff(pix_sharp.astype(float), axis=0)).mean()
    grad_blurred = np.abs(np.diff(pix_blurred.astype(float), axis=0)).mean()

    assert grad_sharp > grad_blurred, "Finer optical resolution must produce sharper layer gradients"


# ==============================================================================
# SECTION 2: Realism Parameter Sensitivity
# ==============================================================================

def test_speckle_amount_sensitivity():
    """Verify speckle_amount controls speckle contrast."""
    scene_smooth = SceneConfig()
    scene_smooth.realism.speckle_amount = 0.0
    pix_smooth, _, _, _ = render_scene(scene_smooth, preview=False)

    scene_speckled = SceneConfig()
    scene_speckled.realism.speckle_amount = 1.0
    pix_speckled, _, _, _ = render_scene(scene_speckled, preview=False)

    # Pixel difference between noiseless and fully speckled image
    diff = np.abs(pix_speckled.astype(float) - pix_smooth.astype(float)).mean()
    assert diff > 2.0, "Speckle amount 1.0 must visibly differ from speckle amount 0.0"

    # Speckled image has higher high-frequency lateral grain/gradient
    grad_smooth = np.abs(np.diff(pix_smooth.astype(float), axis=1)).mean()
    grad_speckled = np.abs(np.diff(pix_speckled.astype(float), axis=1)).mean()
    assert grad_speckled > grad_smooth * 1.2, "Speckle amount 1.0 must have higher high-frequency lateral gradient"


def test_contrast_sensitivity():
    """Verify contrast stretches dynamic range."""
    scene_low = SceneConfig()
    scene_low.realism.contrast = 0.5
    pix_low, _, _, _ = render_scene(scene_low, preview=False)

    scene_high = SceneConfig()
    scene_high.realism.contrast = 2.0
    pix_high, _, _, _ = render_scene(scene_high, preview=False)

    std_low = pix_low.std()
    std_high = pix_high.std()

    assert std_high > std_low * 1.3, "Higher contrast must widen standard deviation of intensities"


def test_gamma_sensitivity():
    """Verify display_gamma darkens midtones for gamma > 1."""
    scene_bright = SceneConfig()
    scene_bright.realism.display_gamma = 0.8
    pix_bright, _, _, _ = render_scene(scene_bright, preview=False)

    scene_dark = SceneConfig()
    scene_dark.realism.display_gamma = 2.5
    pix_dark, _, _, _ = render_scene(scene_dark, preview=False)

    assert pix_bright.mean() > pix_dark.mean() * 1.3, "Lower gamma must produce brighter overall image"


def test_background_noise_sensitivity():
    """Verify background_noise level controls noise in vitreous region."""
    scene_low = SceneConfig()
    scene_low.realism.background_noise = -45.0
    pix_low, _, _, _ = render_scene(scene_low, preview=False)

    scene_high = SceneConfig()
    scene_high.realism.background_noise = -20.0
    pix_high, _, _, _ = render_scene(scene_high, preview=False)

    vitreous_low = pix_low[:50, :].mean()
    vitreous_high = pix_high[:50, :].mean()

    assert vitreous_high > vitreous_low + 5.0, "Higher background noise must raise vitreous pixel brightness"


def test_intensity_nonuniformity_sensitivity():
    """Verify intensity_nonuniformity adds slow-varying modulation."""
    scene_flat = SceneConfig()
    scene_flat.realism.intensity_nonuniformity = 0.0
    pix_flat, _, _, _ = render_scene(scene_flat, preview=False)

    scene_nonunif = SceneConfig()
    scene_nonunif.realism.intensity_nonuniformity = 0.4
    pix_nonunif, _, _, _ = render_scene(scene_nonunif, preview=False)

    diff = np.abs(pix_flat.astype(float) - pix_nonunif.astype(float)).mean()
    assert diff > 1.0, "Intensity nonuniformity must affect pixel values"


def test_boundary_uncertainty_sensitivity():
    """Verify boundary_uncertainty injects high-frequency boundary jitter."""
    scene_clean = SceneConfig()
    scene_clean.realism.boundary_uncertainty = 0.0
    _, _, curves_clean, _ = render_scene(scene_clean, preview=False)

    scene_jitter = SceneConfig()
    scene_jitter.realism.boundary_uncertainty = 3.0
    _, _, curves_jitter, _ = render_scene(scene_jitter, preview=False)

    # ILM boundary should differ
    ilm_diff = np.abs(curves_clean["ILM"] - curves_jitter["ILM"]).mean()
    assert ilm_diff > 0.5, "Boundary uncertainty must modulate boundary curves"


# ==============================================================================
# SECTION 3: Preview vs Final Performance & Quality Split
# ==============================================================================

def test_preview_vs_final_timing_benchmark():
    """Verify preview render is meaningfully faster than final render."""
    scene = SceneConfig()
    scene.realism.speckle_avg_count = 12  # full quality speckle count

    # Warmup
    _ = render_scene(scene, preview=True)
    _ = render_scene(scene, preview=False)

    # Benchmark Preview
    t0 = time.perf_counter()
    n_runs = 4
    for _ in range(n_runs):
        _ = render_scene(scene, preview=True)
    time_preview = (time.perf_counter() - t0) / n_runs

    # Benchmark Final
    t0 = time.perf_counter()
    for _ in range(n_runs):
        _ = render_scene(scene, preview=False)
    time_final = (time.perf_counter() - t0) / n_runs

    # Final should take longer than preview (at least ~1.2x)
    assert time_final > time_preview * 1.2, (
        f"Preview ({time_preview*1000:.1f}ms) must be meaningfully faster than Final ({time_final*1000:.1f}ms)"
    )


def test_preview_output_contract():
    """Verify preview returns valid uint8 arrays with correct dimensions."""
    scene = SceneConfig()
    pixels, layer_id_map, curves, vessel_info = render_scene(scene, preview=True)

    assert pixels.dtype == np.uint8
    assert pixels.shape == (512, 512)
    assert layer_id_map.dtype == np.int8
    assert layer_id_map.shape == (512, 512)
    assert "ILM" in curves
    assert len(curves["ILM"]) == 512
    assert len(vessel_info) > 0
