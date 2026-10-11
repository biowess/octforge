"""OCTForge Pathology Rendering Engine.

Applies pathological deformations to the normal-anatomy forward model output.
Each pathology family is implemented as a deformation pass that modifies the
same (boundary_curves, R, mu) structures produced by forward_model.py.

APPLICATION ORDER (fixed, deterministic):
  1. RPE-level pathologies (drusen, RPE irregularity, RPE attenuation, geographic atrophy)
  2. Fluid-space pathologies (PED, SRF, intraretinal cysts, CME)
  3. Hole pathologies (macular hole, lamellar macular hole)
  4. Disruption pathologies (outer retinal disruption, retinal thinning, hyperreflective foci)
  5. Interface pathologies (ERM, VMT)

Within each group, instances are applied in list order.

Determinism: Each PathologyInstance derives its RNG from
  numpy.random.default_rng(render_seed + seed_offset)
so instances are independently reproducible regardless of list position.

Public API:
  apply_all_pathologies(scene, curves, R, mu) → (curves, R, mu)
"""

from typing import Dict, List, Tuple, Any
import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d

from backend.app.schema import SceneConfig, PathologyInstance


# ==============================================================================
# Application order — pathology types grouped by family.
# Within each group, instances from the user list are applied in order.
# ==============================================================================

APPLICATION_ORDER = [
    # Group 1: RPE-level (modify RPE/BM geometry and reflectivity first)
    "drusen",
    "rpe_irregularity",
    "rpe_attenuation",
    "geographic_atrophy",
    # Group 2: Fluid-space (push/lift boundaries, punch dark regions)
    "pigment_epithelial_detachment",
    "subretinal_fluid",
    "intraretinal_cyst",
    "cystoid_macular_edema",
    # Group 3: Holes (remove layer continuity)
    "macular_hole",
    "lamellar_macular_hole",
    # Group 4: Disruption (EZ loss, thinning, hyperreflective foci)
    "outer_retinal_disruption",
    "retinal_thinning",
    "hyperreflective_foci",
    # Group 5: Interface (ERM, VMT — added on top)
    "epiretinal_membrane",
    "vitreomacular_traction",
]

# Dispatch table — populated at module bottom after function definitions
_DISPATCH: Dict[str, Any] = {}


# ==============================================================================
# Helpers
# ==============================================================================

def _instance_rng(render_seed: int, inst: PathologyInstance) -> np.random.Generator:
    """Create a deterministic RNG stream for one pathology instance."""
    return np.random.default_rng(render_seed + inst.seed_offset)


def _gaussian_bump(x: np.ndarray, center: float, sigma: float) -> np.ndarray:
    """Normalized Gaussian bump centered at `center` with width `sigma`."""
    return np.exp(-0.5 * ((x - center) / (sigma + 1e-9)) ** 2)


def _skewed_gaussian(x: np.ndarray, center: float, sigma: float,
                     asymmetry: float) -> np.ndarray:
    """Asymmetric Gaussian: wider on one side when asymmetry != 0."""
    dx = x - center
    # asymmetry in [-1, 1]: positive = wider to right, negative = wider to left
    sigma_left = sigma * (1.0 - 0.4 * asymmetry)
    sigma_right = sigma * (1.0 + 0.4 * asymmetry)
    s = np.where(dx < 0, sigma_left, sigma_right)
    return np.exp(-0.5 * (dx / (s + 1e-9)) ** 2)


def _lateral_positions(rng: np.random.Generator, count: int, width: int,
                       location: float, distribution: float) -> np.ndarray:
    """Generate `count` lateral positions from a Gaussian distribution."""
    center_px = location * width
    sigma_px = distribution * width
    positions = rng.normal(center_px, sigma_px, count)
    return np.clip(positions, 5, width - 5)


def _boundary_between(curves: Dict[str, np.ndarray],
                      top_key: str, bot_key: str) -> Tuple[np.ndarray, np.ndarray]:
    """Return (top_curve, bot_curve) arrays for a layer bounded by two keys."""
    return curves[top_key], curves[bot_key]


def _push_boundaries_down(curves: Dict[str, np.ndarray], delta: np.ndarray,
                          start_key: str, keys_to_push: List[str]) -> None:
    """Push multiple boundary curves downward by `delta` pixels (in-place).

    Only pushes where delta > 0. Maintains boundary ordering by also pushing
    any boundary that would be crossed.
    """
    for key in keys_to_push:
        if key in curves:
            curves[key] = curves[key] + np.maximum(delta, 0)


# ==============================================================================
# Group 1: RPE-Level Pathologies
# ==============================================================================

