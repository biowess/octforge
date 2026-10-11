"""OCTForge Backend Data Schemas.

Single source of truth for all data models.
Organized into section comments for schema stability and readability.
"""

from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


# ==============================================================================
# SECTION 1: Meta Configuration
# ==============================================================================

class MetaConfig(BaseModel):
    name: str = Field(default="Untitled Scene", description="Human-readable scene name")
    description: str = Field(default="", description="Detailed description of the scene")
    version: str = Field(default="1.0.0", description="Schema version tag")
    created_at: Optional[str] = Field(default=None, description="ISO creation timestamp")
    updated_at: Optional[str] = Field(default=None, description="ISO update timestamp")


# ==============================================================================
# SECTION 2: Anatomy Configuration
# ==============================================================================

class AnatomyConfig(BaseModel):
    """Full anatomical geometry and layer-reflectivity specification for a normal eye.

    Coordinate convention: pixel space (height x width). Depth axis (Z) runs from
    top (vitreous) downward to sclera. Thickness values are expressed in pixels
    in image space — they scale with acquisition.height.

    Layer ordering (inner → outer):
        ILM → RNFL → GCL → IPL → INL → OPL → ONL → ELM → EZ (IS/OS) → RPE → BM
        → Choroid → Sclera
    """

    # -- Foveal pit geometry --
    fovea_center: float = Field(
        default=256.0, ge=0.0, le=2048.0,
        description="Fovea center X-coordinate index (pixels)"
    )
    fovea_pit_depth: float = Field(
        default=80.0, ge=0.0, le=250.0,
        description=(
            "Axial depth of the foveal pit depression below the parafoveal ILM peak (pixels). "
            "Controls how much the inner retinal layers thin/vanish at the foveal center."
        )
    )
    fovea_pit_width: float = Field(
        default=0.30, ge=0.05, le=0.80,
        description=(
            "Half-width of the foveal pit as a fraction of scan width. "
            "Gaussian sigma driving the pit depression profile."
        )
    )

    # -- ILM curvature / overall retinal shape --
    curvature: float = Field(
        default=0.05, ge=-0.20, le=0.20,
        description=(
            "Parabolic axial curvature of the ILM baseline (positive = concave-down / "
            "nasal tilt; negative = concave-up). Fraction of image height at scan edge."
        )
    )

    # -- Boundary irregularity / roughness --
    boundary_smoothness: float = Field(
        default=0.12, ge=0.01, le=0.50,
        description=(
            "Gaussian sigma of band-limited noise injected into each boundary curve, "
            "as a fraction of scan width. Larger values give longer-wavelength undulations."
        )
    )
    boundary_variation: float = Field(
        default=4.0, ge=0.0, le=20.0,
        description="Peak-to-peak amplitude (pixels) of random boundary undulation noise."
    )
    boundary_asymmetry: float = Field(
        default=0.0, ge=-1.0, le=1.0,
        description=(
            "Lateral asymmetry of ILM height: +1 tilts retina nasally, -1 temporally. "
            "Implemented as a linear gradient superimposed on the ILM baseline."
        )
    )

    # -- Per-layer thickness values (pixels, at parafoveal peak) --
    rnfl_thickness: float = Field(
        default=28.0, ge=2.0, le=80.0,
        description="RNFL thickness at parafoveal peak (pixels)"
    )
    gcl_thickness: float = Field(
        default=20.0, ge=1.0, le=60.0,
        description="GCL thickness at parafoveal peak (pixels)"
    )
    ipl_thickness: float = Field(
        default=18.0, ge=1.0, le=60.0,
        description="IPL thickness at parafoveal peak (pixels)"
    )
    inl_thickness: float = Field(
        default=14.0, ge=1.0, le=50.0,
        description="INL thickness at parafoveal peak (pixels)"
    )
    opl_thickness: float = Field(
        default=12.0, ge=1.0, le=50.0,
        description="OPL thickness at parafoveal peak (pixels)"
    )
    onl_thickness: float = Field(
        default=52.0, ge=5.0, le=140.0,
        description="ONL thickness at parafoveal peak (pixels)"
    )
    ez_thickness: float = Field(
        default=20.0, ge=4.0, le=50.0,
        description=(
            "Photoreceptor band thickness (ELM → RPE, encompassing ELM, IS, EZ/IS-OS, "
            "OS, interdigitation zone) at parafoveal position (pixels)"
        )
    )
    rpe_thickness: float = Field(
        default=8.0, ge=2.0, le=20.0,
        description="RPE + Bruch's membrane complex thickness (pixels)"
    )

    # -- Choroid --
    choroid_thickness: float = Field(
        default=130.0, ge=20.0, le=400.0,
        description="Choroid thickness (RPE → choroid–sclera interface, pixels)"
    )

    # -- Layer reflectivity coefficients (linear, pre-log) --
    rnfl_reflectivity: float = Field(
        default=0.85, ge=0.0, le=1.0,
        description="RNFL (+ ILM bright line) reflectivity scale factor"
    )
    gcl_reflectivity: float = Field(
        default=0.25, ge=0.0, le=1.0,
        description="GCL reflectivity scale factor"
    )
    ipl_reflectivity: float = Field(
        default=0.40, ge=0.0, le=1.0,
        description="IPL reflectivity scale factor"
    )
    inl_reflectivity: float = Field(
        default=0.14, ge=0.0, le=1.0,
        description="INL reflectivity scale factor"
    )
    opl_reflectivity: float = Field(
        default=0.40, ge=0.0, le=1.0,
        description="OPL reflectivity scale factor"
    )
    onl_reflectivity: float = Field(
        default=0.06, ge=0.0, le=1.0,
        description="ONL reflectivity scale factor"
    )
    ez_reflectivity: float = Field(
        default=0.80, ge=0.0, le=1.0,
        description="EZ / IS-OS junction reflectivity scale factor"
    )
    rpe_reflectivity: float = Field(
        default=0.95, ge=0.0, le=1.0,
        description="RPE band peak reflectivity scale factor"
    )
    choroid_reflectivity: float = Field(
        default=0.40, ge=0.0, le=1.0,
        description="Choroidal stromal reflectivity scale factor"
    )

    # -- Legacy field aliases (kept for backward compatibility) --
    fovea_depth: float = Field(
        default=80.0, ge=0.0, le=300.0,
        description="[Legacy] Alias for fovea_pit_depth; ignored if fovea_pit_depth is set."
    )
    total_retina_thickness: float = Field(
        default=250.0, ge=50.0, le=600.0,
        description="[Legacy] Baseline total retinal thickness in pixels."
    )
    vitreous_reflectivity: float = Field(
        default=0.05, ge=0.0, le=1.0,
        description="Vitreous region optical reflectivity (noise floor region)"
    )
    nfl_reflectivity: float = Field(
        default=0.85, ge=0.0, le=1.0,
        description="[Legacy] Alias for rnfl_reflectivity."
    )

    # -- Display / rendering quality --
    render_seed: int = Field(
        default=42, ge=0, le=2**31 - 1,
        description=(
            "Integer seed for all stochastic render elements (boundary noise, speckle, "
            "vessel positions). Identical seed → identical output."
        )
    )


