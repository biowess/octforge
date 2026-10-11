"""OCTForge Backend API Router.

ALL API route handlers live in this single file, organized into section comments.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import numpy as np
from PIL import Image
import io
import base64

from backend.app.schema import (
    SceneConfig,
    VisualizationRequest,
    VisualizationResponse,
    AnnotateRequest,
    AnnotateResponse,
    MeasureRequest,
    MeasureResponse,
    CompositionConfig,
    CompositionRequest,
    CompositionResponse,
    ExportConfig,
    ExportRequest,
    ExportResponse,
    ExportPresetInfo,
    PathologyPresetInfo,
    RandomizeSceneRequest,
    PathologyVariantsRequest,
    VariantsResponse,
    Project,
    ProjectSummary,
    ProjectCreateRequest,
    ProjectRenameRequest,
)
from backend.app.rendering.forward_model import (
    render_scene,
    render_bscan_base64,
)
from backend.app.rendering.cache import render_cache, RenderEntry
from backend.app.presentation import (
    generate_visualization,
    generate_annotations,
    generate_measurements,
)
from backend.app.composition import (
    compose_scene_api,
)
from backend.app.export import (
    export_scene_api,
    list_export_presets,
    get_export_preset,
)
from backend.app.presets import (
    list_pathology_presets,
    get_pathology_preset,
    randomize_scene,
    generate_pathology_variants,
)
from backend.app.projects import (
    list_projects,
    get_project,
    save_project,
    create_project,
    duplicate_project,
    rename_project,
    reset_project,
    delete_project,
)

router = APIRouter()


# ==============================================================================
# Response Models
# ==============================================================================

class HealthResponse(BaseModel):
    status: str = Field(default="ok", description="Server status indicator")
    version: str = Field(default="1.0.0", description="Backend API version")


class PreviewRenderResponse(BaseModel):
    status: str = Field(default="success", description="Render status")
    render_id: str = Field(..., description="Stable render reference id for downstream endpoints")
    cache_key: str = Field(..., description="SHA-256 cache key for SceneConfig (same as render_id)")
    image: str = Field(..., description="Base64 encoded PNG data URI")
    cached: bool = Field(..., description="Whether result was served from LRU cache")
    width: int = Field(..., description="Image width in pixels")
    height: int = Field(..., description="Image height in pixels")


class FinalRenderResponse(BaseModel):
    """Full-quality render response including geometry ground truth.

    The boundary_curves and layer_id_map fields carry the stable internal
    contract and the measurement system.
    """
    status: str = Field(default="success", description="Render status")
    render_id: str = Field(..., description="Stable render reference id for downstream endpoints")
    cache_key: str = Field(..., description="SHA-256 cache key for SceneConfig (same as render_id)")
    image: str = Field(..., description="Base64 encoded PNG data URI (full quality)")
    cached: bool = Field(..., description="Whether result was served from LRU cache")
    width: int = Field(..., description="Image width in pixels")
    height: int = Field(..., description="Image height in pixels")
    boundary_curves: Dict[str, List[float]] = Field(
        ...,
        description=(
            "Per-boundary axial depth curves as lists of float (one value per A-scan column). "
            "Keys: ILM, RNFL_bot, GCL_bot, IPL_bot, INL_bot, OPL_bot, ONL_bot, "
            "EZ_bot, RPE_bot, BM, CSI."
        )
    )
    vessel_info: List[Dict[str, Any]] = Field(
        ...,
        description=(
            "List of retinal vessel descriptors. Each dict has: "
            "x_center, radius_x, radius_z, z_center, shadow_tau, top_z, bot_z."
        )
    )
    layer_id_map: Optional[List[List[int]]] = Field(
        default=None,
        description=(
            "Per-pixel layer ID map as a 2-D list (height x width). "
            "Layer IDs: 0=VITREOUS 1=RNFL 2=GCL 3=IPL 4=INL 5=OPL 6=ONL "
            "7=EZ 8=RPE 9=CHOROID 10=SCLERA 11=VESSEL."
        )
    )


# ==============================================================================
# SECTION 1: System & Health Routes
# ==============================================================================

@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint confirming backend operational status."""
    return HealthResponse(status="ok", version="1.0.0")