def apply_drusen(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """Drusen: focal dome-shaped elevations of the RPE above Bruch's membrane.

    Parameters used:
      severity  → height of drusen domes (px = severity * 40)
      size      → lateral diameter of each druse (fraction of width)
      count     → number of drusen
      location  → center of drusen cluster
      distribution → spread of drusen positions
      asymmetry → shape asymmetry of each druse
      reflectivity → internal reflectivity of drusen material (typically medium)
    """
    x = np.arange(width, dtype=np.float64)
    positions = _lateral_positions(rng, inst.count, width, inst.location, inst.distribution)

    max_height = inst.severity * 40.0  # 0-40 px elevation
    druse_sigma = inst.size * width * 0.5  # half-width in px

    total_elevation = np.zeros(width, dtype=np.float64)

    for cx in positions:
        # Per-druse random height variation
        h = max_height * rng.uniform(0.5, 1.0)
        sigma = druse_sigma * rng.uniform(0.6, 1.4)
        bump = _skewed_gaussian(x, cx, sigma, inst.asymmetry)
        total_elevation += h * bump

    # Drusen are sub-RPE deposits that elevate the RPE above Bruch's membrane.
    # BM stays fixed; RPE_bot and EZ_bot are pushed upward (lower z = towards vitreous).
    old_ez_bot = curves["EZ_bot"].copy()
    old_rpe_bot = curves["RPE_bot"].copy()
    bm = curves["BM"]

    # Available space between RPE_bot and BM for drusen height
    available = np.maximum(bm - old_rpe_bot, 1.0)
    # Drusen can also push RPE upward, creating space between RPE and BM
    # Clamp total elevation to avoid excessive deformation
    clamped_elev = np.minimum(total_elevation, available * 1.5 + 20.0)

    # Push RPE_bot and EZ_bot upward by the drusen elevation
    curves["RPE_bot"] = old_rpe_bot - clamped_elev
    curves["EZ_bot"] = old_ez_bot - clamped_elev * 0.8  # EZ lifts less than RPE

    # Ensure ordering: EZ_bot must stay below ONL_bot
    curves["EZ_bot"] = np.maximum(curves["EZ_bot"], curves["ONL_bot"] + 2)
    curves["RPE_bot"] = np.maximum(curves["RPE_bot"], curves["EZ_bot"] + 2)

    # Fill drusen material: medium reflectivity between new RPE_bot and BM
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]
    new_rpe_bot = curves["RPE_bot"][np.newaxis, :]
    bm_2d = bm[np.newaxis, :]
    drusen_region = (Z >= new_rpe_bot) & (Z < bm_2d)

    drusen_refl = 0.15 + inst.reflectivity * 0.35  # medium reflectivity material
    R = np.where(drusen_region, np.maximum(R, drusen_refl), R)
    mu_drusen = 0.008  # drusen have moderate attenuation
    mu = np.where(drusen_region, np.maximum(mu, mu_drusen), mu)

    return curves, R, mu


def apply_rpe_irregularity(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """RPE irregularity: undulating/bumpy RPE boundary.

    Parameters used:
      severity → amplitude of RPE undulations (px)
      size     → wavelength of undulations (fraction of width)
      location → center of irregularity region
      distribution → lateral extent of irregularity
      extent   → axial extent of RPE boundary perturbation
    """
    x = np.arange(width, dtype=np.float64)
    region = _skewed_gaussian(x, inst.location * width, inst.distribution * width, inst.asymmetry)

    # Generate multi-frequency RPE undulation
    amp = inst.severity * 12.0  # up to 12 px undulation
    wavelength_px = inst.size * width
    noise = rng.standard_normal(width)
    smoothed = gaussian_filter1d(noise, max(wavelength_px * 0.2, 2.0), mode="reflect")
    smoothed = smoothed / (smoothed.std() + 1e-9) * amp

    perturbation = smoothed * region

    # Save old boundary positions
    old_ez_bot = curves["EZ_bot"].copy()
    old_rpe_bot = curves["RPE_bot"].copy()

    # Apply to RPE boundaries
    curves["EZ_bot"] = curves["EZ_bot"] + perturbation * 0.6
    curves["RPE_bot"] = curves["RPE_bot"] + perturbation
    curves["BM"] = curves["BM"] + perturbation

    # Ensure ordering
    curves["RPE_bot"] = np.maximum(curves["RPE_bot"], curves["EZ_bot"] + 2)
    curves["BM"] = np.maximum(curves["BM"], curves["RPE_bot"])
    curves["CSI"] = np.maximum(curves["CSI"], curves["BM"] + 2)

    # Modify reflectivity to reflect moved boundaries:
    # Where RPE moved, redistribute bright RPE signal to new positions
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]
    old_ez_2d = old_ez_bot[np.newaxis, :]
    old_rpe_2d = old_rpe_bot[np.newaxis, :]
    new_ez_2d = curves["EZ_bot"][np.newaxis, :]
    new_rpe_2d = curves["RPE_bot"][np.newaxis, :]
    region_2d = region[np.newaxis, :]

    # Darken the old RPE position where it moved away
    was_rpe = (Z >= old_ez_2d) & (Z < old_rpe_2d)
    now_not_rpe = (Z < new_ez_2d) | (Z >= new_rpe_2d)
    displaced = was_rpe & now_not_rpe & (region_2d > 0.1)
    R = np.where(displaced, R * 0.15, R)  # reduce to near-background

    # Brighten the new RPE position where it moved to
    now_rpe = (Z >= new_ez_2d) & (Z < new_rpe_2d)
    was_not_rpe = (Z < old_ez_2d) | (Z >= old_rpe_2d)
    new_rpe_area = now_rpe & was_not_rpe & (region_2d > 0.1)
    R = np.where(new_rpe_area, np.maximum(R, 0.6), R)
    mu = np.where(new_rpe_area, np.maximum(mu, 0.02), mu)

    return curves, R, mu


