"""Presets and Randomization module for OCTForge.

Provides curated, schema-valid clinical pathology presets, export presets,
bounded physiological scene randomization, and pathology variant generation.

CRITICAL ARCHITECTURAL LAW:
All presets produce standard, fully-validated SceneConfig objects using the
canonical Pydantic schema — no proprietary or special-cased preset formats.
"""

import copy
from typing import Dict, List, Optional, Union, Any
import numpy as np

from backend.app.schema import (
    SceneConfig,
    MetaConfig,
    AnatomyConfig,
    PathologyConfig,
    PathologyInstance,
    AcquisitionConfig,
    RealismConfig,
    ArtifactsConfig,
    PathologyPresetInfo,
    ExportPresetInfo,
)
from backend.app.export import list_export_presets, get_export_preset


# ==============================================================================
# Curated Clinical Pathology Presets
# ==============================================================================

def _build_normal_macula() -> SceneConfig:
    """Standard healthy normal human macula."""
    return SceneConfig(
        meta=MetaConfig(
            name="Normal Macula",
            description="Healthy normal foveal architecture with preserved retinal layer stratification and reflectance profiles.",
        ),
        anatomy=AnatomyConfig(
            fovea_center=256.0,
            fovea_pit_depth=80.0,
            fovea_pit_width=0.30,
            curvature=0.05,
            rnfl_thickness=28.0,
            gcl_thickness=20.0,
            ipl_thickness=18.0,
            inl_thickness=14.0,
            opl_thickness=12.0,
            onl_thickness=52.0,
            ez_thickness=20.0,
            rpe_thickness=8.0,
            choroid_thickness=130.0,
            render_seed=42,
        ),
        pathology=PathologyConfig(enabled=False, instances=[]),
        acquisition=AcquisitionConfig(
            scan_width=6.0,
            scan_depth=2.0,
            width=512,
            height=512,
            signal_strength=8.5,
            snr_db=28.0,
        ),
        realism=RealismConfig(
            speckle_amount=1.0,
            speckle_avg_count=6,
            contrast=1.0,
            db_lo=-35.0,
            db_hi=-2.0,
            display_gamma=1.55,
            blur=0.55,
        ),
        artifacts=ArtifactsConfig(enabled=False, instances=[]),
    )


def _build_cme() -> SceneConfig:
    """Cystoid Macular Edema (CME)."""
    scene = _build_normal_macula()
    scene.meta.name = "Cystoid Macular Edema (CME)"
    scene.meta.description = "Multiple hyporeflective intraretinal cystoid spaces within the INL and OPL with loss of foveal depression and retinal thickening."
    scene.anatomy.fovea_pit_depth = 30.0  # Blunted foveal pit
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(
            type="cystoid_macular_edema",
            severity=0.75,
            size=0.35,
            count=8,
            location=0.50,
            distribution=0.14,
            reflectivity=0.08,
            extent=0.65,
            asymmetry=0.0,
            seed_offset=101,
        )
    ]
    return scene


def _build_srf() -> SceneConfig:
    """Subretinal Fluid (SRF) / Central Serous Chorioretinopathy."""
    scene = _build_normal_macula()
    scene.meta.name = "Subretinal Fluid (SRF)"
    scene.meta.description = "Dome-shaped hyporeflective neurosensory retinal detachment separating the photoreceptor layer from the underlying hyperreflective RPE."
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(
            type="subretinal_fluid",
            severity=0.70,
            size=0.38,
            count=1,
            location=0.50,
            distribution=0.10,
            reflectivity=0.05,
            extent=0.55,
            asymmetry=0.0,
            seed_offset=202,
        )
    ]
    return scene


def _build_drusen() -> SceneConfig:
    """Drusen (Dry AMD)."""
    scene = _build_normal_macula()
    scene.meta.name = "Drusen (Dry AMD)"
    scene.meta.description = "Multiple discrete convex and dome-shaped sub-RPE deposits (drusen) with homogeneous medium internal reflectivity elevating the RPE band."
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(
            type="drusen",
            severity=0.65,
            size=0.10,
            count=7,
            location=0.50,
            distribution=0.20,
            reflectivity=0.50,
            extent=0.45,
            asymmetry=0.1,
            seed_offset=303,
        )
    ]
    return scene