# ==============================================================================
# SECTION 3: Pathology Configuration
# ==============================================================================

PATHOLOGY_TYPES = [
    "intraretinal_cyst",
    "cystoid_macular_edema",
    "subretinal_fluid",
    "drusen",
    "pigment_epithelial_detachment",
    "rpe_irregularity",
    "rpe_attenuation",
    "geographic_atrophy",
    "epiretinal_membrane",
    "vitreomacular_traction",
    "macular_hole",
    "lamellar_macular_hole",
    "outer_retinal_disruption",
    "hyperreflective_foci",
    "retinal_thinning",
]


class PathologyInstance(BaseModel):
    """A single pathology instance with full parametric control."""
    model_config = ConfigDict(extra="ignore")

    type: str = Field(
        ...,
        description=(
            "Pathology type identifier. One of: intraretinal_cyst, "
            "cystoid_macular_edema, subretinal_fluid, drusen, "
            "pigment_epithelial_detachment, rpe_irregularity, rpe_attenuation, "
            "geographic_atrophy, epiretinal_membrane, vitreomacular_traction, "
            "macular_hole, lamellar_macular_hole, outer_retinal_disruption, "
            "hyperreflective_foci, retinal_thinning."
        )
    )

    severity: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Overall severity / intensity of the pathology (0=minimal, 1=maximal)."
    )
    size: float = Field(
        default=0.10, ge=0.01, le=1.0,
        description="Lateral size of the lesion as a fraction of image width."
    )
    count: int = Field(
        default=1, ge=1, le=50,
        description="Number of individual focal elements within this instance."
    )
    location: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Lateral center of the pathology as a fraction of image width."
    )
    distribution: float = Field(
        default=0.15, ge=0.01, le=0.5,
        description="Lateral spread (sigma) of multi-focal element distribution."
    )
    reflectivity: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Reflectivity parameter controlling brightness of the pathological feature."
    )
    extent: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Axial extent / height fraction of the lesion relative to affected layer(s)."
    )
    asymmetry: float = Field(
        default=0.0, ge=-1.0, le=1.0,
        description="Shape asymmetry of the lesion (-1=left, 0=symmetric, +1=right)."
    )
    seed_offset: int = Field(
        default=0, ge=0, le=2**31 - 1,
        description="Offset added to render_seed for instance-specific RNG stream."
    )


class PathologyConfig(BaseModel):
    """Pathology configuration holding a list of composable PathologyInstance objects."""
    enabled: bool = Field(
        default=False,
        description="Whether pathological modifications are enabled"
    )
    instances: List[PathologyInstance] = Field(
        default_factory=list,
        description="List of PathologyInstance objects to apply in fixed order."
    )

    # -- Legacy fields --
    drusen: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="[Legacy] List of drusen lesion specifications"
    )
    fluid_pockets: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="[Legacy] Intra/sub-retinal fluid pocket specs"
    )
    cnv: Optional[Dict[str, Any]] = Field(
        default=None,
        description="[Legacy] Choroidal Neovascularization spec"
    )


# ==============================================================================
# SECTION 4: Acquisition Configuration
# ==============================================================================

class AcquisitionConfig(BaseModel):
    """Physical acquisition geometry, sampling grid, and optical specifications."""
    model_config = ConfigDict(extra="ignore")

    scan_width: float = Field(
        default=6.0, ge=1.0, le=16.0,
        description="Lateral scan width across the retina in millimeters (e.g. 6.0 mm macular scan)"
    )
    scan_depth: float = Field(
        default=2.0, ge=0.5, le=5.0,
        description="Axial imaging depth in millimeters in retinal tissue (e.g. 2.0 mm)"
    )
    width: int = Field(
        default=512, ge=128, le=2048,
        description="B-scan width (number of columns / A-scans in output image)"
    )
    height: int = Field(
        default=512, ge=128, le=2048,
        description="B-scan height (number of axial pixels in output image)"
    )
    a_scan_density: int = Field(
        default=512, ge=64, le=2048,
        description="Number of raw acquired A-scan lines across the lateral scan extent"
    )
    b_scan_count: int = Field(
        default=1, ge=1, le=256,
        description="Number of B-scans in the raster volume (1 for single 2D B-scan)"
    )
    output_resolution: List[int] = Field(
        default_factory=lambda: [512, 512],
        description="Configured output image dimensions [height, width] in pixels"
    )
    axial_resolution: float = Field(
        default=4.0, ge=1.0, le=20.0,
        description="Optical axial resolution / coherence length in micrometers (FWHM in tissue)"
    )
    lateral_resolution: float = Field(
        default=15.0, ge=2.0, le=50.0,
        description="Optical lateral resolution / focused spot diameter in micrometers (FWHM)"
    )
    averaging: int = Field(
        default=1, ge=1, le=64,
        description="Hardware B-scan frame averaging count (navg) during acquisition"
    )
    signal_strength: float = Field(
        default=8.0, ge=0.0, le=10.0,
        description="Overall acquisition signal strength index (SSI / quality score, 0-10 scale)"
    )
    snr_db: float = Field(
        default=25.0, ge=0.0, le=60.0,
        description="Signal-to-Noise Ratio in dB"
    )

    @model_validator(mode="after")
    def validate_acquisition(self) -> "AcquisitionConfig":
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"Acquisition dimensions must be positive integers: width={self.width}, height={self.height}")
        if self.scan_width <= 0.0 or self.scan_depth <= 0.0:
            raise ValueError(f"Scan physical dimensions must be positive: scan_width={self.scan_width}, scan_depth={self.scan_depth}")
        return self