def apply_rpe_attenuation(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """RPE attenuation: localized reduction in RPE reflectivity band.

    Parameters used:
      severity → fraction of RPE reflectivity lost (0=none, 1=complete loss)
      size     → lateral extent of attenuation zone
      location → center of attenuation
      distribution → spread/feathering of transition
    """
    x = np.arange(width, dtype=np.float64)
    envelope = _skewed_gaussian(x, inst.location * width,
                                inst.size * width * 0.5, inst.asymmetry)

    # Attenuate RPE reflectivity within the envelope
    loss_fraction = inst.severity
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]
    rpe_top = curves["EZ_bot"][np.newaxis, :]
    rpe_bot = curves["RPE_bot"][np.newaxis, :]
    in_rpe = (Z >= rpe_top) & (Z < rpe_bot)

    attenuation_2d = envelope[np.newaxis, :] * loss_fraction
    R = np.where(in_rpe, R * (1.0 - attenuation_2d), R)

    return curves, R, mu


def apply_geographic_atrophy(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """Geographic atrophy: loss of RPE and outer retina with choroidal signal enhancement.

    Parameters used:
      severity → completeness of RPE/outer retina loss
      size     → lateral extent of atrophy zone
      location → center of atrophy
      distribution → feathering of atrophy edge
      extent   → depth of outer retinal involvement
    """
    x = np.arange(width, dtype=np.float64)
    envelope = _skewed_gaussian(x, inst.location * width,
                                inst.size * width * 0.5, inst.asymmetry)

    loss = inst.severity
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]

    # Loss of RPE signal
    rpe_top = curves["EZ_bot"][np.newaxis, :]
    rpe_bot = curves["RPE_bot"][np.newaxis, :]
    in_rpe = (Z >= rpe_top) & (Z < rpe_bot)
    attenuation_2d = envelope[np.newaxis, :] * loss
    R = np.where(in_rpe, R * (1.0 - attenuation_2d * 0.85), R)
    mu = np.where(in_rpe, mu * (1.0 - attenuation_2d * 0.7), mu)

    # EZ band loss (outer retinal disruption accompanies atrophy)
    ez_involvement = inst.extent
    ez_top = curves["ONL_bot"][np.newaxis, :]
    in_ez = (Z >= ez_top) & (Z < rpe_top)
    ez_loss = envelope[np.newaxis, :] * loss * ez_involvement
    R = np.where(in_ez, R * (1.0 - ez_loss * 0.75), R)

    # Enhanced choroidal signal due to reduced RPE absorption
    bm = curves["BM"][np.newaxis, :]
    csi = curves["CSI"][np.newaxis, :]
    in_choroid = (Z >= bm) & (Z < csi)
    choroid_boost = envelope[np.newaxis, :] * loss * 0.4
    R = np.where(in_choroid, R * (1.0 + choroid_boost), R)

    return curves, R, mu


# ==============================================================================
# Group 2: Fluid-Space Pathologies
# ==============================================================================