def _build_ped() -> SceneConfig:
    """Pigment Epithelial Detachment (PED)."""
    scene = _build_normal_macula()
    scene.meta.name = "Pigment Epithelial Detachment (PED)"
    scene.meta.description = "Large, distinct convex focal detachment of the hyperreflective RPE monolayer from Bruch's membrane with hyporeflective / serous sub-RPE space."
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(
            type="pigment_epithelial_detachment",
            severity=0.80,
            size=0.32,
            count=1,
            location=0.50,
            distribution=0.05,
            reflectivity=0.15,
            extent=0.75,
            asymmetry=0.0,
            seed_offset=404,
        )
    ]
    return scene


def _build_erm() -> SceneConfig:
    """Epiretinal Membrane (ERM)."""
    scene = _build_normal_macula()
    scene.meta.name = "Epiretinal Membrane (ERM)"
    scene.meta.description = "Hyperreflective pre-retinal fibrocellular membrane lining the ILM with surface wrinkling and tractional inner retinal thickening."
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(
            type="epiretinal_membrane",
            severity=0.75,
            size=0.65,
            count=1,
            location=0.50,
            distribution=0.15,
            reflectivity=0.90,
            extent=0.40,
            asymmetry=0.1,
            seed_offset=505,
        )
    ]
    return scene


def _build_macular_hole() -> SceneConfig:
    """Full-Thickness Macular Hole (FTMH)."""
    scene = _build_normal_macula()
    scene.meta.name = "Full-Thickness Macular Hole (FTMH)"
    scene.meta.description = "Complete anatomical full-thickness foveal defect extending from the ILM through the photoreceptor layer with rounded, thickened cuff edges."
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(
            type="macular_hole",
            severity=0.85,
            size=0.14,
            count=1,
            location=0.50,
            distribution=0.05,
            reflectivity=0.05,
            extent=0.95,
            asymmetry=0.0,
            seed_offset=606,
        )
    ]
    return scene


def _build_lamellar_hole() -> SceneConfig:
    """Lamellar Macular Hole (LMH)."""
    scene = _build_normal_macula()
    scene.meta.name = "Lamellar Macular Hole (LMH)"
    scene.meta.description = "Partial-thickness foveal defect with irregular foveal contour, preserved intact photoreceptor layer and split outer retinal layers."
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(
            type="lamellar_macular_hole",
            severity=0.65,
            size=0.18,
            count=1,
            location=0.50,
            distribution=0.05,
            reflectivity=0.08,
            extent=0.50,
            asymmetry=0.0,
            seed_offset=707,
        )
    ]
    return scene


def _build_geographic_atrophy() -> SceneConfig:
    """Geographic Atrophy (GA)."""
    scene = _build_normal_macula()
    scene.meta.name = "Geographic Atrophy (GA)"
    scene.meta.description = "Sharply demarcated zone of complete RPE and outer retinal atrophy with enhanced optical transmission (hypertransmission) into the choroid."
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(
            type="geographic_atrophy",
            severity=0.85,
            size=0.30,
            count=1,
            location=0.50,
            distribution=0.05,
            reflectivity=0.10,
            extent=0.80,
            asymmetry=0.0,
            seed_offset=808,
        )
    ]
    return scene


def _build_vmt() -> SceneConfig:
    """Vitreomacular Traction (VMT)."""
    scene = _build_normal_macula()
    scene.meta.name = "Vitreomacular Traction (VMT)"
    scene.meta.description = "Partial posterior vitreous detachment with persistent hyperreflective vitreous adhesion at the fovea causing tractional tenting."
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(
            type="vitreomacular_traction",
            severity=0.70,
            size=0.22,
            count=1,
            location=0.50,
            distribution=0.05,
            reflectivity=0.60,
            extent=0.60,
            asymmetry=0.0,
            seed_offset=909,
        )
    ]
    return scene