# ==============================================================================
# SECTION 5: Realism Configuration
# ==============================================================================

class RealismConfig(BaseModel):
    """Optical realism knobs, speckle statistics, noise floors, and display mapping."""
    model_config = ConfigDict(extra="ignore")

    speckle_amount: float = Field(
        default=1.0, ge=0.0, le=2.0,
        description="Speckle contrast scaling factor (0 = smooth noiseless, 1 = full Rayleigh speckle)"
    )
    speckle_avg_count: int = Field(
        default=6, ge=1, le=32,
        description="Number of independent coherent speckle realizations to average in final render."
    )
    speckle_noise_level: float = Field(
        default=0.30, ge=0.0, le=1.0,
        description="Legacy speckle level modulator"
    )
    contrast: float = Field(
        default=1.0, ge=0.2, le=3.0,
        description="Visual contrast scaling factor applied to dynamic range window"
    )
    db_lo: float = Field(
        default=-35.0, ge=-80.0, le=0.0,
        description="Log-display window lower bound (dB). Intensities at or below this level appear black."
    )
    db_hi: float = Field(
        default=-2.0, ge=-30.0, le=10.0,
        description="Log-display window upper bound (dB). Intensities at or above this level appear white."
    )
    display_gamma: float = Field(
        default=1.55, ge=0.5, le=4.0,
        description="Display gamma applied after log-window clipping (gamma > 1 darkens midtones)."
    )
    blur: float = Field(
        default=0.55, ge=0.0, le=5.0,
        description="Post-processing anti-aliasing / detector blur sigma in pixels"
    )
    axial_blur: float = Field(
        default=2.1, ge=0.1, le=10.0,
        description="Axial PSF coherence blur kernel sigma in pixels"
    )
    lateral_blur: float = Field(
        default=0.9, ge=0.1, le=10.0,
        description="Lateral PSF coherence blur kernel sigma in pixels"
    )
    background_noise: float = Field(
        default=-31.5, ge=-60.0, le=-10.0,
        description="Additive receiver background noise floor in dB"
    )
    noise_floor_db: float = Field(
        default=-31.5, ge=-60.0, le=-10.0,
        description="Alias for background_noise"
    )
    intensity_nonuniformity: float = Field(
        default=0.16, ge=0.0, le=1.0,
        description="Amplitude of slow-varying multiplicative tissue reflectivity heterogeneity field"
    )
    boundary_uncertainty: float = Field(
        default=0.0, ge=0.0, le=10.0,
        description="High-frequency jitter amplitude (pixels) for boundary position uncertainty"
    )
    attenuation_factor: float = Field(
        default=0.002, ge=0.0, le=0.05,
        description="[Legacy] Global axial light attenuation coefficient"
    )

    @model_validator(mode="after")
    def validate_display_window(self) -> "RealismConfig":
        if self.db_hi <= self.db_lo:
            raise ValueError(
                f"Display window bounds invalid: db_hi ({self.db_hi} dB) must be strictly greater "
                f"than db_lo ({self.db_lo} dB)."
            )
        return self


# ==============================================================================
# SECTION 6: Artifacts Configuration
# ==============================================================================

ARTIFACT_TYPES = [
    "motion",
    "dropout",
    "shadowing",
    "saturation",
    "mirror",
    "poor_signal",
    "banding",
]


class ArtifactInstance(BaseModel):
    """A single optical or motion artifact instance with full parametric control."""
    model_config = ConfigDict(extra="ignore")

    type: str = Field(
        ...,
        description=(
            "Artifact type identifier. One of: motion, dropout, shadowing, "
            "saturation, mirror, poor_signal, banding."
        )
    )
    severity: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Overall severity/intensity of the artifact (0=minimal, 1=maximal)"
    )
    location: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Lateral position center across B-scan width (fraction [0, 1])"
    )
    size: float = Field(
        default=0.15, ge=0.01, le=1.0,
        description="Lateral width or spatial span of the artifact (fraction of width)"
    )
    count: int = Field(
        default=1, ge=1, le=50,
        description="Count of lines/elements (e.g. banding frequency or dropout count)"
    )
    distribution: float = Field(
        default=0.10, ge=0.01, le=0.5,
        description="Spatial dispersion spread sigma for multi-element artifacts"
    )
    extent: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Axial extent, displacement amplitude, or depth fraction [0, 1]"
    )
    asymmetry: float = Field(
        default=0.0, ge=-1.0, le=1.0,
        description="Directional skew or displacement sign (-1=left/up, +1=right/down)"
    )
    reflectivity: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Reflectivity or optical intensity factor of artifact feature"
    )
    seed_offset: int = Field(
        default=0, ge=0, le=2**31 - 1,
        description="Offset added to render_seed for this artifact's private RNG stream"
    )


