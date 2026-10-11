"""OCT Optical and Motion Artifacts Engine.

Implements composable post-processing artifact passes operating on the finished
OCT pixel array and layer-ID map.

Supported Artifact Types:
  - motion       : Saccade-like registration discontinuity (map_coordinates warp
                   with smoothstep easing, axial smear, signal drop, gain banding)
  - dropout      : Signal dropout / interferometric loss in narrow A-scan column range
  - shadowing    : Media opacity (Beer-Lambert attenuation column beneath seeded floater/cataract)
  - saturation   : Hard-clip bright region with bloom/flare
  - mirror       : Fourier-domain conjugate artifact (vertical flip across zero-OPD line)
  - poor_signal  : Regional or whole-frame signal reduction with raised noise floor
  - banding      : Faint row-wise gain variation / scan-line banding

Physical / Procedural labeling:
  - motion       : [PROCEDURAL] simulates sudden eye saccades during scanning
  - dropout      : [PHYSICS] interferometric fringe extinction from tear film/blinking
  - shadowing    : [PHYSICS] Beer-Lambert light extinction beneath media opacities
  - saturation   : [PHYSICS/VISUAL] detector full-well saturation / specular bloom
  - mirror       : [PHYSICS] Fourier-transform complex conjugate ambiguity in SD-OCT
  - poor_signal  : [PHYSICS] reduced SNR / media haze with elevated relative noise
  - banding      : [PROCEDURAL] spectrometer line-rate gain fluctuation

Registration Contract:
  Artifacts that reposition pixels (e.g. motion) warp the layer-ID map identically
  to preserve exact registration with ground-truth anatomy for downstream measurement.
  Where an artifact destroys ground truth (e.g. destructive dropout), layer_id_map is
  marked undefined (-1) in the destroyed column zone.
"""

from typing import Tuple, List, Dict, Callable, Any
import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d, map_coordinates

from backend.app.schema import SceneConfig, ArtifactInstance, ARTIFACT_TYPES


# ==============================================================================
# Fixed Application Order for Deterministic Composition
# ==============================================================================

APPLICATION_ORDER: List[str] = [
    "motion",        # Geometric warp must execute first so subsequent passes act on warped image
    "shadowing",     # Optical attenuation column
    "poor_signal",   # Signal attenuation and noise elevation
    "banding",       # Multiplicative gain variations
    "mirror",        # Superimposes conjugate ghost term
    "dropout",       # Column signal loss
    "saturation",    # Hard-clips high values
]


# ==============================================================================
# Artifact Pass 1: Motion Artifact
# ==============================================================================

def apply_motion_artifact(
    pixels: np.ndarray,
    layer_id_map: np.ndarray,
    instance: ArtifactInstance,
    rng: np.random.Generator,
) -> Tuple[np.ndarray, np.ndarray]:
    """Apply saccade-like motion discontinuity artifact.

    Warp pattern:
      - Smoothstep-eased axial (DZ) + lateral (DX) displacement over short A-scan window.
      - Padded canvas with map_coordinates warp.
      - Local axial smear and signal drop during the moving window.
      - Faint row-wise gain banding confined to neighborhood.
      - Exact coordinate warp applied to layer_id_map (order=0) to keep ground truth registered.
    """
    H, W = pixels.shape
    severity = instance.severity
    if severity <= 0.0:
        return pixels, layer_id_map

    # Transition location and window width
    ic = float(np.clip(instance.location, 0.05, 0.95) * W)
    w_trans = max(4.0, instance.size * W * 0.4)
    i0 = ic - w_trans * 0.5
    i1 = ic + w_trans * 0.5

    # Axial and lateral displacement amplitudes
    sign_z = -1.0 if instance.asymmetry < 0.0 else 1.0
    DZ = (60.0 * severity + 12.0) * sign_z * instance.extent
    DX = -28.0 * severity * (1.0 if instance.asymmetry == 0.0 else abs(instance.asymmetry))

    pad_x = max(60, int(abs(DX) + 25))
    padded_pixels = np.pad(pixels.astype(np.float64), ((0, 0), (pad_x, pad_x)), mode="edge")
    padded_layer_id = np.pad(layer_id_map.astype(np.float64), ((0, 0), (pad_x, pad_x)), mode="edge")

    # Smoothstep interpolation
    ii = np.arange(W, dtype=np.float64)
    s = np.clip((ii - i0) / (i1 - i0 + 1e-9), 0.0, 1.0)
    s = s * s * (3.0 - 2.0 * s)  # smoothstep

    dz = DZ * s
    xt = ii + pad_x + DX * s

    Zg = np.arange(H, dtype=np.float64)[:, np.newaxis]
    coords = np.array([
        np.broadcast_to(Zg - dz[np.newaxis, :], (H, W)),
        np.broadcast_to(xt[np.newaxis, :], (H, W)),
    ])

    # Warp image (bilinear) and layer_id_map (nearest-neighbor)
    warped_pixels = map_coordinates(padded_pixels, coords, order=1, mode="nearest")
    warped_layer_id = map_coordinates(padded_layer_id, coords, order=0, mode="nearest").astype(np.int8)

    # Axial smear and signal drop for A-scans acquired while the eye is moving
    wt = np.exp(-((ii - ic) / max(3.5, w_trans * 0.6)) ** 2)[np.newaxis, :]
    smear_sigma = max(1.0, 12.0 * severity * instance.extent)
    smeared = gaussian_filter1d(warped_pixels, smear_sigma, axis=0)
    warped_pixels = (1.0 - wt * 0.70 * instance.extent) * warped_pixels + (wt * 0.70 * instance.extent) * smeared
    warped_pixels *= (1.0 - 0.48 * severity * wt)

    # Faint row-wise gain banding confined to neighbourhood of event
    band_raw = gaussian_filter1d(rng.standard_normal(H), 1.4)[:, np.newaxis]
    band_raw = band_raw / (band_raw.std() + 1e-9) * 0.12 * severity
    band = 1.0 + band_raw * np.exp(-((ii - ic) / max(16.0, w_trans * 2.5)) ** 2)[np.newaxis, :]
    warped_pixels *= band

    out_pixels = np.clip(warped_pixels + 0.5, 0, 255).astype(np.uint8)
    return out_pixels, warped_layer_id


