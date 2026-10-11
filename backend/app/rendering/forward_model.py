"""OCT Forward Model Renderer.

Implements a deterministic, physics-inspired OCT B-scan synthesis pipeline.

Pipeline (each step is a named pure function):
  1. build_boundaries()        – parametric layer boundary curves + foveal pit + noise
  2. build_reflectivity_map()  – per-pixel linear reflectivity R(z, x)
  3. build_attenuation_map()   – per-tissue attenuation coefficient μ(z, x)
  4. apply_beer_lambert()      – cumulative two-way depth integral → exp(-2∫μ dz)
  5. build_choroid()           – stromal decay + dark choroidal vessel lumina
  6. build_vessels()           – inner-retina vessel cross-sections + Beer-Lambert shadow
  7. build_speckle()           – PSF-correlated coherent speckle averaged over N realizations
  8. add_noise_floor()         – small additive noise floor (vitreous / shadow regions non-black)
  9. to_gray()                 – 10*log10 + [db_lo, db_hi] clip window + gamma → [0, 1] float
 10. render_scene()            – orchestrates all steps; returns (pixels, layer_id_map,
                                 boundary_curves, vessel_info)

Physical motivation labels:
  [PHYSICS]     : equation follows directly from optics / Beer-Lambert law
  [PROCEDURAL]  : parametric approximation matching OCT phenomenology
  [VISUAL]      : purely aesthetic / display mapping choice

Public contract :
    pixels       : np.ndarray uint8 shape (H, W)
    layer_id_map : np.ndarray int8  shape (H, W)  — see LAYER_IDS below
    boundary_curves: dict[str, np.ndarray(W,)] of boundary depth (pixels) per column
    vessel_info  : list[dict] with keys: x_center, radius_x, radius_z, z_center,
                   shadow_tau, top_z, bot_z

Determinism: all random draws use numpy.random.default_rng(seed) derived from
anatomy.render_seed so identical SceneConfig → byte-identical output.
"""

import io
import base64
from typing import Dict, List, Tuple, Any

import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d
from PIL import Image

from backend.app.schema import SceneConfig
from backend.app.rendering.pathology import apply_all_pathologies
from backend.app.rendering.artifacts import apply_all_artifacts

# ---------------------------------------------------------------------------
# Layer ID constants
# ---------------------------------------------------------------------------
LAYER_IDS: Dict[str, int] = {
    "VITREOUS": 0,
    "RNFL":     1,
    "GCL":      2,
    "IPL":      3,
    "INL":      4,
    "OPL":      5,
    "ONL":      6,
    "EZ":       7,   # photoreceptor band (ELM → RPE)
    "RPE":      8,
    "CHOROID":  9,
    "SCLERA":  10,
    "VESSEL":  11,   # overwrites inner-retina layers where a vessel cross-section sits
}

LAYER_NAMES = list(LAYER_IDS.keys())

# ---------------------------------------------------------------------------
# Default per-layer base linear reflectivities (reference: fig3.py LAYERS table
# and fig7.py LAYERS).  These are scaled by the per-layer reflectivity factors
# from AnatomyConfig, so the user can tune relative brightnesses.
# ---------------------------------------------------------------------------
_BASE_REFL = {
    "VITREOUS": 2e-4,
    "RNFL":     0.50,
    "GCL":      0.10,
    "IPL":      0.26,
    "INL":      0.055,
    "OPL":      0.26,
    "ONL":      0.034,
    "EZ":       0.85,   # combined ELM + IS + EZ (peak bright line)
    "RPE":      1.00,   # brightest structure
    "CHOROID":  0.10,   # stroma base; modulated below
    "SCLERA":   0.038,
}

# Per-layer two-way power attenuation coefficient (pixel^-1) [PHYSICS]
# These are cumulated downward; RPE is strongly absorbing.
_BASE_ATTEN = {
    "VITREOUS": 0.0,
    "RNFL":     0.0026,
    "GCL":      0.0026,
    "IPL":      0.0026,
    "INL":      0.0026,
    "OPL":      0.0026,
    "ONL":      0.0020,
    "EZ":       0.0020,
    "RPE":      0.0300,  # high absorption (melanin)
    "CHOROID":  0.0055,
    "SCLERA":   0.0030,
}

# Number of inner layers that thin/vanish in the foveal pit [PROCEDURAL]
_FOVEAL_INNER_LAYERS = ["RNFL", "GCL", "IPL", "INL", "OPL"]


# ==============================================================================
# Step 1 — Boundary geometry
# ==============================================================================

