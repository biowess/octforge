/**
 * OCTForge API Client.
 * Single source of truth for backend HTTP endpoint URLs and fetch wrappers.
 */

const API = (function () {
  const BASE_URL = "/api";

  async function _post(endpoint, data, signal) {
    const res = await fetch(`${BASE_URL}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
      signal,
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Request to ${endpoint} failed: ${res.status} ${res.statusText}`);
    }
    return await res.json();
  }

  async function _get(endpoint) {
    const res = await fetch(`${BASE_URL}${endpoint}`);
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `GET ${endpoint} failed: ${res.status} ${res.statusText}`);
    }
    return await res.json();
  }

  async function _put(endpoint, data) {
    const res = await fetch(`${BASE_URL}${endpoint}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `PUT ${endpoint} failed: ${res.status} ${res.statusText}`);
    }
    return await res.json();
  }

  async function _patch(endpoint, data) {
    const res = await fetch(`${BASE_URL}${endpoint}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `PATCH ${endpoint} failed: ${res.status} ${res.statusText}`);
    }
    return await res.json();
  }

  async function _delete(endpoint) {
    const res = await fetch(`${BASE_URL}${endpoint}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `DELETE ${endpoint} failed: ${res.status} ${res.statusText}`);
    }
    return await res.json();
  }

  // ── 1. System & Health ───────────────────────────────────────────────────
  async function healthCheck() {
    return await _get("/health");
  }

  // ── 2. Rendering ─────────────────────────────────────────────────────────
  async function renderPreview(sceneConfig, signal) {
    return await _post("/render/preview", sceneConfig, signal);
  }

  async function renderFinal(sceneConfig) {
    return await _post("/render/final", sceneConfig);
  }

  // ── 3. Presentation (Visualize, Annotate, Measure) ───────────────────────
  async function visualize(renderId, visualizationConfig) {
    return await _post("/visualize", {
      render_id: renderId,
      config: visualizationConfig,
    });
  }

  async function annotate(renderId, autoLabel = false, autoLabelX = null, palette = "clinical", annotations = null) {
    return await _post("/annotate", {
      render_id: renderId,
      auto_label: autoLabel,
      auto_label_x: autoLabelX,
      palette,
      annotations,
    });
  }

  async function measure(renderId, calipers = null, includeProfiles = false) {
    return await _post("/measure", {
      render_id: renderId,
      calipers,
      include_profiles: includeProfiles,
    });
  }

  // ── 4. Figure Composition ────────────────────────────────────────────────
  async function composeFigure(compositionConfig) {
    return await _post("/compose", {
      config: compositionConfig,
    });
  }

  // ── 5. Multi-Format Export ───────────────────────────────────────────────
  async function exportArtifact(exportRequest) {
    return await _post("/export", exportRequest);
  }

  // ── 6. Presets & Randomization ───────────────────────────────────────────
  async function getPathologyPresets() {
    return await _get("/presets/pathology");
  }

  async function getPathologyPreset(presetId) {
    return await _get(`/presets/pathology/${encodeURIComponent(presetId)}`);
  }

  async function getExportPresets() {
    return await _get("/presets/export");
  }

  async function randomizeScene(baseScene = null, seed = null, mode = "balanced", variance = 0.25) {
    return await _post("/presets/randomize", {
      base_scene: baseScene,
      seed,
      mode,
      variance,
    });
  }

  async function generateVariants(presetId = "cme", baseScene = null, count = 4, baseSeed = 42) {
    return await _post("/presets/variants", {
      preset_id: presetId,
      base_scene: baseScene,
      count,
      base_seed: baseSeed,
    });
  }

  // ── 7. Local Project Persistence ─────────────────────────────────────────
  async function listProjects() {
    return await _get("/projects");
  }

  async function createProject(projectCreateData = null) {
    return await _post("/projects", projectCreateData || {});
  }

  async function getProject(projectId) {
    return await _get(`/projects/${encodeURIComponent(projectId)}`);
  }

  async function saveProject(projectId, projectData) {
    return await _put(`/projects/${encodeURIComponent(projectId)}`, projectData);
  }

  async function duplicateProject(projectId, newName = null) {
    const q = newName ? `?name=${encodeURIComponent(newName)}` : "";
    return await _post(`/projects/${encodeURIComponent(projectId)}/duplicate${q}`, {});
  }

  async function renameProject(projectId, name, description = null) {
    return await _patch(`/projects/${encodeURIComponent(projectId)}/rename`, {
      name,
      description,
    });
  }

  async function resetProject(projectId) {
    return await _post(`/projects/${encodeURIComponent(projectId)}/reset`, {});
  }

  async function deleteProject(projectId) {
    return await _delete(`/projects/${encodeURIComponent(projectId)}`);
  }

  return {
    healthCheck,
    renderPreview,
    renderFinal,
    visualize,
    annotate,
    measure,
    composeFigure,
    exportArtifact,
    getPathologyPresets,
    getPathologyPreset,
    getExportPresets,
    randomizeScene,
    generateVariants,
    listProjects,
    createProject,
    getProject,
    saveProject,
    duplicateProject,
    renameProject,
    resetProject,
    deleteProject,
  };
})();