# ==============================================================================
# SECTION 2: Rendering & Preview Routes
# ==============================================================================

@router.post("/render/preview", response_model=PreviewRenderResponse, tags=["Rendering"])
async def render_preview(scene: SceneConfig):
    """Render a fast preview image from SceneConfig, or return from LRU cache.

    Uses reduced speckle-average count for speed (preview=True in render_scene).
    Caches the full render entry and returns a stable render_id for downstream
    visualization, annotation, and measurement calls.
    """
    try:
        render_id = render_cache.compute_hash(scene, preview=True)
        cached_entry = render_cache.get(render_id)

        if cached_entry is not None:
            return PreviewRenderResponse(
                status="success",
                render_id=render_id,
                cache_key=render_id,
                image=cached_entry.image_b64,
                cached=True,
                width=cached_entry.width,
                height=cached_entry.height,
            )

        pixels, layer_id_map, curves, vessel_info = render_scene(scene, preview=True)

        # Encode image to base64 PNG
        pil_img = Image.fromarray(pixels, mode="L")
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        raw_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        img_b64 = f"data:image/png;base64,{raw_b64}"

        entry = RenderEntry(
            render_id=render_id,
            scene=scene,
            pixels=pixels,
            layer_id_map=layer_id_map,
            boundary_curves=curves,
            vessel_info=vessel_info,
            image_b64=img_b64,
            preview=True,
            width=scene.acquisition.width,
            height=scene.acquisition.height,
        )
        render_cache.put(render_id, entry)

        return PreviewRenderResponse(
            status="success",
            render_id=render_id,
            cache_key=render_id,
            image=img_b64,
            cached=False,
            width=scene.acquisition.width,
            height=scene.acquisition.height,
        )
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Rendering failure: {str(err)}")


@router.post("/render/final", response_model=FinalRenderResponse, tags=["Rendering"])
async def render_final(scene: SceneConfig):
    """Full-quality render returning image, layer boundaries, and vessel metadata.

    Uses the full speckle_avg_count from RealismConfig for highest quality.
    Stores the full RenderEntry in LRU cache and returns a stable render_id.
    """
    try:
        render_id = render_cache.compute_hash(scene, preview=False)
        cached_entry = render_cache.get(render_id)

        if cached_entry is not None:
            return FinalRenderResponse(
                status="success",
                render_id=render_id,
                cache_key=render_id,
                image=cached_entry.image_b64,
                cached=True,
                width=cached_entry.width,
                height=cached_entry.height,
                boundary_curves={k: v.tolist() for k, v in cached_entry.boundary_curves.items()},
                vessel_info=cached_entry.vessel_info,
                layer_id_map=cached_entry.layer_id_map.tolist(),
            )

        pixels, layer_id_map, curves, vessel_info = render_scene(scene, preview=False)

        pil_img = Image.fromarray(pixels, mode="L")
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        raw_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        img_b64 = f"data:image/png;base64,{raw_b64}"

        entry = RenderEntry(
            render_id=render_id,
            scene=scene,
            pixels=pixels,
            layer_id_map=layer_id_map,
            boundary_curves=curves,
            vessel_info=vessel_info,
            image_b64=img_b64,
            preview=False,
            width=scene.acquisition.width,
            height=scene.acquisition.height,
        )
        render_cache.put(render_id, entry)

        return FinalRenderResponse(
            status="success",
            render_id=render_id,
            cache_key=render_id,
            image=img_b64,
            cached=False,
            width=scene.acquisition.width,
            height=scene.acquisition.height,
            boundary_curves={k: v.tolist() for k, v in curves.items()},
            vessel_info=vessel_info,
            layer_id_map=layer_id_map.tolist(),
        )
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Final render failure: {str(err)}")