def _build_outer_retinal_disruption() -> SceneConfig:
    """Outer Retinal Disruption."""
    scene = _build_normal_macula()
    scene.meta.name = "Outer Retinal Disruption"
    scene.meta.description = "Focal discontinuity, attenuation, and fragmentation of the ellipsoid zone (EZ / IS-OS) and interdigitation zone."
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(
            type="outer_retinal_disruption",
            severity=0.75,
            size=0.25,
            count=1,
            location=0.50,
            distribution=0.08,
            reflectivity=0.20,
            extent=0.50,
            asymmetry=0.0,
            seed_offset=1010,
        )
    ]
    return scene


def _build_hyperreflective_foci() -> SceneConfig:
    """Hyperreflective Foci (HRF)."""
    scene = _build_normal_macula()
    scene.meta.name = "Hyperreflective Foci (HRF)"
    scene.meta.description = "Multiple punctate, discrete hyperreflective intraretinal lesions scattered across inner and outer retinal layers."
    scene.pathology.enabled = True
    scene.pathology.instances = [
        PathologyInstance(
            type="hyperreflective_foci",
            severity=0.60,
            size=0.03,
            count=14,
            location=0.50,
            distribution=0.22,
            reflectivity=0.95,
            extent=0.60,
            asymmetry=0.0,
            seed_offset=1111,
        )
    ]
    return scene


PATHOLOGY_PRESETS: Dict[str, Dict[str, Any]] = {
    "normal": {
        "name": "Normal Macula",
        "category": "Normal Anatomy",
        "builder": _build_normal_macula,
    },
    "cme": {
        "name": "Cystoid Macular Edema (CME)",
        "category": "Fluid-Space",
        "builder": _build_cme,
    },
    "srf": {
        "name": "Subretinal Fluid (SRF)",
        "category": "Fluid-Space",
        "builder": _build_srf,
    },
    "drusen": {
        "name": "Drusen (Dry AMD)",
        "category": "RPE-Level",
        "builder": _build_drusen,
    },
    "ped": {
        "name": "Pigment Epithelial Detachment (PED)",
        "category": "Fluid-Space",
        "builder": _build_ped,
    },
    "erm": {
        "name": "Epiretinal Membrane (ERM)",
        "category": "Interface",
        "builder": _build_erm,
    },
    "macular_hole": {
        "name": "Full-Thickness Macular Hole (FTMH)",
        "category": "Hole",
        "builder": _build_macular_hole,
    },
    "lamellar_hole": {
        "name": "Lamellar Macular Hole (LMH)",
        "category": "Hole",
        "builder": _build_lamellar_hole,
    },
    "geographic_atrophy": {
        "name": "Geographic Atrophy (GA)",
        "category": "RPE-Level",
        "builder": _build_geographic_atrophy,
    },
    "vmt": {
        "name": "Vitreomacular Traction (VMT)",
        "category": "Interface",
        "builder": _build_vmt,
    },
    "outer_retinal_disruption": {
        "name": "Outer Retinal Disruption",
        "category": "Disruption",
        "builder": _build_outer_retinal_disruption,
    },
    "hyperreflective_foci": {
        "name": "Hyperreflective Foci (HRF)",
        "category": "Disruption",
        "builder": _build_hyperreflective_foci,
    },
}


def list_pathology_presets() -> List[PathologyPresetInfo]:
    """Return all curated pathology presets with complete SceneConfig objects."""
    results = []
    for pid, info in PATHOLOGY_PRESETS.items():
        scene = info["builder"]()
        results.append(PathologyPresetInfo(
            id=pid,
            name=info["name"],
            category=info["category"],
            description=scene.meta.description,
            scene=scene,
        ))
    return results


