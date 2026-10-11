/**
 * OCTForge Main Application Initializer.
 *
 * Responsibilities:
 *  1. Initialize all modules in proper dependency order.
 *  2. Verify backend operational health.
 *  3. Handle horizontal workspace navigation (Generator | Visualize | Annotate | Export | Projects)
 *     with section sub-tabs inside the left panel.
 *     - Generator  → sections: "Scene" (pane-generator), "Presets" (pane-presets)
 *     - Visualize  → single pane (pane-visualization), no section bar
 *     - Annotate   → sections: "Annotations" (pane-annotation), "Measurements" (pane-measurement)
 *     - Export     → sections: "Export" (pane-export), "Composition" (pane-composition)
 *     - Projects   → single pane (pane-projects), no section bar
 *  4. mainSetActiveTab(name) accepts ALL eight legacy names.
 *  5. Subscribe to state.scene changes and schedule debounced preview renders.
 *     STRICT ARCHITECTURAL LAW: ONLY changes to state.scene trigger renders.
 *  6. Wire all top-bar / toolbar controls.
 *  7. Provide setBusy(btn, busy, busyLabel) helper — keeps SVG icon intact.
 *  8. Reset annotation tool to Select when leaving Annotate workspace.
 */

/* ── Workspace / section mapping ─────────────────────────────────────────── */
const _WS_MAP = {
  // workspace → [sectionName, paneId] pairs (first is default section)
  generator:     [["Scene", "pane-generator"], ["Presets", "pane-presets"]],
  visualization: [["Visualize", "pane-visualization"]],
  annotation:    [["Annotations", "pane-annotation"], ["Measurements", "pane-measurement"]],
  export:        [["Export", "pane-export"], ["Composition", "pane-composition"]],
  projects:      [["Projects", "pane-projects"]],
};

// Legacy name → workspace mapping
const _LEGACY_TO_WS = {
  generator:     "generator",
  presets:       "generator",
  visualization: "visualization",
  annotation:    "annotation",
  measurement:   "annotation",
  export:        "export",
  composition:   "export",
  projects:      "projects",
};

// Legacy name → pane id (direct override)
const _LEGACY_TO_PANE = {
  generator:     "pane-generator",
  presets:       "pane-presets",
  visualization: "pane-visualization",
  annotation:    "pane-annotation",
  measurement:   "pane-measurement",
  export:        "pane-export",
  composition:   "pane-composition",
  projects:      "pane-projects",
};

// Remember last section per workspace
const _lastSection = {
  generator:     "pane-generator",
  visualization: "pane-visualization",
  annotation:    "pane-annotation",
  export:        "pane-export",
  projects:      "pane-projects",
};

let _activeWorkspace = "generator";

/** Name of the active workspace: "generator" | "visualization" | "annotation" | "export" | "projects". */
window.getActiveWorkspace = function () { return _activeWorkspace; };

/* ── Section tabbar builder ──────────────────────────────────────────────── */
function _buildSectionTabs(wsName) {
  const tabbar = document.getElementById("section-tabbar");
  if (!tabbar) return;
  tabbar.innerHTML = "";

  const sections = _WS_MAP[wsName] || [];
  const activePaneId = _lastSection[wsName] || (sections[0] && sections[0][1]);

  if (sections.length <= 1) {
    tabbar.hidden = true;
    return;
  }

  tabbar.hidden = false;
  sections.forEach(([label, paneId]) => {
    const btn = document.createElement("button");
    btn.className = "section-tab" + (paneId === activePaneId ? " active" : "");
    btn.textContent = label;
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", paneId === activePaneId ? "true" : "false");
    btn.addEventListener("click", () => {
      _activateSectionPane(wsName, paneId);
    });
    tabbar.appendChild(btn);
  });
}

