/**
 * OCTForge Projects Module.
 *
 * Responsibilities:
 *  - Project JSON persistence (new, save, load, duplicate, rename, reset, delete).
 *  - Recent projects list with summaries.
 *  - Full Project Metadata Inspector view with copy-to-clipboard.
 *  - Session restoration (scene, visualization, annotations, composition, export).
 *
 * CRITICAL ARCHITECTURAL LAW:
 * Saved projects encapsulate complete scene AND presentation state.
 * Loading a project restores both sides seamlessly.
 */

const Projects = (function () {

  let projectList = [];
  let currentProject = null;

  // ── Unsaved-change detection ─────────────────────────────────────────────
  // Fingerprint of the user-authored working state as of the last save / load /
  // create. Measurements are excluded on purpose: they are derived from each
  // render and refresh on their own, so they are never "unsaved work".
  let savedKey = null;
  let busy = false;          // one create/open flow at a time (no duplicate submissions)
  let savePromise = null;    // one save at a time

  function _workingKey() {
    const s = State.getState();
    return JSON.stringify([s.scene, s.visualization, s.annotations, s.composition, s.export]);
  }

  /** Record the current working state as saved. */
  function markClean() { savedKey = _workingKey(); }

  /** True when the working state differs from the last saved/loaded version. */
  function isDirty() { return savedKey !== null && _workingKey() !== savedKey; }

  // ── Refresh Project List ─────────────────────────────────────────────────
  async function refreshProjectList() {
    try {
      const list = await API.listProjects();
      if (Array.isArray(list)) {
        projectList = list;
        buildPanel();
      }
    } catch (err) {
      console.warn("Failed to list projects:", err);
    }
  }

  // ── Project Lifecycle Operations ─────────────────────────────────────────
  async function createNewProject(name = "Untitled Project") {
    try {
      const newProj = await API.createProject({
        name,
        scene: State.getState().scene,
        visualization: State.getState().visualization,
        annotations: State.getState().annotations,
        composition: State.getState().composition,
      });

      if (newProj && newProj.id) {
        currentProject = newProj;
        State.setCurrentProjectId(newProj.id);
        const nameInput = document.getElementById("project-name");
        if (nameInput) nameInput.value = newProj.name;
        markClean();
        refreshProjectList();
        console.log(`[OCTForge] Created project: ${newProj.id}`);
        return true;
      }
    } catch (err) {
      console.error("Failed to create project:", err);
      if (typeof Dialogs !== "undefined") Dialogs.notify(`Failed to create project: ${err.message}`, "error");
    }
    return false;
  }

  /** Save the working state. Resolves true on success, false on failure. */
  function saveCurrentProject() {
    if (!savePromise) savePromise = _saveCurrentProject().finally(() => { savePromise = null; });
    return savePromise;
  }

  async function _saveCurrentProject() {
    const keyAtSave = _workingKey();   // edits made while the request is in flight stay unsaved
    const s = State.getState();
    const curId = s.currentProjectId || (currentProject ? currentProject.id : null);
    const projectNameInput = document.getElementById("project-name");
    // An existing project keeps its own name. The top-bar field is synced to the *scene*
    // name, so using it here renamed the project (e.g. to "Untitled Scene") on every save.
    const existingName = curId && currentProject && currentProject.id === curId ? currentProject.name : null;
    const name = existingName || (projectNameInput ? projectNameInput.value : s.scene.meta.name || "Untitled Project");

    const payload = {
      id: curId || "temp",
      name,
      description: s.scene.meta.description || "",
      octforge_version: "1.0.0",
      created_at: (currentProject && currentProject.created_at) ? currentProject.created_at : new Date().toISOString(),
      updated_at: new Date().toISOString(),
      scene: s.scene,
      visualization: s.visualization,
      annotations: s.annotations,
      measurements: s.measurements.data,
      composition: s.composition,
      export: s.export,
      metadata: {
        saved_from_client: true,
      },
    };

    try {
      if (curId) {
        const saved = await API.saveProject(curId, payload);
        currentProject = saved;
      } else {
        const created = await API.createProject(payload);
        currentProject = created;
        State.setCurrentProjectId(created.id);
      }
      savedKey = keyAtSave;
      refreshProjectList();
      console.log("[OCTForge] Project saved successfully.");
      if (typeof Dialogs !== "undefined") Dialogs.notify("Project saved successfully.", "ok");
      return true;
    } catch (err) {
      console.error("Failed to save project:", err);
      if (typeof Dialogs !== "undefined") Dialogs.notify(`Failed to save project: ${err.message}`, "error");
      return false;
    }
  }

  /** Restore a project's scene + presentation state into the workspace. */
  function _applyProject(proj) {
    // Restore Scene
    if (proj.scene) {
      State.replaceScene(proj.scene);
      Generator.rebuildAll();
      Preview.triggerRender();
    }

    // Restore Visualization
    if (proj.visualization) {
      State.updateVisualization(proj.visualization);
      if (typeof Visualization !== "undefined" && Visualization.buildPanel) {
        Visualization.buildPanel();
      }
    }

    // Restore Annotations
    if (proj.annotations) {
      State.updateAnnotations(proj.annotations);
      if (typeof Annotation !== "undefined" && Annotation.buildPanel) {
        Annotation.buildPanel();
        Annotation.redraw();
      }
    }

    // Restore Composition
    if (proj.composition) {
      State.updateComposition(proj.composition);
      if (typeof Composition !== "undefined" && Composition.buildPanel) {
        Composition.buildPanel();
      }
    }

    // Restore Export
    if (proj.export) {
      State.updateExport(proj.export);
    }

    // Sync Project Name
    const nameInput = document.getElementById("project-name");
    if (nameInput) nameInput.value = proj.name || "Untitled Project";
  }

  /** Load a saved project into the workspace (unguarded; see requestLoadProject). */
  async function loadProject(projectId) {
    try {
      const proj = await API.getProject(projectId);
      if (proj) {
        currentProject = proj;
        State.setCurrentProjectId(proj.id);
        _applyProject(proj);
        markClean();   // baseline = what the workspace now holds

        refreshProjectList();
        console.log(`[OCTForge] Loaded project: ${proj.id} (${proj.name})`);
        return true;
      }
    } catch (err) {
      console.error("Failed to load project:", err);
      if (typeof Dialogs !== "undefined") Dialogs.notify(`Failed to load project: ${err.message}`, "error");
    }
    return false;
  }

  // ── Unsaved-work protection ──────────────────────────────────────────────
  /** Throw away unsaved edits: back to the last saved version (or defaults if never saved). */
  async function _discardUnsavedChanges() {
    const curId = State.getState().currentProjectId || (currentProject ? currentProject.id : null);
    if (curId) return loadProject(curId);
    _applyProject({
      scene: State.getDefaultScene(),
      visualization: State.getDefaultVisualization(),
      annotations: State.getDefaultAnnotations(),
      composition: State.getDefaultComposition(),
      export: State.getDefaultExport(),
      name: "Untitled Project",
    });
    markClean();
    return true;
  }

  /**
   * Resolves true when the workspace may be replaced: nothing unsaved, or the user
   * saved (successfully) or discarded. Resolves false on Cancel or a failed save/revert,
   * in which case the caller must not proceed.
   * @param {string} action   e.g. "creating a new project"
   * @param {boolean} revertOnDiscard  revert the workspace when discarding (needed when
   *        the caller will keep using the current workspace content, as New Project does)
   */
  async function _resolveUnsavedChanges(action, revertOnDiscard) {
    if (!isDirty()) return true;
    const nameInput = document.getElementById("project-name");
    const label = (currentProject && currentProject.name) || (nameInput && nameInput.value) || "The current project";
    const choice = await Dialogs.choiceAsync({
      title: "Unsaved changes",
      message: `"${label}" has unsaved changes. Save them before ${action}?`,
      choices: [
        { label: "Cancel", value: "cancel", kind: "secondary" },
        { label: "Discard Changes", value: "discard", kind: "danger" },
        { label: "Save and Continue", value: "save", kind: "primary" },
      ],
      cancelValue: "cancel",
    });
    if (choice === "save") return await saveCurrentProject();
    if (choice === "discard") return revertOnDiscard ? await _discardUnsavedChanges() : true;
    return false;
  }

  /** New Project: ask for a title, protecting unsaved work before anything is created. */
  async function requestNewProject() {
    if (busy) return;
    busy = true;
    try {
      const name = await Dialogs.promptAsync({
        title: "New project",
        message: "Enter project title:",
        defaultValue: "New OCT Study",
        confirmLabel: "Create",
        // Runs on Create; the dialog (and the typed title) stays open unless this resolves true.
        beforeConfirm: (value) => value.trim() ? _resolveUnsavedChanges("creating a new project", true) : true,
      });
      if (name && name.trim()) await createNewProject(name.trim());
    } finally {
      busy = false;
    }
  }

  /** Open another project, protecting unsaved work before it is replaced. */
  async function requestLoadProject(projectId) {
    if (busy) return;
    busy = true;
    try {
      const same = projectId === State.getState().currentProjectId;
      const action = same ? "reloading this project" : "opening another project";
      if (await _resolveUnsavedChanges(action, false)) await loadProject(projectId);
    } finally {
      busy = false;
    }
  }

  async function duplicateProject(projectId) {
    try {
      const dup = await API.duplicateProject(projectId);
      if (dup) {
        refreshProjectList();
        console.log(`[OCTForge] Duplicated project to: ${dup.id}`);
      }
    } catch (err) {
      console.error("Failed to duplicate project:", err);
      if (typeof Dialogs !== "undefined") Dialogs.notify(`Failed to duplicate project: ${err.message}`, "error");
    }
  }

  async function renameProject(projectId) {
    const newName = await Dialogs.promptAsync({ title: "Rename project", message: "Enter new project name:", confirmLabel: "Rename" });
    if (!newName || !newName.trim()) return;

    try {
      const renamed = await API.renameProject(projectId, newName.trim());
      if (renamed) {
        if (currentProject && currentProject.id === projectId) {
          currentProject.name = renamed.name;
          const nameInput = document.getElementById("project-name");
          if (nameInput) nameInput.value = renamed.name;
        }
        refreshProjectList();
      }
    } catch (err) {
      console.error("Failed to rename project:", err);
      if (typeof Dialogs !== "undefined") Dialogs.notify(`Failed to rename project: ${err.message}`, "error");
    }
  }

  async function resetProject(projectId) {
    const ok = await Dialogs.confirmAsync({ title: "Reset project", message: "Reset this project's scene to default healthy macula?", confirmLabel: "Reset", cancelLabel: "Cancel", danger: true });
    if (!ok) return;

    try {
      const reset = await API.resetProject(projectId);
      if (reset) {
        if (currentProject && currentProject.id === projectId) {
          loadProject(projectId);
        } else {
          refreshProjectList();
        }
      }
    } catch (err) {
      console.error("Failed to reset project:", err);
      if (typeof Dialogs !== "undefined") Dialogs.notify(`Failed to reset project: ${err.message}`, "error");
    }
  }

  async function deleteProject(projectId) {
    const ok = await Dialogs.confirmAsync({ title: "Delete project", message: "Permanently delete this project? This cannot be undone.", confirmLabel: "Delete", cancelLabel: "Cancel", danger: true });
    if (!ok) return;

    try {
      await API.deleteProject(projectId);
      if (currentProject && currentProject.id === projectId) {
        currentProject = null;
        State.setCurrentProjectId(null);
      }
      refreshProjectList();
    } catch (err) {
      console.error("Failed to delete project:", err);
      if (typeof Dialogs !== "undefined") Dialogs.notify(`Failed to delete project: ${err.message}`, "error");
    }
  }

  // ── UI Construction ──────────────────────────────────────────────────────
  function buildPanel() {
    const container = document.getElementById("projects-body");
    if (!container) return;
    container.innerHTML = "";

    const s = State.getState();
    const curId = s.currentProjectId || (currentProject ? currentProject.id : null);

    // ── 1. Project Actions Card ────────────────────────────────────────────
    const actionCard = document.createElement("div");
    actionCard.className = "section-card";

    const actionHeader = document.createElement("div");
    actionHeader.className = "section-card-title";
    actionHeader.textContent = "Project Management";
    actionCard.appendChild(actionHeader);

    const btnRow1 = document.createElement("div");
    btnRow1.style.display = "flex";
    btnRow1.style.gap = "6px";

    const newBtn = document.createElement("button");
    newBtn.className = "btn-secondary";
    newBtn.style.flex = "1";
    newBtn.textContent = "+ New Project";
    newBtn.addEventListener("click", () => requestNewProject());
    btnRow1.appendChild(newBtn);

    const saveBtn = document.createElement("button");
    saveBtn.className = "btn-primary";
    saveBtn.style.flex = "1";
    saveBtn.innerHTML = '<svg class="icon" aria-hidden="true"><use href="assets/icons.svg#save"></use></svg> Save project';
    saveBtn.addEventListener("click", () => saveCurrentProject());
    btnRow1.appendChild(saveBtn);

    actionCard.appendChild(btnRow1);
    container.appendChild(actionCard);

    // ── 2. Recent Projects List Card ───────────────────────────────────────
    const listCard = document.createElement("div");
    listCard.className = "section-card";

    const listHeader = document.createElement("div");
    listHeader.className = "section-card-title";
    listHeader.innerHTML = `
      <span>Saved projects (${projectList.length})</span>
      <button class="btn-tool-action" id="btn-reload-projects" style="font-size:11px;">Refresh</button>
    `;
    listCard.appendChild(listHeader);

    const listContainer = document.createElement("div");
    listContainer.className = "instance-list-items";

    if (projectList.length === 0) {
      listContainer.innerHTML = `<div class="instance-empty">No saved projects found. Click "Save project" to store your session.</div>`;
    } else {
      projectList.forEach((proj) => {
        const isCurrent = curId === proj.id;
        const pCard = document.createElement("div");
        pCard.className = `project-card${isCurrent ? " active-project" : ""}`;

        const updatedStr = proj.updated_at ? new Date(proj.updated_at).toLocaleDateString(undefined, {
          month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
        }) : "—";

        pCard.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:12px;font-weight:600;color:var(--text-strong);">${proj.name}</span>
            <span style="font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums;">${updatedStr}</span>
          </div>
          <div style="font-size:11px;color:var(--accent);">
            ${proj.pathology_summary || "Normal Macula"} | Seed: ${proj.render_seed || 42}
          </div>
          <div style="display:flex;gap:4px;margin-top:4px;">
            <button class="btn-primary btn-load" style="flex:1;font-size:11px;padding:2px 4px;">Load</button>
            <button class="btn-secondary btn-dup" style="font-size:11px;padding:2px 4px;">Duplicate</button>
            <button class="btn-secondary btn-ren" style="font-size:11px;padding:2px 4px;">Rename</button>
            <button class="btn-remove btn-del" style="width:22px;height:20px;" title="Delete">&times;</button>
          </div>
        `;

        pCard.querySelector(".btn-load").addEventListener("click", () => requestLoadProject(proj.id));
        pCard.querySelector(".btn-dup").addEventListener("click", () => duplicateProject(proj.id));
        pCard.querySelector(".btn-ren").addEventListener("click", () => renameProject(proj.id));
        pCard.querySelector(".btn-del").addEventListener("click", () => deleteProject(proj.id));

        listContainer.appendChild(pCard);
      });
    }

    listCard.appendChild(listContainer);
    container.appendChild(listCard);

    // ── 3. Full Project Metadata Inspector Card ────────────────────────────
    const metaCard = document.createElement("div");
    metaCard.className = "section-card";

    const metaHeader = document.createElement("div");
    metaHeader.className = "section-card-title";
    metaHeader.innerHTML = `
      <span>Metadata inspector</span>
      <button class="btn-tool-action" id="btn-copy-meta" style="font-size:11px;">Copy JSON</button>
    `;
    metaCard.appendChild(metaHeader);

    const fullMeta = {
      software: "OCTForge",
      octforge_version: "1.0.0",
      project_id: curId || "transient_session",
      project_name: s.scene.meta.name || "Untitled Scene",
      synthetic_notice: "SYNTHETIC — FOR EDUCATION ONLY",
      render_id: s.renderId,
      render_seed: s.scene.anatomy.render_seed,
      scan_geometry: {
        width_mm: s.scene.acquisition.scan_width,
        depth_mm: s.scene.acquisition.scan_depth,
        output_resolution: [s.scene.acquisition.height, s.scene.acquisition.width],
        axial_resolution_um: s.scene.acquisition.axial_resolution,
        lateral_resolution_um: s.scene.acquisition.lateral_resolution,
      },
      pathology: {
        enabled: s.scene.pathology.enabled,
        instances: s.scene.pathology.instances,
      },
      annotations_count: (s.annotations.items || []).length,
      composition_panels: (s.composition.panels || []).length,
    };

    const jsonBox = document.createElement("pre");
    jsonBox.className = "json-inspector-box";
    jsonBox.textContent = JSON.stringify(fullMeta, null, 2);
    metaCard.appendChild(jsonBox);

    const copyBtn = metaHeader.querySelector("#btn-copy-meta");
    if (copyBtn) {
      copyBtn.addEventListener("click", () => {
        navigator.clipboard.writeText(JSON.stringify(fullMeta, null, 2));
        copyBtn.textContent = "Copied";
        setTimeout(() => { copyBtn.textContent = "Copy JSON"; }, 1500);
      });
    }

    container.appendChild(metaCard);

    // Wire reload button
    const reloadBtn = listHeader.querySelector("#btn-reload-projects");
    if (reloadBtn) {
      reloadBtn.addEventListener("click", () => refreshProjectList());
    }
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    refreshProjectList();
    buildPanel();

    // Top-bar save button
    const topbarSaveBtn = document.getElementById("btn-save-project");
    if (topbarSaveBtn) {
      topbarSaveBtn.addEventListener("click", () => saveCurrentProject());
    }
  }

  return {
    init,
    buildPanel,
    refreshProjectList,
    createNewProject,
    requestNewProject,
    saveCurrentProject,
    loadProject,
    requestLoadProject,
    isDirty,
    markClean,
    duplicateProject,
    renameProject,
    resetProject,
    deleteProject,
  };
})();