# ==============================================================================
# Artifact Pass 2: Signal Dropout
# ==============================================================================

def apply_dropout_artifact(
    pixels: np.ndarray,
    layer_id_map: np.ndarray,
    instance: ArtifactInstance,
    rng: np.random.Generator,
) -> Tuple[np.ndarray, np.ndarray]:
    """Apply column signal dropout (e.g. blink, pupil clipping, tear film breakup)."""
    H, W = pixels.shape
    severity = instance.severity
    if severity <= 0.0:
        return pixels, layer_id_map

    col_c = instance.location * W
    w_drop = max(1.5, instance.size * W * 0.25)

    x = np.arange(W, dtype=np.float64)
    prof = np.exp(-0.5 * ((x - col_c) / (w_drop * 0.5 + 1e-6)) ** 4)

    atten = 1.0 - (0.95 * severity) * prof[np.newaxis, :]
    noise = rng.uniform(0.0, 3.0 * severity, (H, W)) * prof[np.newaxis, :]

    out_pixels = np.clip(pixels.astype(np.float64) * atten + noise + 0.5, 0, 255).astype(np.uint8)

    # Where signal is destroyed (>85% extinction), ground truth correspondence is lost
    out_layer_id = layer_id_map.copy()
    if severity > 0.85:
        undefined_mask = (prof > 0.80)[np.newaxis, :]
        out_layer_id = np.where(undefined_mask, -1, out_layer_id).astype(np.int8)

    return out_pixels, out_layer_id


# ==============================================================================
# Artifact Pass 3: Shadowing (Media Opacity)
# ==============================================================================

def apply_shadowing_artifact(
    pixels: np.ndarray,
    layer_id_map: np.ndarray,
    instance: ArtifactInstance,
    rng: np.random.Generator,
) -> Tuple[np.ndarray, np.ndarray]:
    """Apply Beer-Lambert attenuation column beneath a seeded media opacity."""
    H, W = pixels.shape
    severity = instance.severity
    if severity <= 0.0:
        return pixels, layer_id_map

    xv = instance.location * W
    zv = (0.04 + 0.22 * instance.extent) * H
    rx = max(4.0, instance.size * W * 0.22)
    rz = max(3.0, rx * 0.55)
    tau_total = 1.5 + 4.5 * severity

    x = np.arange(W, dtype=np.float64)[np.newaxis, :]
    Z = np.arange(H, dtype=np.float64)[:, np.newaxis]

    dxn = (x - xv) / (rx + 1e-6)
    dzn = (Z - zv) / (rz + 1e-6)

    # Half-chord length per column through the media opacity ellipsoid
    h_chord = rz * np.sqrt(np.clip(1.0 - dxn[0] ** 2, 0.0, 1.0))
    s0 = zv - 0.30 * h_chord[np.newaxis, :]
    span = 1.30 * h_chord[np.newaxis, :] + 1e-6
    a_v = tau_total / (1.30 * rz)
    tau = a_v * np.clip(Z - s0, 0.0, span)

    # Forward scattering: lateral broadening + partial recovery with depth below opacity
    below = np.clip(Z - (zv + rz), 0.0, None)
    recovery = 1.0 - 0.36 * (1.0 - np.exp(-below / (H * 0.55 + 1e-6)))
    tau = tau * np.where(Z > zv + rz, recovery, 1.0)

    sig_row = 3.5 + 0.055 * below[:, 0]
    buckets = np.round(sig_row / 1.5).astype(int)
    for b in np.unique(buckets):
        rows = np.where(buckets == b)[0]
        tau[rows] = gaussian_filter1d(tau[rows], max(b * 1.5, 0.5), axis=1, mode="nearest")

    atten = np.exp(-0.72 * tau)
    atten_pixels = pixels.astype(np.float64) * atten

    # Small hyperreflective/scattering opacity body at (xv, zv)
    dist = np.sqrt(dxn ** 2 + dzn ** 2)
    opacity_mask = np.exp(-((dist - 0.5) / 0.35) ** 2) * (dist <= 1.0)
    body_val = 175.0 * severity * instance.reflectivity
    atten_pixels = np.where(dist < 1.0, np.maximum(atten_pixels, body_val * opacity_mask), atten_pixels)

    out_pixels = np.clip(atten_pixels + 0.5, 0, 255).astype(np.uint8)
    return out_pixels, layer_id_map


