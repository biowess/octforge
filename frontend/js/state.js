/**
 * OCTForge State Store.
 *
 * Extends pub/sub store with:
 *  - A canonical `sceneConfig` alias (same object as `scene`) for generator panels.
 *  - A shared undo/redo command stack usable by every panel.
 *  - Helpers: `commitSceneChange(patch)` pushes an undo snapshot then
 *    applies the patch — the single function all panels must call for
 *    undoable edits.
 *
 * NOTE: Only changes to sceneConfig (i.e. `state.scene`) should trigger
 * preview renders.  Presentation-layer config will live in a
 * separate `state.presentation` key and MUST NOT trigger scene re-renders.
 * The main.js subscriber explicitly checks `state.scene` for this reason.
 */

const State = (function () {

  // ── Default scene (matches Pydantic schema defaults) ──────────────────────
  const DEFAULT_SCENE = {
    meta: {
      name: "Untitled Scene",
      description: "",
      version: "1.0.0",
      created_at: null,
      updated_at: null,
    },
    anatomy: {
      // Foveal geometry
      fovea_center:        256.0,
      fovea_pit_depth:      80.0,
      fovea_pit_width:       0.30,
      curvature:             0.05,
      boundary_asymmetry:    0.0,

      // Boundary irregularity
      boundary_smoothness:   0.12,
      boundary_variation:    4.0,

      // Per-layer thicknesses (pixels, parafoveal peak)
      rnfl_thickness:       28.0,
      gcl_thickness:        20.0,
      ipl_thickness:        18.0,
      inl_thickness:        14.0,
      opl_thickness:        12.0,
      onl_thickness:        52.0,
      ez_thickness:         20.0,
      rpe_thickness:         8.0,
      choroid_thickness:   130.0,

      // Layer reflectivities (linear, pre-log, [0,1])
      rnfl_reflectivity:    0.85,
      gcl_reflectivity:     0.25,
      ipl_reflectivity:     0.40,
      inl_reflectivity:     0.14,
      opl_reflectivity:     0.40,
      onl_reflectivity:     0.06,
      ez_reflectivity:      0.80,
      rpe_reflectivity:     0.95,
      choroid_reflectivity: 0.40,
      vitreous_reflectivity: 0.05,

      // Render seed
      render_seed: 42,
    },
    pathology: {
      enabled: false,
      instances: [],
    },
    acquisition: {
      scan_width:          6.0,
      scan_depth:          2.0,
      width:             512,
      height:            512,
      a_scan_density:    512,
      b_scan_count:        1,
      output_resolution: [512, 512],
      axial_resolution:    4.0,
      lateral_resolution:  15.0,
      averaging:           1,
      signal_strength:     8.0,
      snr_db:             25.0,
    },
    realism: {
      speckle_amount:           1.0,
      speckle_avg_count:          6,
      contrast:                 1.0,
      db_lo:                  -35.0,
      db_hi:                   -2.0,
      display_gamma:            1.55,
      blur:                     0.55,
      axial_blur:               2.1,
      lateral_blur:             0.9,
      background_noise:       -31.5,
      intensity_nonuniformity:  0.16,
      boundary_uncertainty:     0.0,
    },
    artifacts: {
      enabled: false,
      instances: [],
    },
  };

  // ── Default presentation configs ────────────────────────────────────────
  const DEFAULT_VISUALIZATION = {
    mode: "colored_layer", // "grayscale", "colored_layer", "hybrid", "educational"
    palette: "clinical", // "clinical", "colorblind_safe", "educational", "custom"
    layers: {
      RNFL:    { color: "#FF9800", opacity: 0.6, visible: true },
      GCL:     { color: "#00BCD4", opacity: 0.6, visible: true },
      IPL:     { color: "#2196F3", opacity: 0.6, visible: true },
      INL:     { color: "#4CAF50", opacity: 0.6, visible: true },
      OPL:     { color: "#8BC34A", opacity: 0.6, visible: true },
      ONL:     { color: "#3F51B5", opacity: 0.6, visible: true },
      EZ:      { color: "#FFEB3B", opacity: 0.6, visible: true },
      RPE:     { color: "#E91E63", opacity: 0.6, visible: true },
      CHOROID: { color: "#795548", opacity: 0.6, visible: true },
      SCLERA:  { color: "#9E9E9E", opacity: 0.6, visible: true },
      VESSEL:  { color: "#D32F2F", opacity: 0.6, visible: true },
    },
    global_opacity: 0.6,
    highlight_pathology: true,
  };

  const DEFAULT_ANNOTATIONS = {
    items: [],
    legend: [
      { id: 1, label: "RNFL", color: "#FF9800" },
      { id: 2, label: "GCL", color: "#00BCD4" },
      { id: 3, label: "IPL", color: "#2196F3" },
      { id: 4, label: "INL", color: "#4CAF50" },
      { id: 5, label: "OPL", color: "#8BC34A" },
      { id: 6, label: "ONL", color: "#3F51B5" },
      { id: 7, label: "EZ", color: "#FFEB3B" },
      { id: 8, label: "RPE", color: "#E91E63" },
      { id: 9, label: "Choroid", color: "#795548" },
      { id: 10, label: "Sclera", color: "#9E9E9E" },
    ],
  };

  const DEFAULT_MEASUREMENTS = {
    calipers: [],
    showScaleBars: true,
    scaleBarColor: "#FFFFFF",
    data: null, // live derived quantitative metrics from server
  };

  const DEFAULT_COMPOSITION = {
    title: "Macular OCT Study",
    subtitle: "Synthetic High-Resolution Cross-Sectional Series",
    layout: "1x2", // "single", "1x2", "2x1", "1x3", "3x1", "2x2", "1x4", "4x1", "grid"
    grid_columns: null,
    spacing: 20,
    padding: 30,
    background_color: "#121212",
    text_color: "#EEEEEE",
    font_size_title: 22,
    font_size_subtitle: 14,
    font_size_panel_label: 18,
    font_size_panel_title: 13,
    show_legend: false,
    legend_palette: "clinical",
    show_metadata_block: false,
    metadata_block_text: "SYNTHETIC OCT IMAGE — FOR EDUCATION AND RESEARCH ONLY — NOT FOR CLINICAL DIAGNOSIS",
    panels: [], // Array of PanelConfig
  };

  const DEFAULT_EXPORT = {
    preset: "publication",
    format: "png",
    dpi: 300,
    width: null,
    height: null,
    color_mode: "RGB",
    quality: 95,
    compression: "lzw",
    include_annotations: true,
    include_measurements: true,
    include_scale_bars: true,
    watermark: false,
    watermark_text: "SYNTHETIC — FOR EDUCATION ONLY",
    custom_metadata: null,
  };

  // ── Application state ────────────────────────────────────────────────────
  let state = {
    // Generator scene config — the ONLY thing that triggers preview renders.
    scene: deepClone(DEFAULT_SCENE),

    // Presentation-layer state stores — completely decoupled from scene.
    // Changing these NEVER triggers /api/render/* calls.
    visualization: deepClone(DEFAULT_VISUALIZATION),
    annotations: deepClone(DEFAULT_ANNOTATIONS),
    measurements: deepClone(DEFAULT_MEASUREMENTS),
    composition: deepClone(DEFAULT_COMPOSITION),
    export: deepClone(DEFAULT_EXPORT),

    // Presentation UI state
    activeTab: "generator", // "generator", "visualization", "annotation", "measurement", "composition", "presets", "projects", "export"
    activeAnnotationTool: "select", // "select", "text", "arrow", "leader_line", "dot", "numbered_marker", "bracket", "reference_line", "caliper"
    selectedAnnotationId: null,
    viewMode: "bscan", // "bscan" or "composition"
    styledPreviewImage: null,
    composedImage: null,
    currentProjectId: null,

    // Render status
    previewImage: null,
    renderId: null,
    cacheKey: null,
    isCached: false,
    renderLatencyMs: 0,
    isRendering: false,

    // Server health
    serverHealth: "connecting",
  };

  // ── Undo / Redo stack ────────────────────────────────────────────────────
  // Each entry is a deep clone of `state.scene` at the moment of commit.
  const MAX_UNDO = 64;
  let undoStack  = [];   // past snapshots (newest last)
  let redoStack  = [];   // future snapshots (newest last → pop to redo)

  // ── Subscribers ──────────────────────────────────────────────────────────
  const listeners = new Set();

  // ── Utilities ─────────────────────────────────────────────────────────────
  function deepClone(obj) {
    if (obj === undefined || obj === null) return obj;
    return JSON.parse(JSON.stringify(obj));
  }

  // ── Core pub/sub ─────────────────────────────────────────────────────────
  function getState() {
    return state;
  }

  function subscribe(fn) {
    listeners.add(fn);
    return function unsubscribe() { listeners.delete(fn); };
  }

  /** Low-level setState — does NOT push to undo stack. Use for UI state only. */
  function setState(patch) {
    if (typeof patch === "function") {
      state = patch(state);
    } else {
      state = { ...state, ...patch };
    }
    _notify();
  }

  function _notify() {
    listeners.forEach((fn) => fn(state));
  }

  // ── Undo / Redo API ───────────────────────────────────────────────────────
  /**
   * Commit a scene change and push the PREVIOUS scene to the undo stack.
   * `nextScene` is a full SceneConfig.
   */
  function commitScene(nextScene) {
    // Push current scene onto undo stack
    undoStack.push(deepClone(state.scene));
    if (undoStack.length > MAX_UNDO) undoStack.shift();
    // Clear redo on new commit
    redoStack = [];

    state = { ...state, scene: nextScene };
    _notify();
    _notifyHistoryChange();
  }

  function undo() {
    if (undoStack.length === 0) return;
    redoStack.push(deepClone(state.scene));
    const prev = undoStack.pop();
    state = { ...state, scene: prev };
    _notify();
    _notifyHistoryChange();
  }

  function redo() {
    if (redoStack.length === 0) return;
    undoStack.push(deepClone(state.scene));
    const next = redoStack.pop();
    state = { ...state, scene: next };
    _notify();
    _notifyHistoryChange();
  }

  function canUndo() { return undoStack.length > 0; }
  function canRedo() { return redoStack.length > 0; }

  // History-change listeners (for updating undo/redo button state)
  const historyListeners = new Set();
  function subscribeHistory(fn) {
    historyListeners.add(fn);
    return () => historyListeners.delete(fn);
  }
  function _notifyHistoryChange() {
    historyListeners.forEach((fn) => fn({ canUndo: canUndo(), canRedo: canRedo(), depth: undoStack.length }));
  }

  // ── Scene commit helpers — push undo, then apply ──────────────────────────
  function updateAnatomy(patch) {
    commitScene({
      ...state.scene,
      anatomy: { ...state.scene.anatomy, ...patch },
    });
  }

  function updateRealism(patch) {
    commitScene({
      ...state.scene,
      realism: { ...state.scene.realism, ...patch },
    });
  }

  function updateAcquisition(patch) {
    commitScene({
      ...state.scene,
      acquisition: { ...state.scene.acquisition, ...patch },
    });
  }

  function updatePathology(patch) {
    commitScene({
      ...state.scene,
      pathology: { ...state.scene.pathology, ...patch },
    });
  }

  function updateArtifacts(patch) {
    commitScene({
      ...state.scene,
      artifacts: { ...state.scene.artifacts, ...patch },
    });
  }

  function replaceScene(newScene) {
    commitScene(deepClone(newScene));
  }

  function updateSeed(seed) {
    updateAnatomy({ render_seed: seed });
  }

  // ── Presentation State Mutation Helpers ──────────────────────────────────
  // CRITICAL: These functions modify presentation state stores ONLY and
  // NEVER touch state.scene, so they NEVER trigger /api/render/* preview renders.

  function updateVisualization(patch) {
    state = {
      ...state,
      visualization: { ...state.visualization, ...patch },
    };
    _notify();
  }

  function updateAnnotations(patch) {
    state = {
      ...state,
      annotations: { ...state.annotations, ...patch },
    };
    _notify();
  }

  function updateMeasurements(patch) {
    state = {
      ...state,
      measurements: { ...state.measurements, ...patch },
    };
    _notify();
  }

  function updateComposition(patch) {
    state = {
      ...state,
      composition: { ...state.composition, ...patch },
    };
    _notify();
  }

  function updateExport(patch) {
    state = {
      ...state,
      export: { ...state.export, ...patch },
    };
    _notify();
  }

  function setActiveTab(tab) {
    state = { ...state, activeTab: tab };
    _notify();
  }

  function setActiveAnnotationTool(tool) {
    state = { ...state, activeAnnotationTool: tool };
    _notify();
  }

  function setSelectedAnnotationId(id) {
    state = { ...state, selectedAnnotationId: id };
    _notify();
  }

  function setViewMode(mode) {
    state = { ...state, viewMode: mode };
    _notify();
  }

  function setCurrentProjectId(id) {
    state = { ...state, currentProjectId: id };
    _notify();
  }

  return {
    // Core
    getState,
    subscribe,
    setState,

    // Undo/Redo
    undo,
    redo,
    canUndo,
    canRedo,
    subscribeHistory,

    // Scene commit helpers (all undoable)
    updateAnatomy,
    updateRealism,
    updateAcquisition,
    updatePathology,
    updateArtifacts,
    replaceScene,
    updateSeed,

    // Presentation state helpers (never trigger scene renders)
    updateVisualization,
    updateAnnotations,
    updateMeasurements,
    updateComposition,
    updateExport,
    setActiveTab,
    setActiveAnnotationTool,
    setSelectedAnnotationId,
    setViewMode,
    setCurrentProjectId,

    // Accessors
    getDefaultScene: () => deepClone(DEFAULT_SCENE),
    getDefaultVisualization: () => deepClone(DEFAULT_VISUALIZATION),
    getDefaultAnnotations: () => deepClone(DEFAULT_ANNOTATIONS),
    getDefaultMeasurements: () => deepClone(DEFAULT_MEASUREMENTS),
    getDefaultComposition: () => deepClone(DEFAULT_COMPOSITION),
    getDefaultExport: () => deepClone(DEFAULT_EXPORT),
    deepClone,
  };
})();
