"""Project persistence module for OCTForge.

Handles local JSON project persistence (new, save, load, duplicate, rename, reset, list)
under the projects/ directory with full schema validation, error recovery, and backward compatibility.

CRITICAL ARCHITECTURAL LAW:
All saved projects store complete SceneConfig, VisualizationConfig, AnnotationSet,
MeasurementSet, CompositionConfig, and ExportConfig state to ensure 100% reproducibility.
"""

import os
import json
import uuid
import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from backend.app.schema import (
    Project,
    ProjectSummary,
    ProjectCreateRequest,
    ProjectRenameRequest,
    SceneConfig,
    VisualizationConfig,
    AnnotationSet,
)
from backend.app.presets import _build_normal_macula

# Project storage directory
PROJECTS_DIR = Path("projects")


def _ensure_projects_dir() -> Path:
    """Ensure the projects/ storage directory exists."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    return PROJECTS_DIR


def _get_project_path(project_id: str) -> Path:
    """Return canonical path for a project JSON file."""
    # Sanitize project ID to prevent path traversal
    safe_id = "".join(c for c in project_id if c.isalnum() or c in "-_")
    return _ensure_projects_dir() / f"{safe_id}.json"


def list_projects() -> List[ProjectSummary]:
    """List all saved projects from the projects/ directory."""
    _ensure_projects_dir()
    summaries: List[ProjectSummary] = []

    for fpath in PROJECTS_DIR.glob("*.json"):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Summarize pathology
            path_summary = "Normal Macula"
            pathology_data = data.get("scene", {}).get("pathology", {})
            if pathology_data.get("enabled"):
                instances = pathology_data.get("instances", [])
                if instances:
                    types = [inst.get("type", "lesion").replace("_", " ").title() for inst in instances]
                    path_summary = ", ".join(types)
                else:
                    path_summary = "Pathology Enabled"

            summaries.append(ProjectSummary(
                id=str(data.get("id", fpath.stem)),
                name=str(data.get("name", "Untitled Project")),
                description=str(data.get("description", "")),
                octforge_version=str(data.get("octforge_version", "1.0.0")),
                created_at=str(data.get("created_at", datetime.datetime.now(datetime.timezone.utc).isoformat())),
                updated_at=str(data.get("updated_at", datetime.datetime.now(datetime.timezone.utc).isoformat())),
                pathology_summary=path_summary,
                render_seed=int(data.get("scene", {}).get("anatomy", {}).get("render_seed", 42)),
            ))
        except Exception:
            # Skip unparseable files without crashing list view
            continue

    # Sort descending by updated_at
    summaries.sort(key=lambda s: s.updated_at, reverse=True)
    return summaries


def get_project(project_id: str) -> Optional[Project]:
    """Load and return a complete Project by ID, handling missing/extra fields gracefully."""
    fpath = _get_project_path(project_id)
    if not fpath.exists():
        return None

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Project(**data)
    except Exception as err:
        # If schema parsing fails on partial legacy file, attempt fallback reconstruction
        try:
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            fallback = Project(
                id=project_id,
                name=str(data.get("name", "Recovered Project")),
                description=str(data.get("description", "")),
                created_at=str(data.get("created_at", now_iso)),
                updated_at=str(data.get("updated_at", now_iso)),
                scene=SceneConfig(**data.get("scene", {})),
            )
            return fallback
        except Exception:
            return None


def save_project(project: Project) -> Project:
    """Save a Project model to disk as formatted JSON."""
    _ensure_projects_dir()
    project.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    fpath = _get_project_path(project.id)

    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(project.model_dump(), f, indent=2, default=str)

    return project


def create_project(request: Optional[ProjectCreateRequest] = None) -> Project:
    """Create and persist a new Project."""
    _ensure_projects_dir()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    new_id = str(uuid.uuid4())

    name = request.name if (request and request.name) else "Untitled Project"
    description = request.description if (request and request.description) else ""
    scene = request.scene if (request and request.scene) else _build_normal_macula()
    vis = request.visualization if (request and request.visualization) else VisualizationConfig()
    annot = request.annotations if (request and request.annotations) else AnnotationSet()
    comp = request.composition if (request and request.composition) else None

    project = Project(
        id=new_id,
        name=name,
        description=description,
        octforge_version="1.0.0",
        created_at=now_iso,
        updated_at=now_iso,
        scene=scene,
        visualization=vis,
        annotations=annot,
        composition=comp,
    )

    return save_project(project)


def duplicate_project(project_id: str, new_name: Optional[str] = None) -> Optional[Project]:
    """Duplicate an existing project under a new UUID and name."""
    existing = get_project(project_id)
    if existing is None:
        return None

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    dup_id = str(uuid.uuid4())
    dup_name = new_name or f"{existing.name} (Copy)"

    dup_project = existing.model_copy(deep=True)
    dup_project.id = dup_id
    dup_project.name = dup_name
    dup_project.created_at = now_iso
    dup_project.updated_at = now_iso

    return save_project(dup_project)


def rename_project(project_id: str, new_name: str, new_description: Optional[str] = None) -> Optional[Project]:
    """Rename an existing project."""
    project = get_project(project_id)
    if project is None:
        return None

    project.name = new_name
    if new_description is not None:
        project.description = new_description

    return save_project(project)


def reset_project(project_id: str) -> Optional[Project]:
    """Reset project scene and presentations to default healthy state while retaining ID and created_at."""
    project = get_project(project_id)
    if project is None:
        return None

    project.scene = _build_normal_macula()
    project.visualization = VisualizationConfig()
    project.annotations = AnnotationSet()
    project.measurements = None
    project.composition = None
    project.export = None

    return save_project(project)


def delete_project(project_id: str) -> bool:
    """Delete a project JSON file."""
    fpath = _get_project_path(project_id)
    if fpath.exists():
        fpath.unlink()
        return True
    return False