class ArtifactsConfig(BaseModel):
    """Artifacts configuration holding a composable list of ArtifactInstance objects."""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(
        default=False,
        description="Master switch for applying optical and motion artifacts"
    )
    instances: List[ArtifactInstance] = Field(
        default_factory=list,
        description="Ordered list of ArtifactInstance objects to apply"
    )
    shadowing: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="[Legacy] Shadowing region specifications"
    )
    motion_artifacts: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="[Legacy] Motion displacement artifact specifications"
    )


# ==============================================================================
# SECTION 7: Root Scene Configuration
# ==============================================================================

class SceneConfig(BaseModel):
    """Canonical root configuration containing all anatomy, pathology, acquisition, realism, and artifact parameters."""
    model_config = ConfigDict(extra="ignore")

    meta: MetaConfig = Field(default_factory=MetaConfig)
    anatomy: AnatomyConfig = Field(default_factory=AnatomyConfig)
    pathology: PathologyConfig = Field(default_factory=PathologyConfig)
    acquisition: AcquisitionConfig = Field(default_factory=AcquisitionConfig)
    realism: RealismConfig = Field(default_factory=RealismConfig)
    artifacts: ArtifactsConfig = Field(default_factory=ArtifactsConfig)

    @model_validator(mode="after")
    def validate_cross_field_consistency(self) -> "SceneConfig":
        # Check fovea_center within scan image extent
        w = self.acquisition.width
        h = self.acquisition.height
        if self.anatomy.fovea_center < 0 or self.anatomy.fovea_center > w * 2.0:
            raise ValueError(
                f"Fovea center ({self.anatomy.fovea_center} px) is unreasonably far outside image width ({w} px)."
            )
        # Check foveal pit depth vs image height
        if self.anatomy.fovea_pit_depth >= h:
            raise ValueError(
                f"Fovea pit depth ({self.anatomy.fovea_pit_depth} px) exceeds image height ({h} px)."
            )
        return self


# ==============================================================================
# SECTION 8: Visualization Configuration & Models
# ==============================================================================

class LayerStyle(BaseModel):
    """Per-layer visual styling override."""
    model_config = ConfigDict(extra="ignore")

    color: Optional[str] = Field(default=None, description="Hex color string (e.g. #FF9800)")
    opacity: float = Field(default=0.6, ge=0.0, le=1.0, description="Layer opacity [0.0, 1.0]")
    visible: bool = Field(default=True, description="Whether the layer overlay is visible")


class VisualizationConfig(BaseModel):
    """Configuration for OCT visualization styling and colormaps."""
    model_config = ConfigDict(extra="ignore")

    mode: Literal["grayscale", "colored_layer", "hybrid", "educational", "layers"] = Field(
        default="colored_layer",
        description="Visualization mode: grayscale, colored_layer (layers), hybrid, educational"
    )
    palette: Literal["clinical", "colorblind_safe", "educational", "custom"] = Field(
        default="clinical",
        description="Palette preset name: clinical, colorblind_safe, educational, custom"
    )
    layers: Dict[str, LayerStyle] = Field(
        default_factory=dict,
        description="Per-layer style overrides (keyed by layer name, e.g. RNFL, GCL, RPE)"
    )
    global_opacity: float = Field(
        default=0.6, ge=0.0, le=1.0,
        description="Global opacity multiplier for layer color overlays"
    )
    highlight_pathology: bool = Field(
        default=True,
        description="In hybrid mode, whether pathological features and fluid are highlighted"
    )


class VisualizationRequest(BaseModel):
    """Request payload for POST /api/visualize."""
    model_config = ConfigDict(extra="ignore")

    render_id: str = Field(..., description="Stable render reference id from previous render call")
    config: VisualizationConfig = Field(
        default_factory=VisualizationConfig,
        description="Visualization configuration"
    )


class VisualizationResponse(BaseModel):
    """Response payload for POST /api/visualize."""
    status: str = Field(default="success", description="Response status")
    render_id: str = Field(..., description="Render reference id")
    image: str = Field(..., description="Base64 encoded PNG data URI of styled image")
    mode: str = Field(..., description="Active visualization mode")
    palette: str = Field(..., description="Active palette preset")
    width: int = Field(..., description="Image width in pixels")
    height: int = Field(..., description="Image height in pixels")


# ==============================================================================
# SECTION 9: Annotation Configuration & Models
# ==============================================================================

class AnnotationItem(BaseModel):
    """A single structured annotation shape primitive."""
    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = Field(default=None, description="Optional unique identifier")
    type: Literal["text", "leader_line", "arrow", "dot", "numbered_marker", "bracket", "reference_line", "caliper", "legend"] = Field(
        ...,
        description="Shape primitive type"
    )
    x: Optional[float] = Field(default=None, description="Anchor X-coordinate (pixels)")
    y: Optional[float] = Field(default=None, description="Anchor Y-coordinate / depth Z (pixels)")
    x2: Optional[float] = Field(default=None, description="Secondary X-coordinate for lines/arrows/brackets")
    y2: Optional[float] = Field(default=None, description="Secondary Y-coordinate for lines/arrows/brackets")
    width: Optional[float] = Field(default=None, description="Box width (pixels) for text boxes")
    height: Optional[float] = Field(default=None, description="Box height (pixels) for text boxes")
    rotation: Optional[float] = Field(default=None, description="Rotation in degrees for text boxes")
    text: Optional[str] = Field(default=None, description="Text content for text/marker annotations")
    label: Optional[str] = Field(default=None, description="Short label identifier")
    color: Optional[str] = Field(default="#FFFFFF", description="Stroke / foreground hex color")
    fill_color: Optional[str] = Field(default=None, description="Fill hex color")
    font_size: Optional[int] = Field(default=12, ge=6, le=72, description="Font size in points")
    stroke_width: Optional[float] = Field(default=1.5, ge=0.5, le=10.0, description="Stroke line width (px)")
    dashed: Optional[bool] = Field(default=False, description="Whether line is dashed")
    radius: Optional[float] = Field(default=None, ge=1.0, le=100.0, description="Radius for dot/marker (px)")
    layer: Optional[str] = Field(default=None, description="Associated layer name (if applicable)")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Custom metadata dictionary")