function _activateSectionPane(wsName, paneId) {
  // Update remembered section
  _lastSection[wsName] = paneId;

  // Show correct pane
  document.querySelectorAll(".tab-pane").forEach((p) => {
    p.classList.toggle("active", p.id === paneId);
  });

  // Update section tab active state
  const tabbar = document.getElementById("section-tabbar");
  if (tabbar) {
    tabbar.querySelectorAll(".section-tab").forEach((btn) => {
      const sections = _WS_MAP[wsName] || [];
      const match = sections.find((s) => s[0] === btn.textContent);
      const isActive = match && match[1] === paneId;
      btn.classList.toggle("active", !!isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });
  }

  // Derive legacy tab name for state
  const sections = _WS_MAP[wsName] || [];
  const section = sections.find((s) => s[1] === paneId);
  const legacyName = Object.entries(_LEGACY_TO_PANE).find(([, p]) => p === paneId)?.[0] || wsName;
  State.setActiveTab(legacyName);

  // Composition view mode management
  if (legacyName === "composition") {
    Composition.setViewMode("composition");
  } else {
    if (State.getState().viewMode === "composition") {
      Composition.setViewMode("bscan");
    }
  }
}

/* ── Workspace toolbar context switcher ─────────────────────────────────── */
function _updateToolbarContext(wsName) {
  const contexts = {
    "wtb-generator": "generator",
    "wtb-visualize": "visualization",
    "wtb-annotate":  "annotation",
    "wtb-export":    "export",
  };
  Object.entries(contexts).forEach(([id, ws]) => {
    const node = document.getElementById(id);
    if (node) node.hidden = (wsName !== ws);
  });
}

/* ── Main workspace switcher ─────────────────────────────────────────────── */
window.mainSetActiveTab = function (tabName) {
  const wsName = _LEGACY_TO_WS[tabName] || "generator";
  const paneId = _LEGACY_TO_PANE[tabName] || ("pane-" + tabName);

  // If leaving Annotate, reset tool to Select
  if (_activeWorkspace === "annotation" && wsName !== "annotation") {
    if (typeof Annotation !== "undefined" && Annotation.setTool) {
      Annotation.setTool("select");
    }
  }

  _activeWorkspace = wsName;

  // Activate primary workspace tab
  document.querySelectorAll(".ws-tab").forEach((tab) => {
    const isTarget = tab.getAttribute("data-workspace") === wsName;
    tab.classList.toggle("active", isTarget);
    tab.setAttribute("aria-selected", isTarget.toString());
    tab.setAttribute("tabindex", isTarget ? "0" : "-1");
  });

  // Build section tabs and activate specific pane
  _buildSectionTabs(wsName);
  _lastSection[wsName] = paneId;
  _activateSectionPane(wsName, paneId);

  // Switch toolbar context
  _updateToolbarContext(wsName);

  // State update
  State.setActiveTab(tabName);

  // Composition view mode
  if (tabName === "composition") {
    Composition.setViewMode("composition");
  } else if (State.getState().viewMode === "composition") {
    Composition.setViewMode("bscan");
  }
};

/* ── Busy button helper — keeps SVG icon intact ───────────────────────────── */
window.setBusy = function (btn, busy, busyLabel) {
  if (!btn) return;
  if (busy) {
    btn.disabled = true;
    btn.setAttribute("aria-busy", "true");
    // Replace only the label text node, leave the SVG
    const labelSpan = btn.querySelector(".btn-label");
    if (labelSpan) {
      labelSpan._originalText = labelSpan._originalText || labelSpan.textContent;
      labelSpan.textContent = busyLabel;
    } else {
      // Fallback: find the last text node and replace
      let lastText = null;
      btn.childNodes.forEach((n) => {
        if (n.nodeType === Node.TEXT_NODE && n.textContent.trim()) lastText = n;
      });
      if (lastText) {
        lastText._originalContent = lastText._originalContent || lastText.textContent;
        lastText.textContent = " " + busyLabel;
      }
    }
  } else {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    const labelSpan = btn.querySelector(".btn-label");
    if (labelSpan && labelSpan._originalText) {
      labelSpan.textContent = labelSpan._originalText;
    } else {
      btn.childNodes.forEach((n) => {
        if (n.nodeType === Node.TEXT_NODE && n._originalContent) {
          n.textContent = n._originalContent;
        }
      });
    }
  }
};

document.addEventListener("DOMContentLoaded", async () => {
  console.log("[OCTForge] Initializing Application Suite…");

  // ── 1. Initialize modules in order ───────────────────────────────────────
  Controls.init();
  Preview.init();
  Visualization.init();
  Annotation.init();
  Measurement.init();
  Composition.init();
  Presets.init();
  Projects.init();
  Export.init();

  // ── 2. Primary Workspace Tab Navigation ──────────────────────────────────
  const wsTabs = document.querySelectorAll(".ws-tab");
  wsTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const wsName = tab.getAttribute("data-workspace");
      if (wsName) mainSetActiveTab(wsName);
    });
  });

  // Keyboard navigation: Left/Right/Home/End on workspace tab strip
  const wsNav = document.getElementById("workspace-nav");
  if (wsNav) {
    wsNav.addEventListener("keydown", (e) => {
      const tabs = [...wsNav.querySelectorAll(".ws-tab")];
      const cur  = tabs.indexOf(document.activeElement);
      if (cur < 0) return;
      let next = cur;
      if (e.key === "ArrowRight") { next = (cur + 1) % tabs.length; }
      else if (e.key === "ArrowLeft") { next = (cur - 1 + tabs.length) % tabs.length; }
      else if (e.key === "Home") { next = 0; }
      else if (e.key === "End")  { next = tabs.length - 1; }
      else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        tabs[cur].click();
        return;
      } else { return; }
      e.preventDefault();
      tabs[next].focus();
      tabs[next].click();
    });
  }

  // ── 3. Backend Health Check ──────────────────────────────────────────────
  try {
    const health = await API.healthCheck();
    if (health.status === "ok") {
      State.setState({ serverHealth: "online" });
      console.log("[OCTForge] Backend online:", health.version);
    }
  } catch (err) {
    console.error("[OCTForge] Backend health check failed:", err);
    State.setState({ serverHealth: "offline" });
  }

  // ── 4. Scene-Change Subscription → Debounced Preview Render ──────────────
  let prevSceneJSON = JSON.stringify(State.getState().scene);
  State.subscribe((s) => {
    const currentSceneJSON = JSON.stringify(s.scene);
    if (currentSceneJSON !== prevSceneJSON) {
      prevSceneJSON = currentSceneJSON;
      Preview.scheduleRender();
    }
  });

  // ── 5. Control Wiring ────────────────────────────────────────────────────

  // Undo / Redo
  const undoBtn = document.getElementById("btn-undo");
  const redoBtn = document.getElementById("btn-redo");
  const undoDepthEl = document.getElementById("val-undo-depth");

  function _syncHistoryButtons({ canUndo, canRedo, depth }) {
    if (undoBtn) undoBtn.disabled = !canUndo;
    if (redoBtn) redoBtn.disabled = !canRedo;
    if (undoDepthEl) undoDepthEl.textContent = depth;
  }
  State.subscribeHistory(_syncHistoryButtons);
  _syncHistoryButtons({ canUndo: State.canUndo(), canRedo: State.canRedo(), depth: 0 });

  if (undoBtn) undoBtn.addEventListener("click", () => { State.undo(); });
  if (redoBtn) redoBtn.addEventListener("click", () => { State.redo(); });

  // Keyboard Shortcuts (Ctrl+Z / Ctrl+Y)
  document.addEventListener("keydown", (e) => {
    const tag = document.activeElement.tagName;
    const isInput = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || document.activeElement.isContentEditable;
    if (isInput) return;

    if ((e.ctrlKey || e.metaKey) && e.key === "z" && !e.shiftKey) {
      e.preventDefault(); State.undo();
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === "y" || (e.key === "z" && e.shiftKey))) {
      e.preventDefault(); State.redo();
    }
  });

  // Seed input (now in workspace toolbar)
  const seedInput = document.getElementById("seed-input");
  if (seedInput) {
    seedInput.value = State.getState().scene.anatomy.render_seed;
    seedInput.addEventListener("change", (e) => {
      const v = parseInt(e.target.value, 10);
      if (!isNaN(v) && v >= 0) State.updateSeed(v);
    });
    State.subscribe((s) => {
      const seed = s.scene.anatomy.render_seed;
      if (parseInt(seedInput.value, 10) !== seed) seedInput.value = seed;
    });
  }

  // Randomize button (now in workspace toolbar)
  const randomizeBtn = document.getElementById("btn-randomize");
  if (randomizeBtn) {
    randomizeBtn.addEventListener("click", async () => {
      setBusy(randomizeBtn, true, "Working…");
      try {
        const result = await API.randomizeScene();
        if (result && result.anatomy) {
          State.replaceScene(result);
          Generator.rebuildAll();
          Preview.triggerRender();
          console.log("[OCTForge] Randomized scene, seed:", result.anatomy.render_seed);
        }
      } catch (err) {
        console.error("[OCTForge] Randomize failed:", err);
        const seed = Math.floor(Math.random() * 2147483647);
        State.updateSeed(seed);
      } finally {
        setBusy(randomizeBtn, false);
      }
    });
  }

  // Render Final button (now in workspace toolbar)
  const renderFinalBtn = document.getElementById("btn-render-final");
  if (renderFinalBtn) {
    renderFinalBtn.addEventListener("click", async () => {
      setBusy(renderFinalBtn, true, "Rendering…");
      try {
        await Generator.triggerFinalRender();
        console.log("[OCTForge] Final render complete");
      } catch (err) {
        console.error("[OCTForge] Final render failed:", err);
      } finally {
        setBusy(renderFinalBtn, false);
      }
    });
  }

  // Export button (Export workspace toolbar) — same action as the Export panel's button
  const exportToolbarBtn = document.getElementById("btn-export-toolbar");
  if (exportToolbarBtn) {
    exportToolbarBtn.addEventListener("click", async () => {
      setBusy(exportToolbarBtn, true, "Exporting…");
      try {
        await Export.downloadExport();
      } finally {
        setBusy(exportToolbarBtn, false);
      }
    });
  }

  // Project Name field
  const projectNameInput = document.getElementById("project-name");
  if (projectNameInput) {
    projectNameInput.addEventListener("change", (e) => {
      const currentScene = State.getState().scene;
      State.replaceScene({ ...currentScene, meta: { ...currentScene.meta, name: e.target.value } });
    });
    State.subscribe((s) => {
      if (document.activeElement !== projectNameInput) {
        projectNameInput.value = s.scene.meta.name || "Untitled Scene";
      }
    });
  }

  // Save button
  const saveBtn = document.getElementById("btn-save-project");
  if (saveBtn) {
    saveBtn.addEventListener("click", () => {
      if (typeof Projects !== "undefined" && Projects.saveCurrentProject) {
        Projects.saveCurrentProject();
      }
    });
  }

  // ── 6. Initial workspace state ──────────────────────────────────────────
  // Activate Generator workspace
  _buildSectionTabs("generator");
  _updateToolbarContext("generator");

  // ── 7. Initial Render ────────────────────────────────────────────────────
  Preview.triggerRender();

  // Baseline for unsaved-change detection: the freshly initialised session is "clean".
  Projects.markClean();

  console.log("[OCTForge] Presentation Platform Ready (Phase 2).");
});