def apply_pigment_epithelial_detachment(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """PED: RPE lifts off Bruch's membrane, creating a sub-RPE fluid/material space.

    Parameters used:
      severity → height of RPE elevation
      size     → lateral extent of PED
      location → center of PED
      asymmetry → shape asymmetry
      reflectivity → 0=serous (dark), 1=fibrovascular (bright)
      extent   → steepness of dome edges
    """
    x = np.arange(width, dtype=np.float64)
    center_px = inst.location * width
    sigma_px = inst.size * width * 0.5

    dome = _skewed_gaussian(x, center_px, sigma_px, inst.asymmetry)
    elevation = dome * inst.severity * 60.0  # up to 60 px lift

    # RPE and EZ lift upward (lower z), BM stays in place
    curves["EZ_bot"] = curves["EZ_bot"] - elevation
    curves["RPE_bot"] = curves["RPE_bot"] - elevation

    # Ensure EZ_bot doesn't cross ONL_bot
    curves["EZ_bot"] = np.maximum(curves["EZ_bot"], curves["ONL_bot"] + 2)
    curves["RPE_bot"] = np.maximum(curves["RPE_bot"], curves["EZ_bot"] + 2)

    # Fill the sub-RPE space with fluid or fibrovascular material
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]
    rpe_bot_new = curves["RPE_bot"][np.newaxis, :]
    bm_arr = curves["BM"][np.newaxis, :]
    sub_rpe = (Z >= rpe_bot_new) & (Z < bm_arr)

    # Serous PED: very low reflectivity; fibrovascular: medium-high
    fill_refl = 0.005 + inst.reflectivity * 0.25
    R = np.where(sub_rpe, fill_refl, R)
    mu = np.where(sub_rpe, 0.001, mu)

    return curves, R, mu


def apply_subretinal_fluid(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """SRF: fluid collection between neurosensory retina and RPE.

    Parameters used:
      severity → height of fluid pocket
      size     → lateral extent
      location → center position
      asymmetry → dome asymmetry
      reflectivity → fluid turbidity (0=clear, 1=turbid)
    """
    x = np.arange(width, dtype=np.float64)
    center_px = inst.location * width
    sigma_px = inst.size * width * 0.5

    dome = _skewed_gaussian(x, center_px, sigma_px, inst.asymmetry)
    fluid_height = dome * inst.severity * 50.0  # up to 50 px fluid

    # SRF: push the outer retina (EZ, ONL) upward, RPE stays
    for key in ["ONL_bot", "EZ_bot"]:
        curves[key] = curves[key] - fluid_height
    # Ensure ordering
    curves["ONL_bot"] = np.maximum(curves["ONL_bot"], curves["OPL_bot"] + 2)
    curves["EZ_bot"] = np.maximum(curves["EZ_bot"], curves["ONL_bot"] + 2)

    # Also slightly detach higher layers proportionally (retinal elevation)
    for key in ["OPL_bot", "INL_bot", "IPL_bot", "GCL_bot", "RNFL_bot", "ILM"]:
        curves[key] = curves[key] - fluid_height * 0.3

    # Fill fluid space
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]
    ez_bot = curves["EZ_bot"][np.newaxis, :]
    rpe_top_z = curves["EZ_bot"][np.newaxis, :] + fluid_height[np.newaxis, :]
    # The SRF region is between the lifted EZ_bot and where RPE actually is
    rpe_bot = curves["RPE_bot"][np.newaxis, :]
    srf_region = (Z >= ez_bot) & (Z < rpe_bot) & (fluid_height[np.newaxis, :] > 1.0)

    fluid_refl = 0.002 + inst.reflectivity * 0.02  # very dark (fluid)
    R = np.where(srf_region, np.minimum(R, fluid_refl), R)
    mu = np.where(srf_region, 0.0005, mu)

    return curves, R, mu


def apply_intraretinal_cyst(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """Intraretinal cysts: round/oval dark fluid-filled spaces in the inner retina.

    Cysts are typically found in INL and OPL layers. Each cyst punches a
    low-reflectivity rounded region into the retina.

    Parameters used:
      severity  → size/prominence of each cyst
      size      → diameter of cysts (fraction of width)
      count     → number of cysts
      location  → cluster center
      distribution → spread of cyst positions
      reflectivity → internal cyst reflectivity (typically very low)
    """
    x_arr = np.arange(width, dtype=np.float64)
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]

    positions = _lateral_positions(rng, inst.count, width, inst.location, inst.distribution)

    cyst_diameter_px = inst.size * width * inst.severity
    cyst_r_x = max(cyst_diameter_px * 0.5, 3.0)
    cyst_r_z = cyst_r_x * rng.uniform(0.6, 1.0)  # slightly oval

    for cx in positions:
        # Cyst center depth: in INL/OPL region
        col = int(np.clip(cx, 0, width - 1))
        inl_top = float(curves["IPL_bot"][col])
        opl_bot = float(curves["OPL_bot"][col])
        cz = rng.uniform(inl_top + 3, max(opl_bot - 3, inl_top + 5))

        # Randomize individual cyst size slightly
        rx = cyst_r_x * rng.uniform(0.7, 1.3)
        rz = cyst_r_z * rng.uniform(0.7, 1.3)

        # Elliptical cyst mask
        dxn = (x_arr[np.newaxis, :] - cx) / (rx + 1e-9)
        dzn = (Z - cz) / (rz + 1e-9)
        dist_sq = dxn ** 2 + dzn ** 2

        # Smooth boundary (sigmoid falloff)
        cyst_mask = 1.0 / (1.0 + np.exp(np.clip((dist_sq - 1.0) / 0.15, -500, 500)))

        cyst_refl = 0.001 + inst.reflectivity * 0.01  # very dark
        R = R * (1.0 - cyst_mask) + cyst_refl * cyst_mask
        mu = mu * (1.0 - cyst_mask) + 0.0003 * cyst_mask

    return curves, R, mu


