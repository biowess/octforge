"""Seeded Regression Tests for OCTForge Forward Rendering Engine.

Asserts that deterministic seeded render outputs (pixels and layer maps)
do not silently drift across refactors, optimizations, or dependency changes.
"""

import hashlib
import numpy as np
import pytest

from backend.app.schema import SceneConfig, PathologyInstance, ArtifactInstance
from backend.app.rendering.forward_model import render_scene
from backend.app.presets import (
    _build_normal_macula,
    _build_cme,
    _build_drusen,
    _build_srf,
    _build_macular_hole,
    _build_erm,
)


# Expected SHA-256 checksums for fixed-seed scenes (preview=False)
SEEDED_REGRESSION_TARGETS = {
    "normal_macula_seed42": {
        "pixels_sha256": "a256093fbdc7764aa1f53087ec95337bc198943fabfabbdffb0308eb5e7e8ba2",
        "layer_map_sha256": "896f109dc3e3e5df18b28a47add731c39bf798bdce39372828abb374d3c6f90b",
        "shape": (512, 512),
        "mean_intensity": 57.9203,
    },
    "cme_seed42": {
        "pixels_sha256": "f78b104c9d2a6726b1364b1f42a0f42e9fc7efd2767f5ace95cdf33db9c31c31",
        "layer_map_sha256": "557869948715b56333ced778b5df5a93ff881c42100c7c0331986c6b34300274",
        "shape": (512, 512),
        "mean_intensity": 59.7365,
    },
    "drusen_seed42": {
        "pixels_sha256": "fe52d4cdabece24b622327f7ced3033c091b570b732e5db66e1054138d175f8c",
        "layer_map_sha256": "8f6b96db545f068cd8797183279307ef84f1a5601d729d51569a62e147addcb0",
        "shape": (512, 512),
        "mean_intensity": 57.3854,
    },
    "srf_seed42": {
        "pixels_sha256": "8ff250ce4b5287959589267381014c97f056278c8d231d3c68dc7546717670b5",
        "layer_map_sha256": "ac995261b4c9ffa6539fe0a85fa1b42d93798457aa97f41afc04fbf828429956",
        "shape": (512, 512),
        "mean_intensity": 53.4836,
    },
    "macular_hole_seed42": {
        "pixels_sha256": "ff4351270ea95aa6930259693e0389f272a57ae4042c41daa1d9a03391dcaaf7",
        "layer_map_sha256": "297084efe0a64df80157d93e26de79e66fbda5c5c1e8fabb655b8b698f4f9719",
        "shape": (512, 512),
        "mean_intensity": 57.1138,
    },
    "erm_seed42": {
        "pixels_sha256": "2c6ded27caa6a7c9d16e5de1cd4debecf60b6f65c806bfc601a3abebcf55fcb6",
        "layer_map_sha256": "896f109dc3e3e5df18b28a47add731c39bf798bdce39372828abb374d3c6f90b",
        "shape": (512, 512),
        "mean_intensity": 59.0744,
    },
    "motion_artifact_seed42": {
        "pixels_sha256": "71de2a0aaf33e78cb6bf9c73863069114224e41ebd107ce302733148a3b9bcb6",
        "layer_map_sha256": "75d15a9603ec5ac31b41f031c40005d12533019f5f46ce13ae104decb08abe5f",
        "shape": (512, 512),
        "mean_intensity": 56.0015,
    },
    "dropout_artifact_seed42": {
        "pixels_sha256": "4b69770952c1d5a84b191e165602b51ce2823ea9216e09ec8c84cca7ce5bd8c0",
        "layer_map_sha256": "896f109dc3e3e5df18b28a47add731c39bf798bdce39372828abb374d3c6f90b",
        "shape": (512, 512),
        "mean_intensity": 56.9377,
    },
}


def _get_regression_scene(name: str) -> SceneConfig:
    if name == "normal_macula_seed42":
        return _build_normal_macula()
    elif name == "cme_seed42":
        return _build_cme()
    elif name == "drusen_seed42":
        return _build_drusen()
    elif name == "srf_seed42":
        return _build_srf()
    elif name == "macular_hole_seed42":
        return _build_macular_hole()
    elif name == "erm_seed42":
        return _build_erm()
    elif name == "motion_artifact_seed42":
        sc = _build_normal_macula()
        sc.artifacts.enabled = True
        sc.artifacts.instances = [
            ArtifactInstance(type="motion", severity=0.6, location=0.5, size=0.15, seed_offset=10)
        ]
        return sc
    elif name == "dropout_artifact_seed42":
        sc = _build_normal_macula()
        sc.artifacts.enabled = True
        sc.artifacts.instances = [
            ArtifactInstance(type="dropout", severity=0.7, location=0.45, size=0.1, seed_offset=20)
        ]
        return sc
    raise ValueError(f"Unknown regression scene: {name}")


@pytest.mark.parametrize("scene_name", list(SEEDED_REGRESSION_TARGETS.keys()))
def test_seeded_render_checksum_integrity(scene_name: str):
    """Assert that rendered output checksums do not drift from verified ground truth."""
    scene = _get_regression_scene(scene_name)
    target = SEEDED_REGRESSION_TARGETS[scene_name]

    pixels, layer_map, curves, vessel_info = render_scene(scene, preview=False)

    # Check dimensions and data types
    assert pixels.shape == target["shape"]
    assert pixels.dtype == np.uint8
    assert layer_map.shape == target["shape"]
    assert layer_map.dtype == np.int8

    # Check SHA-256 hashes
    actual_pixels_hash = hashlib.sha256(pixels.tobytes()).hexdigest()
    actual_layer_map_hash = hashlib.sha256(layer_map.tobytes()).hexdigest()

    assert actual_pixels_hash == target["pixels_sha256"], (
        f"Render output pixel checksum drift detected for '{scene_name}'!\n"
        f"Expected: {target['pixels_sha256']}\n"
        f"Actual:   {actual_pixels_hash}\n"
        f"Mean intensity: expected {target['mean_intensity']:.4f}, actual {float(np.mean(pixels)):.4f}"
    )

    assert actual_layer_map_hash == target["layer_map_sha256"], (
        f"Layer-ID map checksum drift detected for '{scene_name}'!\n"
        f"Expected: {target['layer_map_sha256']}\n"
        f"Actual:   {actual_layer_map_hash}"
    )

    # Mean intensity within small tolerance
    assert abs(float(np.mean(pixels)) - target["mean_intensity"]) < 1e-3