# ==============================================================================
# Artifact Pass 4: Saturation
# ==============================================================================

def apply_saturation_artifact(
    pixels: np.ndarray,
    layer_id_map: np.ndarray,
    instance: ArtifactInstance,
    rng: np.random.Generator,
) -> Tuple[np.ndarray, np.ndarray]:
    """Hard-clip a bright region to maximum display value (255) with specular flare."""
    H, W = pixels.shape
    severity = instance.severity
    if severity <= 0.0:
        return pixels, layer_id_map

    col_c = instance.location * W
    z_c = (0.12 + 0.68 * instance.extent) * H
    w_sat = max(3.5, instance.size * W * 0.25)
    h_sat = max(12.0, H * 0.30 * instance.size)

    x = np.arange(W, dtype=np.float64)[np.newaxis, :]
    Z = np.arange(H, dtype=np.float64)[:, np.newaxis]

    dx = (x - col_c) / w_sat
    dz = (Z - z_c) / h_sat
    flare = np.exp(-(dx ** 2 + dz ** 2 * 0.22))

    boost = 280.0 * severity * flare
    boosted = pixels.astype(np.float64) + boost

    # Hard-clip the saturated core to 255
    core_mask = flare > (1.0 - 0.70 * severity)
    boosted = np.where(core_mask, 255.0, boosted)

    out_pixels = np.clip(boosted + 0.5, 0, 255).astype(np.uint8)
    return out_pixels, layer_id_map


# ==============================================================================
# Artifact Pass 5: Mirror Artifact (Zero-OPD Conjugate Term)
# ==============================================================================

def apply_mirror_artifact(
    pixels: np.ndarray,
    layer_id_map: np.ndarray,
    instance: ArtifactInstance,
    rng: np.random.Generator,
) -> Tuple[np.ndarray, np.ndarray]:
    """Apply Fourier-domain complex conjugate mirror artifact across zero-OPD plane."""
    H, W = pixels.shape
    severity = instance.severity
    if severity <= 0.0:
        return pixels, layer_id_map

    zero_opd_z = int(np.clip(instance.location, 0.08, 0.92) * H)
    mirror_gain = (0.22 + 0.38 * severity) * instance.reflectivity

    # Mirror mapping across zero_opd_z:
    # A pixel at (z, x) reflects from source row z_src = 2 * zero_opd_z - z
    Z_indices = np.arange(H)[:, np.newaxis]
    z_src = 2 * zero_opd_z - Z_indices

    valid_mask = (z_src >= 0) & (z_src < H)
    z_src_clipped = np.clip(z_src, 0, H - 1)

    reflected = np.take_along_axis(pixels, z_src_clipped, axis=0).astype(np.float64)
    reflected = np.where(valid_mask, reflected, 0.0)

    # Conjugate term is slightly blurred axially
    blurred_mirror = gaussian_filter(reflected, (2.2, 0.4)) * mirror_gain

    composited = pixels.astype(np.float64) + blurred_mirror

    # Faint zero-OPD horizontal line
    if 0 <= zero_opd_z < H and severity > 0.2:
        composited[zero_opd_z, :] = np.clip(
            composited[zero_opd_z, :] + 30.0 * severity, 0.0, 255.0
        )

    out_pixels = np.clip(composited + 0.5, 0, 255).astype(np.uint8)
    return out_pixels, layer_id_map