def apply_cystoid_macular_edema(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """CME: multiple cystoid spaces in the inner retina causing macular thickening.

    CME is essentially multiple clustered intraretinal cysts with retinal thickening.
    The ILM is pushed outward (upward) to accommodate the increased retinal volume.

    Parameters used:
      severity  → degree of thickening and cyst prominence
      size      → individual cyst size
      count     → number of cyst spaces
      location  → center (typically foveal)
      distribution → lateral extent
      reflectivity → cyst fluid reflectivity
      extent    → fraction of retinal thickness involved
    """
    x_arr = np.arange(width, dtype=np.float64)
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]

    # First: thicken the retina by pushing ILM upward
    thickening_envelope = _skewed_gaussian(
        x_arr, inst.location * width,
        inst.distribution * width, inst.asymmetry
    )
    thickening = thickening_envelope * inst.severity * 40.0  # up to 40 px thickening
    curves["ILM"] = curves["ILM"] - thickening
    curves["RNFL_bot"] = curves["RNFL_bot"] - thickening * 0.8
    curves["GCL_bot"] = curves["GCL_bot"] - thickening * 0.6

    # Generate cystoid spaces
    positions = _lateral_positions(rng, inst.count, width, inst.location, inst.distribution)

    for cx in positions:
        col = int(np.clip(cx, 0, width - 1))

        # CME cysts span from INL through OPL, sometimes into GCL
        top_z = float(curves["GCL_bot"][col]) if inst.extent > 0.7 else float(curves["IPL_bot"][col])
        bot_z = float(curves["ONL_bot"][col])
        available = bot_z - top_z

        cz = rng.uniform(top_z + available * 0.2, top_z + available * 0.8)
        rx = inst.size * width * 0.5 * rng.uniform(0.5, 1.2) * inst.severity
        rz = available * 0.3 * inst.severity * rng.uniform(0.5, 1.0)
        rx = max(rx, 3.0)
        rz = max(rz, 3.0)

        dxn = (x_arr[np.newaxis, :] - cx) / (rx + 1e-9)
        dzn = (Z - cz) / (rz + 1e-9)
        dist_sq = dxn ** 2 + dzn ** 2
        cyst_mask = 1.0 / (1.0 + np.exp(np.clip((dist_sq - 1.0) / 0.12, -500, 500)))

        cyst_refl = 0.001 + inst.reflectivity * 0.015
        R = R * (1.0 - cyst_mask) + cyst_refl * cyst_mask
        mu = mu * (1.0 - cyst_mask) + 0.0003 * cyst_mask

    return curves, R, mu


# ==============================================================================
# Group 3: Hole Pathologies
# ==============================================================================

def apply_macular_hole(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """Full-thickness macular hole: gap in all retinal layers from ILM to ELM/EZ.

    Parameters used:
      severity → completeness of tissue loss (1=complete gap)
      size     → diameter of hole
      location → center of hole
      asymmetry → edge asymmetry
      extent   → smoothness of hole edges
      reflectivity → residual tissue reflectivity at hole base
    """
    x_arr = np.arange(width, dtype=np.float64)
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]

    center_px = inst.location * width
    hole_radius = inst.size * width * 0.5

    # Hole profile: steep-sided with smooth edges
    edge_width = hole_radius * (0.1 + inst.extent * 0.3)
    dx = np.abs(x_arr - center_px)
    hole_profile = 1.0 / (1.0 + np.exp((dx - hole_radius) / (edge_width + 1e-9)))

    # Apply asymmetry
    if abs(inst.asymmetry) > 0.01:
        dx_signed = x_arr - center_px
        asym_mod = 1.0 + inst.asymmetry * 0.3 * np.sign(dx_signed)
        hole_profile = hole_profile * np.clip(asym_mod, 0.5, 1.5)

    hole_profile = hole_profile * inst.severity

    # Pull ILM up toward vitreous at hole edges (cuff formation)
    cuff_height = 15.0 * inst.severity
    cuff_profile = np.exp(-0.5 * ((dx - hole_radius) / (hole_radius * 0.3 + 1e-9)) ** 2)
    curves["ILM"] = curves["ILM"] - cuff_height * cuff_profile

    # Remove tissue reflectivity within the hole
    for col in range(width):
        if hole_profile[col] < 0.05:
            continue
        ilm_z = int(max(0, curves["ILM"][col]))
        ez_bot_z = int(min(height - 1, curves["EZ_bot"][col]))
        loss = hole_profile[col]
        R[ilm_z:ez_bot_z, col] *= (1.0 - loss)
        # Replace with near-vitreous signal
        R[ilm_z:ez_bot_z, col] += loss * 2e-4

    return curves, R, mu