# ==============================================================================
# SECTION 3: Presentation Routes (Visualize, Annotate, Measure)
# ==============================================================================

@router.post("/visualize", response_model=VisualizationResponse, tags=["Presentation"])
async def visualize_render(request: VisualizationRequest):
    """Apply visualization colormaps and opacity styling to an existing render reference.

    Operates strictly on the cached RenderEntry without triggering scene regeneration.
    """
    entry = render_cache.get(request.render_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Render reference '{request.render_id}' not found or expired. Please render first."
        )

    try:
        return generate_visualization(entry, request.config)
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Visualization failure: {str(err)}")


@router.post("/annotate", response_model=AnnotateResponse, tags=["Presentation"])
async def annotate_render(request: AnnotateRequest):
    """Generate structured annotation primitives and automatic layer labeling.

    Operates strictly on the cached RenderEntry without triggering scene regeneration.
    """
    entry = render_cache.get(request.render_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Render reference '{request.render_id}' not found or expired. Please render first."
        )

    try:
        palette_name = request.palette or "clinical"
        annot_set = generate_annotations(
            entry=entry,
            auto_label=request.auto_label,
            auto_label_x=request.auto_label_x,
            palette_name=palette_name,
            user_annotations=request.annotations,
        )
        return AnnotateResponse(
            status="success",
            render_id=request.render_id,
            annotations=annot_set,
        )
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Annotation failure: {str(err)}")


@router.post("/measure", response_model=MeasureResponse, tags=["Presentation"])
async def measure_render(request: MeasureRequest):
    """Derive accurate thickness, foveal morphology, fluid metrics, and scale bars.

    Operates strictly on the cached RenderEntry without triggering scene regeneration.
    """
    entry = render_cache.get(request.render_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Render reference '{request.render_id}' not found or expired. Please render first."
        )

    try:
        meas_set = generate_measurements(
            entry=entry,
            calipers=request.calipers,
            include_profiles=request.include_profiles,
        )
        return MeasureResponse(
            status="success",
            render_id=request.render_id,
            measurements=meas_set,
        )
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Measurement failure: {str(err)}")


# ==============================================================================
# SECTION 4: Figure Composition Routes
# ==============================================================================

@router.post("/compose", response_model=CompositionResponse, tags=["Composition"])
async def compose_figure_endpoint(request: CompositionRequest):
    """Assemble multi-panel educational/publication figures from existing render references.

    Operates strictly on cached render entries without triggering optical re-simulation.
    """
    try:
        return compose_scene_api(request.config)
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Composition failure: {str(err)}")


# ==============================================================================
# SECTION 5: Multi-Format Export Routes
# ==============================================================================

@router.post("/export", response_model=ExportResponse, tags=["Export"])
async def export_endpoint(request: ExportRequest):
    """Export single OCT images or multi-panel compositions to PNG, JPEG, TIFF, PDF, or SVG.

    Embeds full metadata, non-removable synthetic notice, and applies configured DPI and presets.
    """
    try:
        return export_scene_api(request)
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Export failure: {str(err)}")


# ==============================================================================
# SECTION 6: Presets & Randomization Routes
# ==============================================================================

@router.get("/presets/pathology", response_model=List[PathologyPresetInfo], tags=["Presets"])
async def get_pathology_presets():
    """List all curated clinical pathology presets with full SceneConfig specifications."""
    try:
        return list_pathology_presets()
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Failed to list pathology presets: {str(err)}")


@router.get("/presets/pathology/{preset_id}", response_model=SceneConfig, tags=["Presets"])
async def get_pathology_preset_by_id(preset_id: str):
    """Retrieve a full schema-valid SceneConfig for a specific pathology preset."""
    scene = get_pathology_preset(preset_id)
    if scene is None:
        raise HTTPException(
            status_code=404,
            detail=f"Pathology preset '{preset_id}' not found. Available presets: CME, SRF, Drusen, PED, ERM, Macular Hole, etc."
        )
    return scene