class AnnotationSet(BaseModel):
    """Collection of structured annotations and optional legend."""
    model_config = ConfigDict(extra="ignore")

    items: List[AnnotationItem] = Field(default_factory=list, description="List of annotation items")
    legend: Optional[List[Dict[str, Any]]] = Field(default=None, description="Generated legend list")


class AnnotateRequest(BaseModel):
    """Request payload for POST /api/annotate."""
    model_config = ConfigDict(extra="ignore")

    render_id: str = Field(..., description="Stable render reference id from previous render call")
    annotations: Optional[AnnotationSet] = Field(default=None, description="Optional user annotation set to validate/enrich")
    auto_label: bool = Field(default=False, description="Whether to generate automatic layer labeling from layer_id_map")
    auto_label_x: Optional[float] = Field(default=None, description="Lateral column index for auto-label probing")
    palette: Optional[str] = Field(default="clinical", description="Palette to use for auto-label colors")


class AnnotateResponse(BaseModel):
    """Response payload for POST /api/annotate."""
    status: str = Field(default="success", description="Response status")
    render_id: str = Field(..., description="Render reference id")
    annotations: AnnotationSet = Field(..., description="Validated and auto-generated annotation set")


# ==============================================================================
# SECTION 10: Measurement Configuration & Models
# ==============================================================================

class Point2D(BaseModel):
    """A 2D coordinate point in image pixel space."""
    x: float = Field(..., description="Horizontal coordinate (pixels, 0 = left)")
    y: float = Field(..., description="Vertical depth coordinate (pixels, 0 = top)")


class CaliperRequest(BaseModel):
    """A manual point-to-point distance measurement request."""
    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = Field(default=None, description="Caliper identifier")
    label: Optional[str] = Field(default=None, description="User label for caliper measurement")
    p1: Point2D = Field(..., description="Start point in image pixel coordinates")
    p2: Point2D = Field(..., description="End point in image pixel coordinates")


class CaliperResult(BaseModel):
    """Calculated physical caliper measurement."""
    id: Optional[str] = Field(default=None, description="Caliper identifier")
    label: Optional[str] = Field(default=None, description="User label")
    p1: Point2D = Field(..., description="Start point in image pixel coordinates")
    p2: Point2D = Field(..., description="End point in image pixel coordinates")
    pixel_distance: float = Field(..., description="Euclidean distance in image pixels")
    dx_pixels: float = Field(..., description="Lateral distance in pixels")
    dz_pixels: float = Field(..., description="Axial distance in pixels")
    physical_distance_um: float = Field(..., description="Physical distance in micrometers (μm)")
    dx_um: float = Field(..., description="Lateral physical distance in micrometers (μm)")
    dz_um: float = Field(..., description="Axial physical distance in micrometers (μm)")
    physical_distance_mm: float = Field(..., description="Physical distance in millimeters (mm)")
    angle_deg: float = Field(..., description="Angle in degrees (0 = horizontal right, 90 = downward)")


class LayerThicknessMetric(BaseModel):
    """Thickness statistics for a single anatomical layer."""
    layer: str = Field(..., description="Layer name (e.g. RNFL, GCL, RPE)")
    mean_thickness_um: float = Field(..., description="Mean layer thickness across scan in μm")
    min_thickness_um: float = Field(..., description="Minimum layer thickness in μm")
    max_thickness_um: float = Field(..., description="Maximum layer thickness in μm")
    foveal_thickness_um: float = Field(..., description="Layer thickness at foveal center in μm")
    parafoveal_thickness_um: float = Field(..., description="Average parafoveal layer thickness in μm")
    mean_thickness_px: float = Field(..., description="Mean layer thickness in image pixels")
    profile_um: Optional[List[float]] = Field(default=None, description="Column-by-column thickness profile in μm")


class FluidRegionMetric(BaseModel):
    """Morphological measurement of a fluid or pathological lesion region."""
    id: int = Field(..., description="Unique lesion / region index")
    layer_id: int = Field(..., description="Associated tissue or layer ID")
    pathology_type: Optional[str] = Field(default=None, description="Pathology classification type")
    x_min_px: float = Field(..., description="Bounding box minimum X in pixels")
    x_max_px: float = Field(..., description="Bounding box maximum X in pixels")
    z_min_px: float = Field(..., description="Bounding box minimum Z in pixels")
    z_max_px: float = Field(..., description="Bounding box maximum Z in pixels")
    width_px: float = Field(..., description="Lesion lateral width in pixels")
    height_px: float = Field(..., description="Lesion axial height in pixels")
    width_um: float = Field(..., description="Lesion lateral width in micrometers (μm)")
    height_um: float = Field(..., description="Lesion axial height in micrometers (μm)")
    area_px: float = Field(..., description="Area in pixels")
    area_um2: float = Field(..., description="Area in square micrometers (μm²)")
    area_mm2: float = Field(..., description="Area in square millimeters (mm²)")
    centroid_x_px: float = Field(..., description="Centroid X in pixels")
    centroid_z_px: float = Field(..., description="Centroid Z in pixels")
    centroid_x_mm: float = Field(..., description="Centroid lateral position in mm across scan")
    centroid_z_mm: float = Field(..., description="Centroid axial depth in mm")