def apply_lamellar_macular_hole(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """Lamellar macular hole: partial-thickness defect (inner retina only, EZ intact).

    Parameters used:
      severity → depth of the hole (what fraction of inner retina is lost)
      size     → diameter of hole
      location → center position
      extent   → which layers are involved (0.3=ILM-IPL, 0.7=ILM-OPL)
      asymmetry → edge shape asymmetry
    """
    x_arr = np.arange(width, dtype=np.float64)
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]

    center_px = inst.location * width
    hole_radius = inst.size * width * 0.5

    edge_width = hole_radius * 0.15
    dx = np.abs(x_arr - center_px)
    hole_profile = 1.0 / (1.0 + np.exp((dx - hole_radius) / (edge_width + 1e-9)))
    hole_profile = hole_profile * inst.severity

    # Determine depth: which boundary marks the floor of the lamellar hole
    # extent 0-0.5: IPL/INL; extent 0.5-1.0: down to OPL/ONL
    floor_frac = inst.extent
    floor_keys = ["IPL_bot", "INL_bot", "OPL_bot", "ONL_bot"]
    floor_idx = min(int(floor_frac * len(floor_keys)), len(floor_keys) - 1)
    floor_key = floor_keys[floor_idx]

    for col in range(width):
        if hole_profile[col] < 0.05:
            continue
        ilm_z = int(max(0, curves["ILM"][col]))
        floor_z = int(min(height - 1, curves[floor_key][col]))
        loss = hole_profile[col]
        R[ilm_z:floor_z, col] *= (1.0 - loss)
        R[ilm_z:floor_z, col] += loss * 2e-4

    return curves, R, mu


# ==============================================================================
# Group 4: Disruption Pathologies
# ==============================================================================

def apply_outer_retinal_disruption(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """Outer retinal disruption / EZ loss: localized or diffuse loss of the EZ band.

    Parameters used:
      severity → fraction of EZ signal lost (0=intact, 1=completely absent)
      size     → lateral extent of disruption
      location → center of disruption
      distribution → feathering of edges
      extent   → whether loss extends to interdigitation zone / ELM
    """
    x_arr = np.arange(width, dtype=np.float64)
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]

    envelope = _skewed_gaussian(x_arr, inst.location * width,
                                inst.size * width * 0.5, inst.asymmetry)

    # EZ band region
    ez_top = curves["ONL_bot"][np.newaxis, :]
    ez_bot = curves["EZ_bot"][np.newaxis, :]
    in_ez = (Z >= ez_top) & (Z < ez_bot)

    loss_2d = envelope[np.newaxis, :] * inst.severity
    R = np.where(in_ez, R * (1.0 - loss_2d * 0.85), R)

    return curves, R, mu


def apply_retinal_thinning(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """Retinal thinning / localized layer loss.

    Parameters used:
      severity → fraction of thickness lost
      size     → lateral extent
      location → center
      distribution → feathering
      extent   → which layer to thin (0-0.3=RNFL, 0.3-0.5=GCL, 0.5-0.7=INL, 0.7-1.0=ONL)
    """
    x_arr = np.arange(width, dtype=np.float64)

    envelope = _skewed_gaussian(x_arr, inst.location * width,
                                inst.size * width * 0.5, inst.asymmetry)

    # Select target layer based on extent
    # Each entry: (top_boundary, bottom_boundary) — thinning moves bottom upward
    layer_map = [
        ("ILM",      "RNFL_bot"),  # RNFL thinning
        ("RNFL_bot", "GCL_bot"),   # GCL thinning
        ("IPL_bot",  "INL_bot"),   # INL thinning
        ("OPL_bot",  "ONL_bot"),   # ONL thinning
    ]
    layer_idx = min(int(inst.extent * len(layer_map)), len(layer_map) - 1)
    top_key, bot_key = layer_map[layer_idx]

    # Compute thinning: reduce layer thickness by moving bot_key upward
    old_bot = curves[bot_key].copy()
    current_thickness = curves[bot_key] - curves[top_key]
    thinning = current_thickness * inst.severity * envelope
    thinning = np.minimum(thinning, np.maximum(current_thickness - 2, 0))  # keep min 2px
    thinning = np.maximum(thinning, 0)

    # Move the bottom boundary of the target layer upward (reduce layer thickness)
    curves[bot_key] = curves[bot_key] - thinning

    # Ensure this doesn't cross the top boundary
    curves[bot_key] = np.maximum(curves[bot_key], curves[top_key] + 1)

    # Modify reflectivity: in the vacated area (between new and old bot), reduce signal
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]
    new_bot_2d = curves[bot_key][np.newaxis, :]
    old_bot_2d = old_bot[np.newaxis, :]
    vacated = (Z >= new_bot_2d) & (Z < old_bot_2d) & (thinning[np.newaxis, :] > 1.0)
    R = np.where(vacated, R * 0.2, R)  # reduce reflectivity in thinned area

    return curves, R, mu