@router.get("/presets/export", response_model=List[ExportPresetInfo], tags=["Presets"])
async def get_export_presets():
    """List all available publication and presentation export presets."""
    try:
        return list_export_presets()
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Failed to list export presets: {str(err)}")


@router.post("/presets/randomize", response_model=SceneConfig, tags=["Randomization"])
async def randomize_scene_endpoint(request: RandomizeSceneRequest):
    """Perturb scene parameters within strictly bounded clinical & optical ranges.

    Maintains physiological plausibility, schema validity, and records a concrete render seed.
    """
    try:
        return randomize_scene(
            base_scene=request.base_scene,
            seed=request.seed,
            mode=request.mode,
            variance=request.variance,
        )
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Randomization failure: {str(err)}")


@router.post("/presets/variants", response_model=VariantsResponse, tags=["Randomization"])
async def generate_variants_endpoint(request: PathologyVariantsRequest):
    """Generate N distinct reproducible variants of a pathology preset or base scene."""
    try:
        if request.base_scene is not None:
            variants = generate_pathology_variants(
                preset_or_scene=request.base_scene,
                count=request.count,
                base_seed=request.base_seed,
            )
        else:
            preset_id = request.preset_id or "cme"
            variants = generate_pathology_variants(
                preset_or_scene=preset_id,
                count=request.count,
                base_seed=request.base_seed,
            )

        return VariantsResponse(
            status="success",
            count=len(variants),
            variants=variants,
        )
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Variant generation failure: {str(err)}")


# ==============================================================================
# SECTION 7: Local Project Persistence Routes
# ==============================================================================

@router.get("/projects", response_model=List[ProjectSummary], tags=["Projects"])
async def list_projects_endpoint():
    """List all saved user projects with metadata summaries."""
    try:
        return list_projects()
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Failed to list projects: {str(err)}")


@router.post("/projects", response_model=Project, status_code=201, tags=["Projects"])
async def create_project_endpoint(request: Optional[ProjectCreateRequest] = None):
    """Create a new project JSON file in projects/ directory."""
    try:
        return create_project(request)
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(err)}")


@router.get("/projects/{project_id}", response_model=Project, tags=["Projects"])
async def get_project_endpoint(project_id: str):
    """Load a complete project by ID with full scene and presentation state."""
    project = get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    return project


@router.put("/projects/{project_id}", response_model=Project, tags=["Projects"])
async def save_project_endpoint(project_id: str, project: Project):
    """Save / update an existing project."""
    try:
        project.id = project_id
        return save_project(project)
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Failed to save project: {str(err)}")


@router.post("/projects/{project_id}/duplicate", response_model=Project, tags=["Projects"])
async def duplicate_project_endpoint(project_id: str, name: Optional[str] = None):
    """Duplicate an existing project under a new UUID and name."""
    dup = duplicate_project(project_id, new_name=name)
    if dup is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    return dup


@router.patch("/projects/{project_id}/rename", response_model=Project, tags=["Projects"])
async def rename_project_endpoint(project_id: str, request: ProjectRenameRequest):
    """Rename an existing project."""
    renamed = rename_project(project_id, new_name=request.name, new_description=request.description)
    if renamed is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    return renamed


@router.post("/projects/{project_id}/reset", response_model=Project, tags=["Projects"])
async def reset_project_endpoint(project_id: str):
    """Reset a project's scene to default healthy state."""
    res = reset_project(project_id)
    if res is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    return res


@router.delete("/projects/{project_id}", tags=["Projects"])
async def delete_project_endpoint(project_id: str):
    """Delete a project JSON file."""
    deleted = delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    return {"status": "success", "message": f"Project '{project_id}' deleted."}