class FovealMetrics(BaseModel):
    """Morphometric readouts of the foveal pit."""
    fovea_center_x_px: float = Field(..., description="Fovea center X in pixels")
    fovea_center_x_mm: float = Field(..., description="Fovea center lateral position in mm")
    central_foveal_thickness_um: float = Field(..., description="Central Foveal Thickness (CFT) in μm (ILM to BM)")
    central_foveal_thickness_px: float = Field(..., description="Central Foveal Thickness in pixels")
    foveal_pit_depth_um: float = Field(..., description="Foveal pit depression depth in μm")
    foveal_pit_depth_px: float = Field(..., description="Foveal pit depression depth in pixels")
    foveal_pit_width_um: float = Field(..., description="Foveal pit FWHM width in μm")
    foveal_pit_width_mm: float = Field(..., description="Foveal pit FWHM width in mm")


class ScaleBar(BaseModel):
    """Scale bar geometry and physical calibration."""
    orientation: Literal["lateral", "axial"] = Field(..., description="Scale bar orientation")
    length_px: float = Field(..., description="Bar length in pixels")
    length_physical: float = Field(..., description="Physical calibration value")
    unit: str = Field(..., description="Physical unit (e.g. mm or μm)")
    label: str = Field(..., description="Formatted label (e.g. 1 mm or 200 μm)")
    start_px: Point2D = Field(..., description="Start point in image pixel coordinates")
    end_px: Point2D = Field(..., description="End point in image pixel coordinates")


class ScaleBars(BaseModel):
    """Set of lateral and axial scale bars calibrated to scan geometry."""
    lateral: ScaleBar = Field(..., description="Lateral (horizontal) scale bar")
    axial: ScaleBar = Field(..., description="Axial (vertical) scale bar")


class MeasurementSet(BaseModel):
    """Complete quantitative geometric measurement suite derived from registered maps."""
    scan_width_mm: float = Field(..., description="Scan lateral width in mm")
    scan_depth_mm: float = Field(..., description="Scan axial depth in mm")
    width_px: int = Field(..., description="B-scan width in pixels")
    height_px: int = Field(..., description="B-scan height in pixels")
    pixel_width_um: float = Field(..., description="Lateral pixel resolution in μm/pixel")
    pixel_height_um: float = Field(..., description="Axial pixel resolution in μm/pixel")
    central_retinal_thickness_um: float = Field(..., description="Central retinal thickness (CFT) in μm")
    total_retinal_thickness_mean_um: float = Field(..., description="Mean total retinal thickness in μm")
    total_retinal_thickness_min_um: float = Field(..., description="Minimum total retinal thickness in μm")
    total_retinal_thickness_max_um: float = Field(..., description="Maximum total retinal thickness in μm")
    foveal_metrics: FovealMetrics = Field(..., description="Foveal pit morphology metrics")
    layer_thicknesses: Dict[str, LayerThicknessMetric] = Field(..., description="Per-layer thickness metrics")
    fluid_regions: List[FluidRegionMetric] = Field(default_factory=list, description="Fluid and lesion region metrics")
    scale_bars: ScaleBars = Field(..., description="Calibrated scale bars")
    calipers: List[CaliperResult] = Field(default_factory=list, description="Manual caliper measurements")


class MeasureRequest(BaseModel):
    """Request payload for POST /api/measure."""
    model_config = ConfigDict(extra="ignore")

    render_id: str = Field(..., description="Stable render reference id from previous render call")
    calipers: Optional[List[CaliperRequest]] = Field(default=None, description="Optional manual caliper measurements to compute")
    include_profiles: bool = Field(default=False, description="Whether to include full column-by-column thickness vectors")


class MeasureResponse(BaseModel):
    """Response payload for POST /api/measure."""
    status: str = Field(default="success", description="Response status")
    render_id: str = Field(..., description="Render reference id")
    measurements: MeasurementSet = Field(..., description="Quantitative measurement dataset")


# ==============================================================================
# SECTION 11: Composition Configuration & Models
# ==============================================================================

class PanelConfig(BaseModel):
    """Configuration for a single panel within a multi-panel figure composition."""
    model_config = ConfigDict(extra="ignore")

    render_id: str = Field(..., description="Render reference id for this panel")
    label: Optional[str] = Field(default=None, description="Panel identifier/badge (e.g. 'A', 'B', 'Control', 'Disease')")
    title: Optional[str] = Field(default=None, description="Panel subtitle or clinical heading")
    visualization: Optional[VisualizationConfig] = Field(
        default=None,
        description="Optional visualization styling override for this panel"
    )
    annotations: Optional[AnnotationSet] = Field(
        default=None,
        description="Optional custom annotation primitives for this panel"
    )
    auto_label: bool = Field(
        default=False,
        description="Whether to generate automatic layer annotations for this panel"
    )
    auto_label_x: Optional[float] = Field(
        default=None,
        description="Lateral X-coordinate for automatic layer labeling probe"
    )
    palette: Optional[str] = Field(
        default=None,
        description="Palette preset for auto-labeling (clinical, colorblind_safe, educational)"
    )
    calipers: Optional[List[CaliperRequest]] = Field(
        default=None,
        description="Optional point-to-point caliper measurements to compute and draw"
    )
    show_scale_bar: bool = Field(
        default=False,
        description="Whether to draw calibrated lateral/axial scale bars on this panel"
    )
    scale_bar_color: str = Field(
        default="#FFFFFF",
        description="Hex color for scale bar markings"
    )