# ==============================================================================
# Artifact Pass 6: Poor Signal
# ==============================================================================

def apply_poor_signal_artifact(
    pixels: np.ndarray,
    layer_id_map: np.ndarray,
    instance: ArtifactInstance,
    rng: np.random.Generator,
) -> Tuple[np.ndarray, np.ndarray]:
    """Apply regional or full-frame signal degradation with elevated relative noise."""
    H, W = pixels.shape
    severity = instance.severity
    if severity <= 0.0:
        return pixels, layer_id_map

    atten_val = 1.0 - 0.85 * severity
    if instance.size < 0.85:
        col_c = instance.location * W
        w_span = max(8.0, instance.size * W * 0.5)
        x = np.arange(W, dtype=np.float64)[np.newaxis, :]
        env = np.exp(-0.5 * ((x - col_c) / w_span) ** 2)
        atten_map = 1.0 - (1.0 - atten_val) * env
    else:
        atten_map = np.full((1, W), atten_val, dtype=np.float64)

    noise_amp = (1.0 - atten_map) * (28.0 + 40.0 * severity)
    noise = rng.standard_normal((H, W)) * noise_amp

    degraded = pixels.astype(np.float64) * atten_map + noise
    out_pixels = np.clip(degraded + 0.5, 0, 255).astype(np.uint8)
    return out_pixels, layer_id_map


# ==============================================================================
# Artifact Pass 7: Gain Banding
# ==============================================================================

def apply_banding_artifact(
    pixels: np.ndarray,
    layer_id_map: np.ndarray,
    instance: ArtifactInstance,
    rng: np.random.Generator,
) -> Tuple[np.ndarray, np.ndarray]:
    """Apply row-wise multiplicative gain variation / scan-line banding."""
    H, W = pixels.shape
    severity = instance.severity
    if severity <= 0.0:
        return pixels, layer_id_map

    ic = instance.location * W
    w_band = max(12.0, instance.size * W)
    n_freq = max(1, instance.count)

    raw_band = gaussian_filter1d(rng.standard_normal(H), max(0.8, 3.5 / n_freq))
    raw_band = raw_band / (raw_band.std() + 1e-9) * 0.22 * severity

    x = np.arange(W, dtype=np.float64)[np.newaxis, :]
    lat_env = np.exp(-((x - ic) / w_band) ** 2)

    gain = 1.0 + raw_band[:, np.newaxis] * lat_env
    banded = pixels.astype(np.float64) * gain

    out_pixels = np.clip(banded + 0.5, 0, 255).astype(np.uint8)
    return out_pixels, layer_id_map


# ==============================================================================
# Dispatch Table & Orchestrator
# ==============================================================================

_DISPATCH: Dict[str, Callable[
    [np.ndarray, np.ndarray, ArtifactInstance, np.random.Generator],
    Tuple[np.ndarray, np.ndarray]
]] = {
    "motion":       apply_motion_artifact,
    "dropout":      apply_dropout_artifact,
    "shadowing":    apply_shadowing_artifact,
    "saturation":   apply_saturation_artifact,
    "mirror":       apply_mirror_artifact,
    "poor_signal":  apply_poor_signal_artifact,
    "banding":      apply_banding_artifact,
}


def apply_all_artifacts(
    scene: SceneConfig,
    pixels: np.ndarray,
    layer_id_map: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Apply all configured artifact instances in fixed deterministic order.

    Args:
        scene       : SceneConfig with artifacts.instances
        pixels      : finished uint8 B-scan array (H, W)
        layer_id_map: int8 layer ID map (H, W)

    Returns:
        (pixels, layer_id_map): modified array and updated registered layer-ID map.
    """
    if not scene.artifacts.enabled or not scene.artifacts.instances:
        return pixels, layer_id_map

    # Group instances by type
    by_type: Dict[str, List[ArtifactInstance]] = {}
    for inst in scene.artifacts.instances:
        if inst.type in _DISPATCH:
            by_type.setdefault(inst.type, []).append(inst)

    base_seed = scene.anatomy.render_seed

    out_pixels = pixels.copy()
    out_layer_map = layer_id_map.copy()

    # Apply types in strict APPLICATION_ORDER
    for art_type in APPLICATION_ORDER:
        if art_type not in by_type:
            continue
        func = _DISPATCH[art_type]
        for inst in by_type[art_type]:
            rng = np.random.default_rng(base_seed + inst.seed_offset)
            out_pixels, out_layer_map = func(out_pixels, out_layer_map, inst, rng)

    return out_pixels, out_layer_map


def apply_artifacts(scene: SceneConfig) -> SceneConfig:
    """Legacy helper for backward compatibility with stub."""
    return scene