def get_pathology_preset(preset_id: str) -> Optional[SceneConfig]:
    """Retrieve a fresh copy of a pathology preset by ID."""
    key = preset_id.lower().strip()
    if key in PATHOLOGY_PRESETS:
        return PATHOLOGY_PRESETS[key]["builder"]()
    # Check aliases
    aliases = {
        "ftmh": "macular_hole",
        "lmh": "lamellar_hole",
        "cscr": "srf",
        "ga": "geographic_atrophy",
        "amd": "drusen",
        "dry_amd": "drusen",
        "edema": "cme",
    }
    alias_key = aliases.get(key)
    if alias_key and alias_key in PATHOLOGY_PRESETS:
        return PATHOLOGY_PRESETS[alias_key]["builder"]()
    return None


# ==============================================================================
# Controlled / Bounded Randomization Engine
# ==============================================================================

def randomize_scene(
    base_scene: Optional[SceneConfig] = None,
    seed: Optional[int] = None,
    mode: str = "balanced",
    variance: float = 0.25,
) -> SceneConfig:
    """Randomize scene parameters within strictly bounded clinical & optical ranges.

    Ensures all generated values satisfy schema constraints (ge/le) and remain
    physiologically plausible and optically valid.
    """
    rng = np.random.default_rng(seed if seed is not None else np.random.randint(0, 2**31 - 1))
    target_seed = int(rng.integers(0, 2**31 - 1)) if seed is None else seed

    if base_scene is not None:
        scene = base_scene.model_copy(deep=True)
    else:
        scene = _build_normal_macula()

    v = float(np.clip(variance, 0.05, 1.0))

    def perturb(val: float, low: float, high: float, scale_factor: float = 1.0) -> float:
        span = (high - low) * v * scale_factor
        delta = rng.uniform(-span / 2.0, span / 2.0)
        return float(np.clip(val + delta, low, high))

    # 1. Randomize Anatomy
    if mode in ["all", "balanced", "anatomy_only"]:
        anat = scene.anatomy
        anat.render_seed = target_seed
        # Foveal geometry
        w = scene.acquisition.width
        anat.fovea_center = perturb(anat.fovea_center, w * 0.40, w * 0.60)
        anat.fovea_pit_depth = perturb(anat.fovea_pit_depth, 40.0, 120.0)
        anat.fovea_pit_width = perturb(anat.fovea_pit_width, 0.20, 0.45)
        anat.curvature = perturb(anat.curvature, -0.08, 0.12)
        anat.boundary_variation = perturb(anat.boundary_variation, 1.0, 8.0)
        anat.boundary_smoothness = perturb(anat.boundary_smoothness, 0.05, 0.25)
        anat.boundary_asymmetry = perturb(anat.boundary_asymmetry, -0.4, 0.4)

        # Layer thicknesses
        anat.rnfl_thickness = perturb(anat.rnfl_thickness, 15.0, 45.0)
        anat.gcl_thickness = perturb(anat.gcl_thickness, 12.0, 32.0)
        anat.ipl_thickness = perturb(anat.ipl_thickness, 12.0, 28.0)
        anat.inl_thickness = perturb(anat.inl_thickness, 10.0, 22.0)
        anat.opl_thickness = perturb(anat.opl_thickness, 8.0, 18.0)
        anat.onl_thickness = perturb(anat.onl_thickness, 35.0, 75.0)
        anat.ez_thickness = perturb(anat.ez_thickness, 14.0, 28.0)
        anat.rpe_thickness = perturb(anat.rpe_thickness, 5.0, 12.0)
        anat.choroid_thickness = perturb(anat.choroid_thickness, 80.0, 200.0)

    # 2. Randomize Realism & Noise Floor
    if mode in ["all", "balanced", "realism_only"]:
        real = scene.realism
        real.speckle_amount = perturb(real.speckle_amount, 0.6, 1.4)
        real.contrast = perturb(real.contrast, 0.8, 1.3)
        real.display_gamma = perturb(real.display_gamma, 1.2, 2.0)
        real.blur = perturb(real.blur, 0.3, 0.9)
        real.axial_blur = perturb(real.axial_blur, 1.5, 3.0)
        real.lateral_blur = perturb(real.lateral_blur, 0.6, 1.4)
        real.background_noise = perturb(real.background_noise, -40.0, -25.0)
        real.intensity_nonuniformity = perturb(real.intensity_nonuniformity, 0.05, 0.30)
        real.db_lo = perturb(real.db_lo, -45.0, -28.0)
        real.db_hi = perturb(real.db_hi, -5.0, 5.0)
        if real.db_hi <= real.db_lo + 10.0:
            real.db_hi = real.db_lo + 25.0

    # 3. Randomize Acquisition Signal
    if mode in ["all", "balanced"]:
        acq = scene.acquisition
        acq.signal_strength = perturb(acq.signal_strength, 6.0, 9.8)
        acq.snr_db = perturb(acq.snr_db, 18.0, 35.0)

    # Re-validate against Pydantic schema
    validated_scene = SceneConfig(**scene.model_dump())
    return validated_scene