class CompositionConfig(BaseModel):
    """Configuration for assembling multi-panel educational or publication figures."""
    model_config = ConfigDict(extra="ignore")

    title: Optional[str] = Field(default=None, description="Main figure title")
    subtitle: Optional[str] = Field(default=None, description="Figure subtitle / description")
    panels: List[PanelConfig] = Field(
        default_factory=list,
        description="Ordered list of panels to arrange in the figure"
    )
    layout: str = Field(
        default="grid",
        description="Layout mode: 'single', '1x2', '2x1', '1x3', '3x1', '2x2', '1x4', '4x1', 'A-B', 'A-B-C', 'A-B-C-D', 'grid'"
    )
    grid_columns: Optional[int] = Field(
        default=None, ge=1, le=8,
        description="Explicit grid column count override"
    )
    spacing: int = Field(
        default=20, ge=0, le=200,
        description="Inter-panel spacing in pixels"
    )
    padding: int = Field(
        default=30, ge=0, le=200,
        description="Outer canvas border padding in pixels"
    )
    background_color: str = Field(
        default="#121212",
        description="Background hex color for canvas"
    )
    text_color: str = Field(
        default="#EEEEEE",
        description="Text hex color for figure titles and labels"
    )
    font_size_title: int = Field(default=22, ge=8, le=72, description="Figure title font size (pt)")
    font_size_subtitle: int = Field(default=14, ge=8, le=48, description="Figure subtitle font size (pt)")
    font_size_panel_label: int = Field(default=18, ge=8, le=48, description="Panel badge font size (pt)")
    font_size_panel_title: int = Field(default=13, ge=8, le=36, description="Panel title font size (pt)")
    show_legend: bool = Field(default=False, description="Whether to include a layer color legend bar")
    legend_palette: str = Field(default="clinical", description="Palette for legend items")
    show_metadata_block: bool = Field(default=False, description="Whether to render a synthetic metadata banner")
    metadata_block_text: Optional[str] = Field(default=None, description="Custom metadata banner text")


class PanelLayoutInfo(BaseModel):
    """Calculated layout coordinates for a panel within the composed canvas."""
    index: int = Field(..., description="0-indexed panel sequence index")
    label: Optional[str] = Field(default=None, description="Panel label badge")
    title: Optional[str] = Field(default=None, description="Panel title")
    render_id: str = Field(..., description="Source render reference id")
    x: int = Field(..., description="Panel left position on composed canvas (pixels)")
    y: int = Field(..., description="Panel top position on composed canvas (pixels)")
    width: int = Field(..., description="Panel pixel width")
    height: int = Field(..., description="Panel pixel height")


class CompositionRequest(BaseModel):
    """Request payload for POST /api/compose."""
    model_config = ConfigDict(extra="ignore")

    config: CompositionConfig = Field(..., description="Composition configuration")


class CompositionResponse(BaseModel):
    """Response payload for POST /api/compose."""
    status: str = Field(default="success", description="Composition status")
    image: str = Field(..., description="Base64 encoded PNG data URI of composed figure")
    width: int = Field(..., description="Total composed image width (pixels)")
    height: int = Field(..., description="Total composed image height (pixels)")
    panel_count: int = Field(..., description="Total number of rendered panels")
    panels: List[PanelLayoutInfo] = Field(default_factory=list, description="Computed panel bounding boxes")
    config: CompositionConfig = Field(..., description="Applied composition configuration")


# ==============================================================================
# SECTION 12: Export Configuration & Models
# ==============================================================================

class ExportConfig(BaseModel):
    """Export configuration specifying technical output format, DPI, and metadata."""
    model_config = ConfigDict(extra="ignore")

    preset: str = Field(
        default="custom",
        description="Export preset: 'lecture', 'powerpoint', 'a4', 'publication', 'web', 'high_resolution', 'low_resource', 'custom'"
    )
    format: str = Field(
        default="png",
        description="Target output format: 'png', 'jpeg', 'tiff', 'pdf', 'svg'"
    )
    dpi: int = Field(
        default=300, ge=72, le=1200,
        description="Print / publication target DPI (Dots Per Inch)"
    )
    width: Optional[int] = Field(
        default=None, ge=64, le=8192,
        description="Optional target width in pixels (resizes preserving aspect ratio if height omitted)"
    )
    height: Optional[int] = Field(
        default=None, ge=64, le=8192,
        description="Optional target height in pixels"
    )
    color_mode: str = Field(
        default="RGB",
        description="Color mode: 'RGB', 'L' (grayscale), 'RGBA'"
    )
    quality: int = Field(
        default=95, ge=1, le=100,
        description="Encoding quality for lossy formats (JPEG/TIFF)"
    )
    compression: Optional[str] = Field(
        default="lzw",
        description="Compression algorithm for TIFF ('none', 'lzw', 'deflate', 'packbits') or PNG ('optimize')"
    )
    include_annotations: bool = Field(
        default=True,
        description="Whether to bake / embed annotations and labels"
    )
    include_measurements: bool = Field(
        default=True,
        description="Whether to bake / embed calipers and measurements"
    )
    include_scale_bars: bool = Field(
        default=True,
        description="Whether to bake / embed calibrated scale bars"
    )
    watermark: bool = Field(
        default=False,
        description="Whether to render an on-image visual watermark banner"
    )
    watermark_text: str = Field(
        default="SYNTHETIC — FOR EDUCATION ONLY",
        description="Watermark text banner string"
    )
    custom_metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional custom key-value metadata to embed"
    )


class ExportRequest(BaseModel):
    """Request payload for POST /api/export."""
    model_config = ConfigDict(extra="ignore")

    render_id: Optional[str] = Field(
        default=None,
        description="Stable render reference id (for single-image export)"
    )
    visualization: Optional[VisualizationConfig] = Field(
        default=None,
        description="Visualization configuration (for single-image export)"
    )
    annotations: Optional[AnnotationSet] = Field(
        default=None,
        description="Annotations to overlay (for single-image export)"
    )
    measurements: Optional[MeasurementSet] = Field(
        default=None,
        description="Measurements to overlay (for single-image export)"
    )
    composition: Optional[CompositionConfig] = Field(
        default=None,
        description="Full composition configuration (if exporting a multi-panel figure)"
    )
    config: ExportConfig = Field(
        default_factory=ExportConfig,
        description="Export format and technical properties"
    )