def build_boundaries(
    width: int,
    height: int,
    anatomy,
    rng: np.random.Generator,
    realism=None,
) -> Dict[str, np.ndarray]:
    """Compute layer boundary depth curves (pixels) as a function of lateral column.

    Returns a dict mapping boundary names to 1-D float arrays of shape (width,):
        "ILM", "RNFL_bot", "GCL_bot", "IPL_bot", "INL_bot", "OPL_bot",
        "ONL_bot", "EZ_bot", "RPE_bot", "BM", "CSI"  (choroid-sclera interface)

    All values are axial depth in image pixels (0 = top of image).
    Boundaries are guaranteed non-crossing by construction.

    Physical motivation: [PROCEDURAL] parametric foveal pit + [PHYSICS]-inspired
    boundary ordering; noise term is purely phenomenological.
    """
    x = np.arange(width, dtype=np.float64)
    cx = anatomy.fovea_center
    # Normalised lateral coordinate [-1, 1] relative to scan centre
    u = (x - width / 2.0) / (width / 2.0)

    # -- ILM baseline: parabolic curvature + lateral asymmetry tilt ----------
    # [PROCEDURAL] gentle parabolic bowl matching clinical macular shape
    ilm_base_z = height * 0.22  # top of retina sits at ~22% from top of image
    ilm_curve = anatomy.curvature * height * (u ** 2)          # parabolic curvature
    ilm_tilt  = anatomy.boundary_asymmetry * height * 0.05 * u  # lateral tilt

    # -- Foveal pit: Gaussian depression of the ILM at the foveal center -------
    # [PROCEDURAL] Gaussian is the standard parametric foveal pit model.
    # The ILM dips inward (deeper z) at the foveal center; fovea_pit_depth
    # controls how many pixels deep this depression is.
    pit_sigma_px = anatomy.fovea_pit_width * width
    pit_profile = anatomy.fovea_pit_depth * np.exp(-0.5 * ((x - cx) / pit_sigma_px) ** 2)

    # -- Band-limited boundary roughness noise --------------------------------
    # [PROCEDURAL] Gaussian-filtered white noise gives spatially correlated
    # undulations matching OCT boundary irregularity
    noise_sigma_px = anatomy.boundary_smoothness * width
    amp = anatomy.boundary_variation

    def _noise_curve(rng: np.random.Generator) -> np.ndarray:
        """Generate one smooth noise realization, zero-mean, amplitude ~amp px."""
        raw = rng.standard_normal(width)
        smoothed = gaussian_filter1d(raw, noise_sigma_px, mode="reflect")
        smoothed = smoothed / (smoothed.std() + 1e-9) * amp
        return smoothed

    # Boundary uncertainty jitter from RealismConfig
    bnd_jitter = 0.0
    if realism is not None and getattr(realism, "boundary_uncertainty", 0.0) > 0.0:
        unc = float(realism.boundary_uncertainty)
        raw_j = rng.standard_normal(width)
        sm_j = gaussian_filter1d(raw_j, max(1.0, noise_sigma_px * 0.25), mode="reflect")
        bnd_jitter = sm_j / (sm_j.std() + 1e-9) * unc

    # ILM: baseline + curvature + tilt + foveal pit depression + boundary noise + jitter
    # pit_profile pushes ILM deeper at the fovea (positive z = downward in image)
    ilm = ilm_base_z + ilm_curve + ilm_tilt + pit_profile + _noise_curve(rng) + bnd_jitter

    # -- Scale inner-layer thicknesses to thin at fovea -----------------------
    # [PROCEDURAL] Inner layers (RNFL→OPL) absent at foveal center:
    # they thin following the complement of the pit Gaussian envelope.
    pit_attenuation = 1.0 - np.exp(-0.5 * ((x - cx) / pit_sigma_px) ** 2)


    # Parafoveal thicknesses (from anatomy)
    t_rnfl = anatomy.rnfl_thickness * pit_attenuation + _noise_curve(rng) * 0.15
    t_gcl  = anatomy.gcl_thickness  * pit_attenuation + _noise_curve(rng) * 0.12
    t_ipl  = anatomy.ipl_thickness  * pit_attenuation + _noise_curve(rng) * 0.12
    t_inl  = anatomy.inl_thickness  * pit_attenuation + _noise_curve(rng) * 0.10
    t_opl  = anatomy.opl_thickness  * pit_attenuation + _noise_curve(rng) * 0.10
    # ONL: thickens at fovea (contains the photoreceptors, which are dense at fovea)
    onl_center_factor = 1.0 + 0.3 * np.exp(-0.5 * ((x - cx) / pit_sigma_px) ** 2)
    t_onl  = anatomy.onl_thickness  * onl_center_factor + _noise_curve(rng) * 0.20
    # EZ and RPE are relatively constant
    t_ez   = anatomy.ez_thickness  + _noise_curve(rng) * 0.08
    t_rpe  = anatomy.rpe_thickness + _noise_curve(rng) * 0.06
    # Choroid: very mild lateral variation
    t_ch   = anatomy.choroid_thickness + _noise_curve(rng) * 0.50

    # -- Build boundaries top-down (inner → outer) ---------------------------
    # All thicknesses clipped to minimum 1 px so boundaries never cross.
    rnfl_top = ilm                                      # ILM = top of RNFL
    gcl_top  = rnfl_top + np.maximum(t_rnfl, 1.0)
    ipl_top  = gcl_top  + np.maximum(t_gcl,  1.0)
    inl_top  = ipl_top  + np.maximum(t_ipl,  1.0)
    opl_top  = inl_top  + np.maximum(t_inl,  1.0)
    onl_top  = opl_top  + np.maximum(t_opl,  1.0)
    ez_top   = onl_top  + np.maximum(t_onl,  1.0)
    rpe_top  = ez_top   + np.maximum(t_ez,   1.0)
    bm       = rpe_top  + np.maximum(t_rpe,  1.0)       # Bruch's membrane = RPE bot
    csi      = bm       + np.maximum(t_ch,   1.0)       # choroid-sclera interface

    curves = {
        "ILM":     ilm,
        "RNFL_bot": gcl_top,
        "GCL_bot":  ipl_top,
        "IPL_bot":  inl_top,
        "INL_bot":  opl_top,
        "OPL_bot":  onl_top,
        "ONL_bot":  ez_top,
        "EZ_bot":   rpe_top,
        "RPE_bot":  bm,
        "BM":       bm,
        "CSI":      csi,
    }

    return curves