def apply_hyperreflective_foci(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """Hyperreflective foci: scattered small bright punctate deposits.

    These are tiny (1-3 px) bright spots within the retinal layers,
    associated with lipid exudation or migrating RPE cells.

    Parameters used:
      severity → brightness of foci
      count    → number of foci
      location → cluster center
      distribution → spread of foci
      extent   → depth range (0=inner retina, 1=full retina depth)
      reflectivity → peak reflectivity of each focus
    """
    x_arr = np.arange(width, dtype=np.float64)
    Z = np.arange(height, dtype=np.float64)

    positions_x = _lateral_positions(rng, inst.count, width, inst.location, inst.distribution)

    focus_brightness = 0.3 + inst.reflectivity * 0.7 + inst.severity * 0.3

    for cx in positions_x:
        col = int(np.clip(cx, 2, width - 3))
        # Determine depth range based on extent
        ilm_z = float(curves["ILM"][col])
        rpe_z = float(curves["RPE_bot"][col])
        depth_range = rpe_z - ilm_z

        if inst.extent < 0.5:
            # Inner retina only
            top_z = ilm_z + depth_range * 0.1
            bot_z = ilm_z + depth_range * (0.3 + inst.extent * 0.4)
        else:
            # Full retina depth
            top_z = ilm_z + depth_range * 0.1
            bot_z = ilm_z + depth_range * (0.5 + inst.extent * 0.4)

        cz = rng.uniform(top_z, max(bot_z, top_z + 3))
        focus_r = rng.uniform(1.0, 2.5)  # tiny spot radius

        # Place bright Gaussian spot
        row_start = max(0, int(cz - 4))
        row_end = min(height, int(cz + 5))
        col_start = max(0, col - 3)
        col_end = min(width, col + 4)

        for r in range(row_start, row_end):
            for c in range(col_start, col_end):
                d = np.sqrt((r - cz) ** 2 + (c - cx) ** 2) / (focus_r + 1e-9)
                if d < 3.0:
                    bright = focus_brightness * np.exp(-0.5 * d ** 2)
                    R[r, c] = max(R[r, c], bright)

    return curves, R, mu


# ==============================================================================
# Group 5: Interface Pathologies
# ==============================================================================

def apply_epiretinal_membrane(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """ERM: thin hyperreflective line above/on the ILM surface.

    Parameters used:
      severity → brightness and thickness of the membrane
      size     → lateral extent
      location → center position
      reflectivity → membrane reflectivity (typically bright)
      extent   → separation from ILM (0=adherent, 1=partially detached)
      asymmetry → shape asymmetry
    """
    x_arr = np.arange(width, dtype=np.float64)
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]

    envelope = _skewed_gaussian(x_arr, inst.location * width,
                                inst.size * width * 0.5, inst.asymmetry)

    # ERM sits above ILM with some separation
    separation = inst.extent * 8.0  # 0-8 px above ILM
    membrane_z = curves["ILM"] - separation * envelope
    membrane_thickness = 1.5 + inst.severity * 3.0  # 1.5-4.5 px thick

    # Add wavy perturbation to the membrane
    noise = rng.standard_normal(width)
    wave = gaussian_filter1d(noise, width * 0.05, mode="reflect")
    wave = wave / (wave.std() + 1e-9) * 2.0
    membrane_z = membrane_z + wave * envelope

    # Draw the membrane as a bright Gaussian line
    mem_z_2d = membrane_z[np.newaxis, :]
    env_2d = envelope[np.newaxis, :]
    membrane_line = (inst.reflectivity * 0.6 + 0.3) * \
                    np.exp(-0.5 * ((Z - mem_z_2d) / (membrane_thickness * 0.5 + 0.5)) ** 2)
    membrane_line = membrane_line * env_2d

    R = np.maximum(R, membrane_line)

    return curves, R, mu


def apply_vitreomacular_traction(
    inst: PathologyInstance,
    curves: Dict[str, np.ndarray],
    R: np.ndarray, mu: np.ndarray,
    width: int, height: int,
    rng: np.random.Generator,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """VMT: vitreous attached to and pulling on the macula, deforming the ILM.

    Combines a hyperreflective line (posterior hyaloid) with ILM deformation.

    Parameters used:
      severity → traction force (ILM deformation depth)
      size     → width of attachment zone
      location → attachment center
      reflectivity → hyaloid face brightness
      extent   → how detached the hyaloid is peripherally
      asymmetry → asymmetric traction
    """
    x_arr = np.arange(width, dtype=np.float64)
    Z = np.arange(height, dtype=np.float64)[:, np.newaxis]

    center_px = inst.location * width
    attach_width = inst.size * width * 0.5

    # Traction zone: narrow attachment at center
    attach_profile = _skewed_gaussian(x_arr, center_px, attach_width, inst.asymmetry)

    # ILM deformation: pull upward (lower z) at attachment point
    traction_depth = inst.severity * 30.0  # up to 30 px traction
    curves["ILM"] = curves["ILM"] - traction_depth * attach_profile

    # Posterior hyaloid face: V-shaped line from detached vitreous to attachment
    detach_height = inst.extent * 40.0  # how high above ILM the hyaloid goes peripherally
    hyaloid_z = curves["ILM"].copy()
    # Peripherally, the hyaloid rises above the ILM
    periph_profile = 1.0 - attach_profile
    hyaloid_z = hyaloid_z - detach_height * periph_profile

    # Draw hyaloid as a hyperreflective line
    hyal_z_2d = hyaloid_z[np.newaxis, :]
    hyaloid_line = (0.2 + inst.reflectivity * 0.5) * \
                   np.exp(-0.5 * ((Z - hyal_z_2d) / 1.5) ** 2)

    # Only draw where the hyaloid is actually above the ILM
    ilm_2d = curves["ILM"][np.newaxis, :]
    above_ilm = Z < ilm_2d
    hyaloid_line = hyaloid_line * above_ilm

    R = np.maximum(R, hyaloid_line)

    return curves, R, mu


# ==============================================================================
# Dispatch table
# ==============================================================================

_DISPATCH = {
    "drusen": apply_drusen,
    "rpe_irregularity": apply_rpe_irregularity,
    "rpe_attenuation": apply_rpe_attenuation,
    "geographic_atrophy": apply_geographic_atrophy,
    "pigment_epithelial_detachment": apply_pigment_epithelial_detachment,
    "subretinal_fluid": apply_subretinal_fluid,
    "intraretinal_cyst": apply_intraretinal_cyst,
    "cystoid_macular_edema": apply_cystoid_macular_edema,
    "macular_hole": apply_macular_hole,
    "lamellar_macular_hole": apply_lamellar_macular_hole,
    "outer_retinal_disruption": apply_outer_retinal_disruption,
    "retinal_thinning": apply_retinal_thinning,
    "hyperreflective_foci": apply_hyperreflective_foci,
    "epiretinal_membrane": apply_epiretinal_membrane,
    "vitreomacular_traction": apply_vitreomacular_traction,
}


# ==============================================================================
# Public API
# ==============================================================================

def apply_all_pathologies(
    scene: SceneConfig,
    curves: Dict[str, np.ndarray],
    R: np.ndarray,
    mu: np.ndarray,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """Apply all enabled pathology instances to the normal-anatomy structures.

    Modifies (boundary_curves, R, mu) **in-place** and also returns them.

    Application order follows APPLICATION_ORDER (documented at module top).
    Within each type group, instances are applied in list order.

    Each instance gets its own RNG from (render_seed + seed_offset),
    ensuring independent reproducibility regardless of list position.

    Args:
        scene  : SceneConfig (reads pathology.instances and anatomy.render_seed)
        curves : dict[str → np.ndarray(W,)] boundary depth curves
        R      : (H, W) float64 reflectivity map
        mu     : (H, W) float64 attenuation coefficient map

    Returns:
        (curves, R, mu) — modified structures
    """
    pathology = scene.pathology
    if not pathology.enabled or not pathology.instances:
        return curves, R, mu

    render_seed = scene.anatomy.render_seed
    width = scene.acquisition.width
    height = scene.acquisition.height

    # Group instances by type for ordered application
    instances_by_type: Dict[str, List[PathologyInstance]] = {}
    for inst in pathology.instances:
        instances_by_type.setdefault(inst.type, []).append(inst)

    # Apply in fixed order
    for ptype in APPLICATION_ORDER:
        if ptype not in instances_by_type:
            continue
        handler = _DISPATCH.get(ptype)
        if handler is None:
            continue
        for inst in instances_by_type[ptype]:
            rng = _instance_rng(render_seed, inst)
            curves, R, mu = handler(inst, curves, R, mu, width, height, rng)

    return curves, R, mu