class ExportResponse(BaseModel):
    """Response payload for POST /api/export."""
    status: str = Field(default="success", description="Export execution status")
    format: str = Field(..., description="Exported file format (e.g. 'png', 'tiff', 'pdf', 'svg')")
    mime_type: str = Field(..., description="MIME content type")
    filename: str = Field(..., description="Suggested download filename")
    data: str = Field(..., description="Base64 encoded file data URI or SVG string")
    width: int = Field(..., description="Exported image/document width in pixels / points")
    height: int = Field(..., description="Exported image/document height in pixels / points")
    dpi: int = Field(..., description="Configured DPI")
    file_size_bytes: int = Field(..., description="Size of exported binary artifact in bytes")
    metadata: Dict[str, Any] = Field(..., description="Embedded technical and clinical metadata summary")


# ==============================================================================
# SECTION 13: Presets & Randomization Models
# ==============================================================================

class PathologyPresetInfo(BaseModel):
    """Metadata and SceneConfig template for a clinical pathology preset."""
    id: str = Field(..., description="Preset unique key identifier")
    name: str = Field(..., description="Clinical title (e.g. 'Cystoid Macular Edema')")
    category: str = Field(..., description="Clinical category (e.g. 'Fluid-Space', 'RPE-Level', 'Hole')")
    description: str = Field(..., description="Clinical pathomorphological description")
    scene: SceneConfig = Field(..., description="Full schema-valid SceneConfig instance")


class ExportPresetInfo(BaseModel):
    """Metadata and ExportConfig template for an export preset."""
    id: str = Field(..., description="Preset key identifier (e.g. 'lecture', 'publication')")
    name: str = Field(..., description="Preset human-readable display name")
    description: str = Field(..., description="Target medium description")
    config: ExportConfig = Field(..., description="Concrete ExportConfig instance")


class RandomizeSceneRequest(BaseModel):
    """Request payload for POST /api/presets/randomize."""
    model_config = ConfigDict(extra="ignore")

    base_scene: Optional[SceneConfig] = Field(
        default=None,
        description="Optional base SceneConfig to perturb (if None, perturbs normal anatomy)"
    )
    seed: Optional[int] = Field(
        default=None,
        description="Explicit deterministic integer seed for the randomized output"
    )
    mode: str = Field(
        default="balanced",
        description="Randomization scope: 'anatomy_only', 'realism_only', 'artifacts_only', 'all', 'balanced'"
    )
    variance: float = Field(
        default=0.25, ge=0.05, le=1.0,
        description="Perturbation amplitude fraction relative to parameter bounds"
    )


class PathologyVariantsRequest(BaseModel):
    """Request payload for POST /api/presets/variants."""
    model_config = ConfigDict(extra="ignore")

    preset_id: Optional[str] = Field(
        default="cme",
        description="Pathology preset identifier (e.g. 'cme', 'drusen', 'srf', 'ped', 'macular_hole')"
    )
    base_scene: Optional[SceneConfig] = Field(
        default=None,
        description="Optional custom base SceneConfig to generate variants from"
    )
    count: int = Field(
        default=4, ge=1, le=16,
        description="Number of distinct pathology variants to generate"
    )
    base_seed: Optional[int] = Field(
        default=42,
        description="Base integer seed for reproducible variant sequence"
    )


class VariantsResponse(BaseModel):
    """Response payload for POST /api/presets/variants."""
    status: str = Field(default="success", description="Generation status")
    count: int = Field(..., description="Number of variants generated")
    variants: List[SceneConfig] = Field(..., description="List of schema-valid SceneConfig variants with reproducible seeds")


# ==============================================================================
# SECTION 14: Project Persistence Models
# ==============================================================================

class Project(BaseModel):
    """Full self-contained project data structure supporting complete session reproducibility."""
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Unique project identifier (UUID)")
    name: str = Field(default="Untitled Project", description="User-visible project title")
    description: str = Field(default="", description="Project notes or clinical vignette")
    octforge_version: str = Field(default="1.0.0", description="OCTForge application/schema version tag")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    updated_at: str = Field(..., description="ISO 8601 last-modified timestamp")
    scene: SceneConfig = Field(default_factory=SceneConfig, description="Current scene specification")
    visualization: VisualizationConfig = Field(default_factory=VisualizationConfig, description="Current visualization styling")
    annotations: AnnotationSet = Field(default_factory=AnnotationSet, description="Current annotation primitives")
    measurements: Optional[MeasurementSet] = Field(default=None, description="Last computed quantitative measurements")
    composition: Optional[CompositionConfig] = Field(default=None, description="Multi-panel composition settings")
    export: Optional[ExportConfig] = Field(default=None, description="Last used export configuration")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Custom project tags and application state")


class ProjectSummary(BaseModel):
    """Lightweight metadata descriptor for project list views."""
    id: str = Field(..., description="Project unique identifier")
    name: str = Field(..., description="Project name")
    description: str = Field(default="", description="Project description")
    octforge_version: str = Field(default="1.0.0", description="Schema version")
    created_at: str = Field(..., description="Creation ISO timestamp")
    updated_at: str = Field(..., description="Last modified ISO timestamp")
    pathology_summary: str = Field(default="Normal Macula", description="Brief pathology summary")
    render_seed: int = Field(default=42, description="Anatomy render seed")


class ProjectCreateRequest(BaseModel):
    """Request payload for POST /api/projects."""
    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = Field(default="Untitled Project", description="New project name")
    description: Optional[str] = Field(default="", description="Project description")
    scene: Optional[SceneConfig] = Field(default=None, description="Initial SceneConfig (defaults to normal macula)")
    visualization: Optional[VisualizationConfig] = Field(default=None, description="Initial VisualizationConfig")
    annotations: Optional[AnnotationSet] = Field(default=None, description="Initial AnnotationSet")
    composition: Optional[CompositionConfig] = Field(default=None, description="Initial CompositionConfig")


class ProjectRenameRequest(BaseModel):
    """Request payload for PATCH /api/projects/{id}/rename."""
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, description="New project title")
    description: Optional[str] = Field(default=None, description="Optional updated description")