# ==============================================================================
# Pathology Variant Generator
# ==============================================================================

def generate_pathology_variants(
    preset_or_scene: Union[str, SceneConfig],
    count: int = 4,
    base_seed: Optional[int] = 42,
) -> List[SceneConfig]:
    """Generate N distinct, reproducible variants of a given pathology preset or scene.

    Perturbs lesion size, count, severity, location, and assigns concrete deterministic seeds.
    """
    if isinstance(preset_or_scene, str):
        base = get_pathology_preset(preset_or_scene)
        if base is None:
            base = _build_cme()
    else:
        base = preset_or_scene.model_copy(deep=True)

    root_seed = base_seed if base_seed is not None else 42
    rng = np.random.default_rng(root_seed)

    variants: List[SceneConfig] = []

    for i in range(count):
        var_scene = base.model_copy(deep=True)
        var_seed = int(root_seed + i * 1000 + rng.integers(1, 999))
        var_scene.anatomy.render_seed = var_seed
        var_scene.meta.name = f"{base.meta.name} (Variant {i+1})"

        # If pathology is enabled, perturb the pathology instances
        if var_scene.pathology.enabled and var_scene.pathology.instances:
            new_instances = []
            for inst_idx, inst in enumerate(var_scene.pathology.instances):
                inst_copy = inst.model_copy(deep=True)
                # Perturb severity (+/- 25%)
                sev_delta = rng.uniform(-0.15, 0.15)
                inst_copy.severity = float(np.clip(inst_copy.severity + sev_delta, 0.2, 1.0))

                # Perturb size (+/- 30%)
                size_delta = rng.uniform(-0.05, 0.08)
                inst_copy.size = float(np.clip(inst_copy.size + size_delta, 0.04, 0.60))

                # Perturb location (+/- 10%)
                loc_delta = rng.uniform(-0.08, 0.08)
                inst_copy.location = float(np.clip(inst_copy.location + loc_delta, 0.25, 0.75))

                # Perturb count if multi-focal
                if inst_copy.count > 1:
                    count_delta = rng.integers(-2, 4)
                    inst_copy.count = int(np.clip(inst_copy.count + count_delta, 2, 30))

                # Unique seed offset per variant instance
                inst_copy.seed_offset = int((i + 1) * 100 + inst_idx * 10 + rng.integers(1, 50))
                new_instances.append(inst_copy)

            var_scene.pathology.instances = new_instances

        # Perturb subtle anatomical variations
        var_scene.anatomy.fovea_pit_depth = float(np.clip(
            var_scene.anatomy.fovea_pit_depth + rng.uniform(-15.0, 15.0), 10.0, 140.0
        ))
        var_scene.anatomy.curvature = float(np.clip(
            var_scene.anatomy.curvature + rng.uniform(-0.03, 0.03), -0.10, 0.15
        ))

        # Validate schema
        validated_var = SceneConfig(**var_scene.model_dump())
        variants.append(validated_var)

    return variants