# ==============================================================================
# Step 2+3 — Reflectivity map and attenuation coefficient map
# ==============================================================================

def build_reflectivity_map(
    width: int,
    height: int,
    anatomy,
    curves: Dict[str, np.ndarray],
    rng: np.random.Generator,
    realism=None,
    acquisition=None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Build per-pixel linear reflectivity R(z,x) and attenuation μ(z,x).

    Returns:
        R   : float64 array shape (H, W) — linear reflectivity
        mu  : float64 array shape (H, W) — per-pixel attenuation coefficient (px^-1)

    Physical motivation: [PHYSICS] each tissue has a characteristic backscatter
    coefficient; [PROCEDURAL] bright interface lines (ILM, ELM, EZ, BM) are added
    as Gaussian peaks following the reference scripts' technique.
    """
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]   # (H, 1)

    # Shortcuts to boundary arrays (shape W,) — broadcast with Z via [:, None]
    ilm     = curves["ILM"][np.newaxis, :]
    rnfl_b  = curves["RNFL_bot"][np.newaxis, :]
    gcl_b   = curves["GCL_bot"][np.newaxis, :]
    ipl_b   = curves["IPL_bot"][np.newaxis, :]
    inl_b   = curves["INL_bot"][np.newaxis, :]
    opl_b   = curves["OPL_bot"][np.newaxis, :]
    onl_b   = curves["ONL_bot"][np.newaxis, :]
    ez_b    = curves["EZ_bot"][np.newaxis, :]
    rpe_b   = curves["RPE_bot"][np.newaxis, :]
    bm      = curves["BM"][np.newaxis, :]
    csi     = curves["CSI"][np.newaxis, :]

    # User reflectivity scale factors
    sc = {
        "RNFL": anatomy.rnfl_reflectivity,
        "GCL":  anatomy.gcl_reflectivity,
        "IPL":  anatomy.ipl_reflectivity,
        "INL":  anatomy.inl_reflectivity,
        "OPL":  anatomy.opl_reflectivity,
        "ONL":  anatomy.onl_reflectivity,
        "EZ":   anatomy.ez_reflectivity,
        "RPE":  anatomy.rpe_reflectivity,
        "CH":   anatomy.choroid_reflectivity,
    }

    # Smooth step function helpers [PROCEDURAL]
    def _st(edge, w=2.0):
        """Sigmoid step: 0 above edge, 1 below (w = pixel half-width)."""
        return 0.5 * (1.0 + np.tanh((Z - edge) / w))

    def _band(top_b, bot_b, w=2.0):
        """Indicator for the band [top_b, bot_b]."""
        return _st(top_b, w) - _st(bot_b, w)

    def _line(center, amp, sigma):
        """Gaussian bright interface line. [PROCEDURAL]"""
        return amp * np.exp(-0.5 * ((Z - center) / sigma) ** 2)

    # -- Initialise with vitreous background ----------------------------------
    R  = np.full((height, width), _BASE_REFL["VITREOUS"] * anatomy.vitreous_reflectivity)
    mu = np.zeros((height, width))

    # -- Layer bands (background scattering) ----------------------------------
    layer_specs = [
        ("RNFL", ilm,    rnfl_b, sc["RNFL"]),
        ("GCL",  rnfl_b, gcl_b,  sc["GCL"]),
        ("IPL",  gcl_b,  ipl_b,  sc["IPL"]),
        ("INL",  ipl_b,  inl_b,  sc["INL"]),
        ("OPL",  inl_b,  opl_b,  sc["OPL"]),
        ("ONL",  opl_b,  onl_b,  sc["ONL"]),
        ("EZ",   onl_b,  ez_b,   sc["EZ"]),
        ("RPE",  ez_b,   rpe_b,  sc["RPE"]),
    ]

    for name, top, bot, scale in layer_specs:
        base_r = _BASE_REFL[name]
        base_a = _BASE_ATTEN[name]
        mask = _band(top, bot)
        R  += mask * base_r * scale
        mu += mask * base_a

    # -- Bright interface lines (ILM, ELM, EZ/IS-OS, interdigitation, BM) ----
    # [PROCEDURAL] matching fig3.py line() additions
    tPR = ez_b - onl_b                               # photoreceptor band thickness
    R += _line(ilm,            0.55 * sc["RNFL"], 2.5)   # ILM specular
    R += _line(onl_b,          0.42 * sc["EZ"],   2.2)   # ELM
    R += _line(onl_b + 0.40 * tPR, 0.78 * sc["EZ"],  3.0)  # IS/OS (EZ)
    R += _line(onl_b + 0.86 * tPR, 0.48 * sc["EZ"],  3.5)  # interdigitation zone
    R += _line(ez_b + 4,       1.00 * sc["RPE"],  4.5)   # RPE top bright line
    R += _line(bm + 2,         0.50 * sc["RPE"],  2.5)   # Bruch's membrane

    # -- Choroid: stromal signal decaying with depth + dark vessel lumina -----
    # Built in build_choroid() and merged here; placeholder zeros for now.
    # (choroid is added after this function in render_scene)

    # -- Sclera ---------------------------------------------------------------
    dd_sclera = Z - csi
    in_sclera = dd_sclera >= 0
    R  += np.where(in_sclera,
                   _BASE_REFL["SCLERA"] * (np.exp(-dd_sclera / 320.0) +
                                            np.exp(-0.5 * (dd_sclera / 6.0) ** 2)),
                   0.0)
    mu += np.where(in_sclera, _BASE_ATTEN["SCLERA"], 0.0)

    # -- Low-frequency reflectivity variability / intensity nonuniformity [PROCEDURAL] -
    # Mimics tissue heterogeneity: broad correlated reflectivity fluctuations.
    nonunif_amp = float(getattr(realism, "intensity_nonuniformity", 0.16)) if realism is not None else 0.16
    lf = gaussian_filter(rng.standard_normal((height, width)), (height * 0.07, width * 0.10))
    lf /= (lf.std() + 1e-9)
    R = R * np.exp(nonunif_amp * lf)

    # -- PSF blur (axial + lateral) [PROCEDURAL] -----------------------------
    axial_blur = float(getattr(realism, "axial_blur", 2.1)) if realism is not None else 2.1
    lateral_blur = float(getattr(realism, "lateral_blur", 0.9)) if realism is not None else 0.9
    if acquisition is not None:
        axial_blur *= float(getattr(acquisition, "axial_resolution", 4.0)) / 4.0
        lateral_blur *= float(getattr(acquisition, "lateral_resolution", 15.0)) / 15.0

    sigma_z = max(0.1, axial_blur)
    sigma_x = max(0.1, lateral_blur)
    R  = gaussian_filter(R,  (sigma_z, sigma_x))
    mu = gaussian_filter(mu, (sigma_z * 1.4, sigma_x * 2.2))

    return R, mu


# ==============================================================================
# Step 4 — Beer-Lambert cumulative attenuation
# ==============================================================================

def apply_beer_lambert(
    R: np.ndarray,
    mu: np.ndarray,
) -> np.ndarray:
    """Apply two-way Beer-Lambert depth attenuation to reflectivity map.

    [PHYSICS] In OCT the detected power at depth z is:
        I(z) = R(z) * exp(-2 * ∫₀ᶻ μ(z') dz')
    The factor of 2 accounts for the round-trip (light travels to depth z and
    back). mu is already the one-way attenuation coefficient.

    Args:
        R  : (H, W) linear reflectivity map
        mu : (H, W) per-pixel one-way attenuation coefficient (px^-1)

    Returns:
        mean_I : (H, W) attenuated reflectivity (mean OCT power, pre-speckle)
    """
    # Cumulative sum along axis 0 (depth), shifted by half-step to get the
    # integral *entering* each voxel (exclude the current voxel's own attenuation
    # from the propagation integral, matching the reference scripts' convention).
    cum_mu = np.cumsum(mu, axis=0) - mu        # integral [0, z) exclusive
    atten = np.exp(-2.0 * cum_mu)              # [PHYSICS] two-way factor
    return R * atten


# ==============================================================================
# Step 5 — Choroid: stromal background + dark vessel lumina
# ==============================================================================

def build_choroid(
    width: int,
    height: int,
    curves: Dict[str, np.ndarray],
    anatomy,
    rng: np.random.Generator,
) -> Tuple[np.ndarray, np.ndarray]:
    """Build choroidal reflectivity and attenuation contribution.

    Models:
    - Choriocapillaris: thin bright band just below RPE [PROCEDURAL]
    - Stroma: exponentially decaying reflectivity with depth [PHYSICS inspired]
    - Large vessel lumina: dark rounded ellipses [PROCEDURAL]

    Returns:
        R_ch  : (H, W) choroidal reflectivity contribution (to be added to R)
        mu_ch : (H, W) choroidal attenuation (to be added to mu)

    [PHYSICS] choroidal stroma backscatter decays approximately exponentially
    with depth due to cumulative absorption/scattering.
    """
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]
    x = np.arange(width,  dtype=np.float64)[np.newaxis, :]

    bm_arr  = curves["BM"][np.newaxis, :]
    csi_arr = curves["CSI"][np.newaxis, :]
    Tc = csi_arr - bm_arr  # choroid thickness per column

    d = Z - bm_arr          # depth below BM
    in_ch = (d >= 0) & (Z < csi_arr)

    # Choriocapillaris: thin bright band at top of choroid [PROCEDURAL]
    cc = 0.12 * np.exp(-0.5 * (d / 5.0) ** 2)

    # Stroma: double-exponential decay (fast + slow component) [PROCEDURAL]
    stroma = (0.105 * np.exp(-d / (0.85 * Tc + 1e-6)) +
              0.12  * np.exp(-d / 9.0))
    stroma = stroma * anatomy.choroid_reflectivity / 0.40   # user scale factor

    # Large-vessel lumina: dark ellipses [PROCEDURAL]
    # Use the render_seed to place vessels deterministically.
    n_vessels = 7
    lumen = np.ones((height, width))
    for _ in range(n_vessels):
        cx_v = rng.uniform(0.05, 0.95) * width
        cz_frac = rng.uniform(0.30, 0.80)
        rx_v = rng.uniform(0.08, 0.17) * width  # lateral radius
        rz_v = rng.uniform(0.07, 0.11) * (anatomy.choroid_thickness + 1e-6)
        z_ctr_v = bm_arr + cz_frac * Tc
        dd = ((x - cx_v) / (rx_v + 1e-6)) ** 2 + ((Z - z_ctr_v) / (rz_v + 1e-6)) ** 2
        lumen *= 1.0 - 0.82 * np.exp(-dd ** 2)

    R_ch  = np.where(in_ch, (stroma + cc) * lumen, 0.0)
    mu_ch = np.where(in_ch, _BASE_ATTEN["CHOROID"], 0.0)

    return R_ch, mu_ch


# ==============================================================================
# Step 6 — Retinal vessels: cross-section + Beer-Lambert shadow
# ==============================================================================

def build_vessels(
    width: int,
    height: int,
    curves: Dict[str, np.ndarray],
    anatomy,
    rng: np.random.Generator,
    preview: bool = False,
) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
    """Build inner-retinal vessel cross-sections and their Beer-Lambert shadows.

    Vessel count and positions are derived deterministically from anatomy.render_seed.

    Returns:
        R_v    : (H, W) vessel reflectivity addition
        tau_v  : (H, W) additional optical depth due to vessel shadowing
        vinfo  : list of dicts with vessel metadata

    [PHYSICS] Beer-Lambert shadow: the vessel absorbs/scatters light; the extra
    optical depth integrated through the chord reduces signal below it.
    Implementation follows fig7.py's panel-B shadow integral approach.
    """
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]
    x = np.arange(width,  dtype=np.float64)[np.newaxis, :]

    ilm_arr = curves["ILM"][np.newaxis, :]
    rnfl_b  = curves["RNFL_bot"][np.newaxis, :]

    R_v   = np.zeros((height, width))
    tau_v = np.zeros((height, width))
    vinfo: List[Dict[str, Any]] = []

    # Fixed layout: 2 vessels visible in the typical central 6mm scan
    # Positions chosen to be somewhat nasal/temporal of the fovea
    fovea_px = anatomy.fovea_center
    vessel_params = [
        # (lateral offset fraction from fovea, zeta within RNFL, rx_frac, rz_px, tau)
        ( 0.20, 0.45, 0.022, 14.0, 2.4),   # nasal vessel
        (-0.22, 0.55, 0.018, 10.0, 1.9),   # temporal vessel
    ]

    for (xoff_frac, zeta, rx_frac, rz_px, tau_total) in vessel_params:
        xv = fovea_px + xoff_frac * width
        xv = np.clip(xv, width * 0.08, width * 0.92)
        rx = rx_frac * width

        # Vessel center depth: within the RNFL/GCL [PROCEDURAL]
        col = int(np.clip(xv, 0, width - 1))
        z_ilm_ctr = float(ilm_arr[0, col])
        z_rnfl_bot = float(rnfl_b[0, col])
        zv = z_ilm_ctr + zeta * (z_rnfl_bot - z_ilm_ctr)

        # -- Vessel cross-section: lumen (dark core) + bright wall + bright top --
        # [PROCEDURAL] matching fig7.py vessel model
        dxn = (x - xv) / (rx + 1e-6)
        dzn = (Z - zv) / (rz_px + 1e-6)
        dist = np.sqrt(dxn ** 2 + dzn ** 2)

        lumen_mask = 1.0 / (1.0 + np.exp(np.clip((dist - 1.0) / 0.07, -500, 500)))   # interior
        wall       = np.exp(-((dist - 0.88) / 0.17) ** 2)         # bright rim
        top_glint  = (np.exp(-(dxn / 0.8) ** 2) *
                      np.exp(-((dzn + 0.72) / 0.28) ** 2))        # top specular

        Rv = lumen_mask * 0.34 + wall * lumen_mask * 0.75 + top_glint * 0.95
        R_v = np.maximum(R_v, Rv)

        # -- Beer-Lambert shadow [PHYSICS] ------------------------------------
        # Half-chord length per column through the vessel ellipsoid:
        h_chord = rz_px * np.sqrt(np.clip(1.0 - dxn[0] ** 2, 0.0, 1.0))
        s0   = zv - 0.30 * h_chord[np.newaxis, :]
        span = 1.30 * h_chord[np.newaxis, :] + 1e-6
        a_v  = tau_total / (1.30 * rz_px)
        tau  = a_v * np.clip(Z - s0, 0.0, span)

        # Forward-scattering: shadow broadens laterally below the vessel [PROCEDURAL]
        below    = np.clip(Z - (zv + rz_px), 0.0, None)
        recovery = 1.0 - 0.38 * (1.0 - np.exp(-below / 330.0))
        tau      = tau * np.where(Z > zv + rz_px, recovery, 1.0)

        # Gaussian lateral blur of the shadow that grows with depth [PROCEDURAL]
        if preview:
            tau = gaussian_filter1d(tau, 5.0, axis=1, mode="nearest")
        else:
            sig_row   = 4.0 + 0.060 * below[:, 0]
            buckets   = np.round(sig_row / 1.5).astype(int)
            for b in np.unique(buckets):
                rows = np.where(buckets == b)[0]
                tau[rows] = gaussian_filter1d(tau[rows], max(b * 1.5, 0.5), axis=1, mode="nearest")

        tau_v = tau_v + tau

        vinfo.append(dict(
            x_center=float(xv),
            radius_x=float(rx),
            radius_z=float(rz_px),
            z_center=float(zv),
            shadow_tau=float(tau_total),
            top_z=float(zv - rz_px),
            bot_z=float(zv + rz_px),
        ))

    return R_v, tau_v, vinfo


# ==============================================================================
# Step 7 — Coherent speckle
# ==============================================================================

def build_speckle(
    shape: Tuple[int, int],
    n_avg: int,
    rng: np.random.Generator,
    sz: float = 1.8,
    sx: float = 1.3,
    speckle_amount: float = 1.0,
) -> np.ndarray:
    """Generate PSF-correlated coherent speckle field, averaged over n_avg realizations.

    [PHYSICS] OCT speckle arises from the coherent superposition of backscattered
    fields from many sub-resolution scatterers within a PSF volume.  For a Gaussian
    PSF and random scatterer phases, the intensity at each pixel follows an
    exponential distribution (fully developed Rayleigh / circular Gaussian speckle).
    Averaging N independent frames reduces speckle contrast by 1/√N.

    Implementation: sum of |complex Gaussian field|² (real² + imag²), where each
    field is Gaussian-filtered white noise to match the PSF correlation length.
    Each realization is normalized to unit mean before averaging so the final
    mean value ≈ 1.0 regardless of n_avg.

    Args:
        shape          : (H, W)
        n_avg          : number of independent realizations to average
        rng            : seeded RNG
        sz             : PSF axial sigma (pixels)  [PROCEDURAL]
        sx             : PSF lateral sigma (pixels) [PROCEDURAL]
        speckle_amount : speckle contrast scaling (0 = noiseless, 1 = full Rayleigh)

    Returns:
        sp : (H, W) float64 speckle field, mean ≈ 1.0
    """
    acc = np.zeros(shape, dtype=np.float64)
    for _ in range(n_avg):
        re = gaussian_filter(rng.standard_normal(shape), (sz, sx))
        im = gaussian_filter(rng.standard_normal(shape), (sz, sx))
        s  = re ** 2 + im ** 2
        acc += s / (s.mean() + 1e-12)
    sp = acc / n_avg
    if speckle_amount != 1.0:
        sp = (1.0 - speckle_amount) + speckle_amount * sp
        sp = np.maximum(sp, 0.0)
    return sp


# ==============================================================================
# Step 8 — Noise floor
# ==============================================================================

def add_noise_floor(
    I: np.ndarray,
    rng: np.random.Generator,
    floor_db: float = -31.5,
    signal_scale: float = 1.0,
    n_avg: int = 6,
) -> np.ndarray:
    """Add additive receiver noise floor so dark regions are non-zero.

    [PHYSICS] Real OCT detectors have a finite noise floor (detector shot noise,
    receiver thermal noise). This prevents log-zero artifacts and gives the
    vitreous its characteristic low-level grainy appearance.

    floor_db     : noise floor power level in dB (relative to unit reflectivity).
    signal_scale : scalar multiplier for optical signal before adding noise.
    n_avg        : speckle realization count for noise grain.
    """
    floor_lin = 10.0 ** (floor_db / 10.0)
    # Spatially correlated noise matches the speckle-like grain of a real noise floor
    noise_speckle = build_speckle(I.shape, n_avg=max(1, n_avg), rng=rng, sz=1.8, sx=1.3)
    return I * signal_scale + floor_lin * noise_speckle


# ==============================================================================
# Step 9 — Display mapping
# ==============================================================================

def to_gray(
    I: np.ndarray,
    db_lo: float = -35.0,
    db_hi: float = -2.0,
    gamma: float = 1.55,
    contrast: float = 1.0,
    blur: float = 0.55,
) -> np.ndarray:
    """Convert linear OCT intensity to display grayscale [0, 1].

    [VISUAL] Display mapping is not a physical step but determines perceived
    contrast.  The log-compression + window + gamma matches the clinical display
    pipeline described in Aumann et al. and used in the reference scripts.

    Steps:
        1. Convert to dB: db = 10 * log10(I)  [PHYSICS — decibel convention]
        2. Clip to window [db_lo, db_hi]       [VISUAL — display exposure]
        3. Normalise to [0, 1]
        4. Apply contrast and gamma            [VISUAL — contrast/brightness]
        5. Mild final blur to anti-alias        [VISUAL]

    Args:
        I        : (H, W) linear intensity (non-negative)
        db_lo    : black-point in dB
        db_hi    : white-point in dB
        gamma    : display gamma (>1 darkens midtones)
        contrast : dynamic range contrast stretch factor
        blur     : post-processing blur sigma (pixels)

    Returns:
        gray : (H, W) float64 in [0, 1]
    """
    db   = 10.0 * np.log10(np.maximum(I, 1e-12))
    norm = np.clip((db - db_lo) / (db_hi - db_lo), 0.0, 1.0)
    if contrast != 1.0:
        norm = np.clip(0.5 + contrast * (norm - 0.5), 0.0, 1.0)
    gray = norm ** gamma
    if blur > 0.0:
        gray = gaussian_filter(gray, blur)   # [VISUAL] mild anti-alias
    return gray


# ==============================================================================
# Step 10 — Layer-ID map
# ==============================================================================

def build_layer_id_map(
    width: int,
    height: int,
    curves: Dict[str, np.ndarray],
    vessel_info: List[Dict[str, Any]],
) -> np.ndarray:
    """Build a per-pixel integer layer-ID map.

    Returns:
        layer_map : int8 (H, W), values from LAYER_IDS dict.

    This is the ground-truth geometry for segmentation and measurement.
    """
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]
    x = np.arange(width,  dtype=np.float64)[np.newaxis, :]

    ilm_arr = curves["ILM"][np.newaxis, :]
    bnd = {
        "ILM":     ilm_arr,
        "RNFL_bot": curves["RNFL_bot"][np.newaxis, :],
        "GCL_bot":  curves["GCL_bot"][np.newaxis, :],
        "IPL_bot":  curves["IPL_bot"][np.newaxis, :],
        "INL_bot":  curves["INL_bot"][np.newaxis, :],
        "OPL_bot":  curves["OPL_bot"][np.newaxis, :],
        "ONL_bot":  curves["ONL_bot"][np.newaxis, :],
        "EZ_bot":   curves["EZ_bot"][np.newaxis, :],
        "RPE_bot":  curves["RPE_bot"][np.newaxis, :],
        "BM":       curves["BM"][np.newaxis, :],
        "CSI":      curves["CSI"][np.newaxis, :],
    }

    layer_map = np.full((height, width), LAYER_IDS["VITREOUS"], dtype=np.int8)

    # Build from bottom up so top-priority layers overwrite lower ones
    layer_map = np.where(Z >= bnd["CSI"],      LAYER_IDS["SCLERA"],   layer_map)
    layer_map = np.where((Z >= bnd["BM"])  & (Z < bnd["CSI"]),  LAYER_IDS["CHOROID"], layer_map)
    layer_map = np.where((Z >= bnd["EZ_bot"])  & (Z < bnd["BM"]),    LAYER_IDS["RPE"],     layer_map)
    layer_map = np.where((Z >= bnd["ONL_bot"]) & (Z < bnd["EZ_bot"]), LAYER_IDS["EZ"],     layer_map)
    layer_map = np.where((Z >= bnd["OPL_bot"]) & (Z < bnd["ONL_bot"]), LAYER_IDS["ONL"],   layer_map)
    layer_map = np.where((Z >= bnd["INL_bot"]) & (Z < bnd["OPL_bot"]), LAYER_IDS["OPL"],   layer_map)
    layer_map = np.where((Z >= bnd["IPL_bot"]) & (Z < bnd["INL_bot"]), LAYER_IDS["INL"],   layer_map)
    layer_map = np.where((Z >= bnd["GCL_bot"]) & (Z < bnd["IPL_bot"]), LAYER_IDS["IPL"],   layer_map)
    layer_map = np.where((Z >= bnd["RNFL_bot"]) & (Z < bnd["GCL_bot"]), LAYER_IDS["GCL"],  layer_map)
    layer_map = np.where((Z >= bnd["ILM"])      & (Z < bnd["RNFL_bot"]), LAYER_IDS["RNFL"], layer_map)

    # Vessel pixels (overwrite inner-retina label)
    for v in vessel_info:
        xv, rx = v["x_center"], v["radius_x"]
        zv, rz = v["z_center"], v["radius_z"]
        dxn = (x - xv) / (rx + 1e-6)
        dzn = (Z - zv) / (rz + 1e-6)
        inside = (dxn ** 2 + dzn ** 2) <= 1.0
        layer_map = np.where(inside, LAYER_IDS["VESSEL"], layer_map)

    return layer_map.astype(np.int8)


# ==============================================================================
# Orchestrator: render_scene()
# ==============================================================================

RenderResult = Tuple[np.ndarray, np.ndarray, Dict[str, np.ndarray], List[Dict[str, Any]]]


def render_scene(
    scene: SceneConfig,
    preview: bool = False,
) -> RenderResult:
    """Full deterministic OCT forward model.

    Args:
        scene   : SceneConfig — all parameters.
        preview : If True, use reduced quality (fewer speckle averages) for speed.

    Returns:
        pixels          : np.ndarray uint8 shape (H, W)
        layer_id_map    : np.ndarray int8  shape (H, W)
        boundary_curves : dict[str → np.ndarray(W,)]
        vessel_info     : list[dict]
    """
    seed      = scene.anatomy.render_seed
    rng       = np.random.default_rng(seed)

    width     = scene.acquisition.width
    height    = scene.acquisition.height
    anatomy   = scene.anatomy
    realism   = scene.realism
    acquisition = scene.acquisition

    # Quality: preview uses fewer speckle averages for speed
    speckle_count = realism.speckle_avg_count
    n_avg = max(1, speckle_count // 3) if preview else speckle_count

    # 1. Boundary geometry (with boundary uncertainty jitter if configured)
    curves = build_boundaries(width, height, anatomy, rng, realism=realism)

    # 2+3. Reflectivity map + attenuation (with PSF sizing & nonuniformity)
    R, mu = build_reflectivity_map(
        width, height, anatomy, curves, rng, realism=realism, acquisition=acquisition
    )

    # 5. Choroid contribution
    R_ch, mu_ch = build_choroid(width, height, curves, anatomy, rng)
    R  = R  + R_ch
    mu = mu + mu_ch

    # 6. Vessels: reflectivity addition + shadow optical depth
    R_v, tau_v, vessel_info = build_vessels(width, height, curves, anatomy, rng, preview=preview)
    R = np.maximum(R, R_v)  # vessel bright cross-section replaces tissue

    # --- PATHOLOGY DEFORMATION PASS ---
    # Applied after anatomy construction, before Beer-Lambert propagation.
    # Pathology modifies (curves, R, mu) in-place via deformation functions.
    # Order: RPE-level → fluid-space → holes → disruption → interface
    # (see pathology.py APPLICATION_ORDER for full sequence).
    if scene.pathology.enabled and scene.pathology.instances:
        curves, R, mu = apply_all_pathologies(scene, curves, R, mu)

    # 4. Beer-Lambert propagation (includes vessel shadow via tau_v)
    # mu is cumulated in apply_beer_lambert; tau_v is added to the cumulative sum
    # after propagation, matching fig7.py's approach (tau_v already encodes
    # the shadow as additional optical depth beneath the vessel).
    cum_mu  = np.cumsum(mu, axis=0) - mu   # integral [0, z) exclusive
    atten   = np.exp(-2.0 * (cum_mu + tau_v))  # [PHYSICS] two-way + shadow
    I_mean  = R * atten

    # Roll-off: slow global falloff matching scan sensitivity envelope [PROCEDURAL]
    z_idx  = np.arange(height, dtype=np.float64)[:, np.newaxis]
    rolloff = np.exp(-z_idx / (height * 5.0))
    I_mean *= rolloff

    # 7. Coherent speckle
    speckle_amt = float(getattr(realism, "speckle_amount", 1.0))
    sp = build_speckle((height, width), n_avg=n_avg, rng=rng, speckle_amount=speckle_amt)
    I  = I_mean * sp

    # 8. Noise floor & signal strength
    sig_scale = max(0.1, float(getattr(acquisition, "signal_strength", 8.0)) / 8.0)
    bg_noise = float(getattr(realism, "background_noise", -31.5))
    noise_navg = 1 if preview else 6
    I = add_noise_floor(I, rng, floor_db=bg_noise, signal_scale=sig_scale, n_avg=noise_navg)

    # 9. Display mapping
    contrast_val = float(getattr(realism, "contrast", 1.0))
    blur_val = float(getattr(realism, "blur", 0.55))
    gray = to_gray(
        I,
        db_lo=realism.db_lo,
        db_hi=realism.db_hi,
        gamma=realism.display_gamma,
        contrast=contrast_val,
        blur=blur_val,
    )

    # Convert to uint8
    pixels = np.clip(gray * 255.0 + 0.5, 0, 255).astype(np.uint8)

    # Layer ID map
    layer_id_map = build_layer_id_map(width, height, curves, vessel_info)

    # --- OPTICAL AND MOTION ARTIFACTS ---
    # Applied as composable post-processing passes on the finished array + layer ID map.
    if scene.artifacts.enabled and scene.artifacts.instances:
        pixels, layer_id_map = apply_all_artifacts(scene, pixels, layer_id_map)

    return pixels, layer_id_map, curves, vessel_info


# ==============================================================================
# Public API functions (used by api.py)
# ==============================================================================

def generate_bscan_array(scene: SceneConfig) -> np.ndarray:
    """Generate an OCT B-scan uint8 numpy array.

    Backward-compatible entry point used by existing tests and cache logic.

    Args:
        scene: SceneConfig

    Returns:
        np.ndarray: 2D uint8 numpy array (height x width)
    """
    pixels, _, _, _ = render_scene(scene, preview=False)
    return pixels


def render_bscan_base64(scene: SceneConfig, preview: bool = True) -> str:
    """Render SceneConfig into a base64 encoded PNG data URI.

    Args:
        scene  : SceneConfig instance
        preview: If True, use reduced-quality fast render

    Returns:
        str: Data URI string (data:image/png;base64,...)
    """
    pixels, _, _, _ = render_scene(scene, preview=preview)
    pil_img = Image.fromarray(pixels, mode="L")
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    raw_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{raw_b64}"


def render_full_result(scene: SceneConfig, preview: bool = False) -> Dict[str, Any]:
    """Full render returning image, layer map, boundaries, and vessel info.

    Used by /api/render/final.

    Returns dict with:
        image          : base64 PNG data URI
        layer_id_map   : list[list[int]]   (H x W, JSON-serialisable)
        boundary_curves: dict[str → list[float]]
        vessel_info    : list[dict]
        width          : int
        height         : int
    """
    pixels, layer_id_map, curves, vessel_info = render_scene(scene, preview=preview)

    pil_img = Image.fromarray(pixels, mode="L")
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    raw_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return {
        "image": f"data:image/png;base64,{raw_b64}",
        "layer_id_map": layer_id_map.tolist(),
        "boundary_curves": {k: v.tolist() for k, v in curves.items()},
        "vessel_info": vessel_info,
        "width": scene.acquisition.width,
        "height": scene.acquisition.height,
    }
