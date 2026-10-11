/**
 * OCTForge Generator Module.
 *
 * Builds all five generator panels into the settings rail using the
 * Controls factory functions.  All state mutations go through the shared
 * undo/redo-aware State helpers (updateAnatomy, updateRealism, etc.).
 *
 * Panel structure:
 *   Anatomy    — basic: fovea depth/width, overall thickness.
 *                advanced: all geometry, per-layer thicknesses, all reflectivities.
 *   Pathology  — instance-list editor, enabled toggle.
 *   Acquisition — all AcquisitionConfig fields.
 *   Realism    — all RealismConfig fields.
 *   Artifacts  — instance-list editor, enabled toggle.
 *
 * NOTE: This module handles sceneConfig only.
 */

const Generator = (function () {

  // ── Pathology schema ──────────────────────────────────────────────────
  const PATHOLOGY_TYPE_OPTIONS = [
    { value: "drusen",                        label: "Drusen" },
    { value: "intraretinal_cyst",             label: "Intraretinal Cyst" },
    { value: "cystoid_macular_edema",         label: "Cystoid Macular Edema (CME)" },
    { value: "subretinal_fluid",              label: "Subretinal Fluid (SRF)" },
    { value: "pigment_epithelial_detachment", label: "Pigment Epithelial Detachment (PED)" },
    { value: "rpe_irregularity",              label: "RPE Irregularity" },
    { value: "rpe_attenuation",               label: "RPE Attenuation" },
    { value: "geographic_atrophy",            label: "Geographic Atrophy" },
    { value: "epiretinal_membrane",           label: "Epiretinal Membrane (ERM)" },
    { value: "vitreomacular_traction",        label: "Vitreomacular Traction (VMT)" },
    { value: "macular_hole",                  label: "Full-Thickness Macular Hole" },
    { value: "lamellar_macular_hole",         label: "Lamellar Macular Hole" },
    { value: "outer_retinal_disruption",      label: "Outer Retinal Disruption / EZ Loss" },
    { value: "hyperreflective_foci",          label: "Hyperreflective Foci" },
    { value: "retinal_thinning",              label: "Retinal Thinning / Layer Loss" },
  ];

  const PATHOLOGY_PARAM_SCHEMA = [
    { key: "severity",     label: "Severity",             min: 0.00, max: 1.00, step: 0.01, decimals: 2, default: 0.50 },
    { key: "size",         label: "Size (fraction)",      min: 0.01, max: 1.00, step: 0.01, decimals: 2, default: 0.10 },
    { key: "count",        label: "Count",                min: 1,    max: 50,   step: 1,    decimals: 0, default: 1 },
    { key: "location",     label: "Location (fraction)",  min: 0.00, max: 1.00, step: 0.01, decimals: 2, default: 0.50 },
    { key: "distribution", label: "Spread σ (fraction)",  min: 0.01, max: 0.50, step: 0.01, decimals: 2, default: 0.15 },
    { key: "reflectivity", label: "Reflectivity",         min: 0.00, max: 1.00, step: 0.01, decimals: 2, default: 0.50 },
    { key: "extent",       label: "Extent / Depth",       min: 0.00, max: 1.00, step: 0.01, decimals: 2, default: 0.50 },
    { key: "asymmetry",    label: "Asymmetry",            min: -1.0, max: 1.00, step: 0.01, decimals: 2, default: 0.00 },
    { key: "seed_offset",  label: "Seed Offset",          min: 0,    max: 9999, step: 1,    decimals: 0, default: 0 },
  ];

  // ── Artifact schema ───────────────────────────────────────────────────
  const ARTIFACT_TYPE_OPTIONS = [
    { value: "motion",      label: "Motion Artifact (Saccade)" },
    { value: "dropout",     label: "Signal Dropout" },
    { value: "shadowing",   label: "Media Shadowing" },
    { value: "saturation",  label: "Saturation Flare" },
    { value: "mirror",      label: "Mirror Artifact (Zero-OPD)" },
    { value: "poor_signal", label: "Poor Signal / Media Haze" },
    { value: "banding",     label: "Gain Banding" },
  ];

  const ARTIFACT_PARAM_SCHEMA = [
    { key: "severity",     label: "Severity",            min: 0.00, max: 1.00, step: 0.01, decimals: 2, default: 0.50 },
    { key: "location",     label: "Location (fraction)", min: 0.00, max: 1.00, step: 0.01, decimals: 2, default: 0.50 },
    { key: "size",         label: "Size (fraction)",     min: 0.01, max: 1.00, step: 0.01, decimals: 2, default: 0.15 },
    { key: "count",        label: "Count",               min: 1,    max: 50,   step: 1,    decimals: 0, default: 1 },
    { key: "extent",       label: "Extent / Depth",      min: 0.00, max: 1.00, step: 0.01, decimals: 2, default: 0.50 },
    { key: "asymmetry",    label: "Asymmetry / Sign",    min: -1.0, max: 1.00, step: 0.01, decimals: 2, default: 0.00 },
    { key: "reflectivity", label: "Reflectivity",        min: 0.00, max: 1.00, step: 0.01, decimals: 2, default: 0.50 },
    { key: "seed_offset",  label: "Seed Offset",         min: 0,    max: 9999, step: 1,    decimals: 0, default: 0 },
  ];

  let _pathologySeedCounter = 1;
  let _artifactSeedCounter  = 1;

  // ── Anatomy Panel ─────────────────────────────────────────────────────
  function buildAnatomyPanel() {
    const container = document.getElementById("panel-anatomy-body");
    if (!container) return;
    container.innerHTML = "";

    const s = State.getState().scene.anatomy;

    // ---- BASIC ----
    container.appendChild(Controls.makeSectionHeading("Foveal Shape"));

    container.appendChild(Controls.makeSlider({
      label: "Pit Depth", unit: "px", min: 0, max: 250, step: 1, value: s.fovea_pit_depth,
      decimals: 0,
      onChange: (v) => State.updateAnatomy({ fovea_pit_depth: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Pit Width (σ)", min: 0.05, max: 0.80, step: 0.005, value: s.fovea_pit_width,
      decimals: 3,
      onChange: (v) => State.updateAnatomy({ fovea_pit_width: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Overall Thickness",
      min: 100, max: 380, step: 1,
      value: _estimateTotalThickness(s),
      decimals: 0,
      advanced: false,
      onChange: (v) => {
        // Scale all layer thicknesses proportionally
        const current = State.getState().scene.anatomy;
        const currentTotal = _estimateTotalThickness(current);
        if (currentTotal <= 0) return;
        const scale = v / currentTotal;
        State.updateAnatomy({
          rnfl_thickness:    Math.round(current.rnfl_thickness    * scale),
          gcl_thickness:     Math.round(current.gcl_thickness     * scale),
          ipl_thickness:     Math.round(current.ipl_thickness     * scale),
          inl_thickness:     Math.round(current.inl_thickness     * scale),
          opl_thickness:     Math.round(current.opl_thickness     * scale),
          onl_thickness:     Math.round(current.onl_thickness     * scale),
          ez_thickness:      Math.round(current.ez_thickness      * scale),
          rpe_thickness:     Math.round(current.rpe_thickness     * scale),
          choroid_thickness: Math.round(current.choroid_thickness * scale),
        });
      },
    }));

    // ---- ADVANCED ----
    container.appendChild(Controls.makeSectionHeading("Foveal Geometry", true));

    container.appendChild(Controls.makeSlider({
      label: "Fovea Center", unit: "px", min: 64, max: 448, step: 1,
      value: s.fovea_center, decimals: 0, advanced: true,
      onChange: (v) => State.updateAnatomy({ fovea_center: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Curvature", min: -0.20, max: 0.20, step: 0.005, value: s.curvature,
      decimals: 3, advanced: true,
      onChange: (v) => State.updateAnatomy({ curvature: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "ILM Asymmetry", min: -1.0, max: 1.0, step: 0.02, value: s.boundary_asymmetry,
      decimals: 2, advanced: true,
      onChange: (v) => State.updateAnatomy({ boundary_asymmetry: v }),
    }));

    container.appendChild(Controls.makeSectionHeading("Boundary Irregularity", true));

    container.appendChild(Controls.makeSlider({
      label: "Smoothness (σ)", min: 0.01, max: 0.50, step: 0.005, value: s.boundary_smoothness,
      decimals: 3, advanced: true,
      onChange: (v) => State.updateAnatomy({ boundary_smoothness: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Variation", unit: "px", min: 0, max: 20, step: 0.2, value: s.boundary_variation,
      decimals: 1, advanced: true,
      onChange: (v) => State.updateAnatomy({ boundary_variation: v }),
    }));

    container.appendChild(Controls.makeSectionHeading("Layer Thicknesses (px)", true));

    const layerThicknesses = [
      { key: "rnfl_thickness",    label: "RNFL",     min: 2,  max: 80,  layerColor: "lc-rnfl" },
      { key: "gcl_thickness",     label: "GCL",      min: 1,  max: 60,  layerColor: "lc-gcl" },
      { key: "ipl_thickness",     label: "IPL",      min: 1,  max: 60,  layerColor: "lc-ipl" },
      { key: "inl_thickness",     label: "INL",      min: 1,  max: 50,  layerColor: "lc-inl" },
      { key: "opl_thickness",     label: "OPL",      min: 1,  max: 50,  layerColor: "lc-opl" },
      { key: "onl_thickness",     label: "ONL",      min: 5,  max: 140, layerColor: "lc-onl" },
      { key: "ez_thickness",      label: "EZ / IS-OS", min: 4, max: 50, layerColor: "lc-ez" },
      { key: "rpe_thickness",     label: "RPE + BM", min: 2,  max: 20,  layerColor: "lc-rpe" },
      { key: "choroid_thickness", label: "Choroid",  min: 20, max: 400, layerColor: "lc-choroid" },
    ];

    layerThicknesses.forEach(({ key, label, min, max, layerColor }) => {
      container.appendChild(Controls.makeSlider({
        label, unit: "px", min, max, step: 1, value: s[key],
        decimals: 0, advanced: true, layerColor,
        onChange: (v) => State.updateAnatomy({ [key]: v }),
      }));
    });

    container.appendChild(Controls.makeSectionHeading("Layer Reflectivities", true));

    const reflectivities = [
      { key: "rnfl_reflectivity",    label: "RNFL",     layerColor: "lc-rnfl" },
      { key: "gcl_reflectivity",     label: "GCL",      layerColor: "lc-gcl" },
      { key: "ipl_reflectivity",     label: "IPL",      layerColor: "lc-ipl" },
      { key: "inl_reflectivity",     label: "INL",      layerColor: "lc-inl" },
      { key: "opl_reflectivity",     label: "OPL",      layerColor: "lc-opl" },
      { key: "onl_reflectivity",     label: "ONL",      layerColor: "lc-onl" },
      { key: "ez_reflectivity",      label: "EZ / IS-OS", layerColor: "lc-ez" },
      { key: "rpe_reflectivity",     label: "RPE",      layerColor: "lc-rpe" },
      { key: "choroid_reflectivity", label: "Choroid",  layerColor: "lc-choroid" },
      { key: "vitreous_reflectivity", label: "Vitreous", layerColor: "" },
    ];

    reflectivities.forEach(({ key, label, layerColor }) => {
      container.appendChild(Controls.makeSlider({
        label, min: 0.0, max: 1.0, step: 0.01, value: s[key],
        decimals: 2, advanced: true, layerColor,
        onChange: (v) => State.updateAnatomy({ [key]: v }),
      }));
    });
  }

  function _estimateTotalThickness(a) {
    return (a.rnfl_thickness || 0) + (a.gcl_thickness || 0) + (a.ipl_thickness || 0)
         + (a.inl_thickness  || 0) + (a.opl_thickness || 0) + (a.onl_thickness || 0)
         + (a.ez_thickness   || 0) + (a.rpe_thickness || 0) + (a.choroid_thickness || 0);
  }

  // ── Pathology Panel ───────────────────────────────────────────────────
  let _pathologyListCtrl = null;

  function buildPathologyPanel() {
    const container = document.getElementById("panel-pathology-body");
    if (!container) return;
    container.innerHTML = "";

    const s = State.getState().scene.pathology;

    // Enable toggle
    container.appendChild(Controls.makeToggle({
      label: "Enable Pathology",
      value: s.enabled,
      onChange: (v) => State.updatePathology({ enabled: v }),
    }));

    // Instance list editor
    _pathologyListCtrl = Controls.makeInstanceList({
      getInstances: () => State.getState().scene.pathology.instances || [],
      setInstances: (arr) => State.updatePathology({ enabled: arr.length > 0 || State.getState().scene.pathology.enabled, instances: arr }),
      typeOptions:   PATHOLOGY_TYPE_OPTIONS,
      paramSchema:   PATHOLOGY_PARAM_SCHEMA,
      makeDefault:   (type) => ({
        type,
        severity:     0.50,
        size:         0.10,
        count:        1,
        location:     0.50,
        distribution: 0.15,
        reflectivity: 0.50,
        extent:       0.50,
        asymmetry:    0.00,
        seed_offset:  (_pathologySeedCounter++ * 73) % 10000,
      }),
      cardClass:       "pathology-card",
      titleColorClass: "pathology-title-color",
    });

    container.appendChild(_pathologyListCtrl.el);
  }

  // ── Acquisition Panel ─────────────────────────────────────────────────
  function buildAcquisitionPanel() {
    const container = document.getElementById("panel-acquisition-body");
    if (!container) return;
    container.innerHTML = "";

    const s = State.getState().scene.acquisition;

    container.appendChild(Controls.makeSlider({
      label: "Scan Width", unit: "mm", min: 1.0, max: 16.0, step: 0.5, value: s.scan_width,
      decimals: 1,
      onChange: (v) => State.updateAcquisition({ scan_width: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Scan Depth", unit: "mm", min: 0.5, max: 5.0, step: 0.1, value: s.scan_depth,
      decimals: 1,
      onChange: (v) => State.updateAcquisition({ scan_depth: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Signal Strength", min: 0.0, max: 10.0, step: 0.1, value: s.signal_strength,
      decimals: 1,
      onChange: (v) => State.updateAcquisition({ signal_strength: v }),
    }));

    container.appendChild(Controls.makeSectionHeading("Sampling", true));

    container.appendChild(Controls.makeSlider({
      label: "A-scan Density", min: 64, max: 2048, step: 64, value: s.a_scan_density,
      decimals: 0, advanced: true,
      onChange: (v) => State.updateAcquisition({ a_scan_density: Math.round(v) }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Frame Averaging", min: 1, max: 64, step: 1, value: s.averaging,
      decimals: 0, advanced: true,
      onChange: (v) => State.updateAcquisition({ averaging: Math.round(v) }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "SNR", unit: "dB", min: 0.0, max: 60.0, step: 0.5, value: s.snr_db,
      decimals: 1, advanced: true,
      onChange: (v) => State.updateAcquisition({ snr_db: v }),
    }));

    container.appendChild(Controls.makeSectionHeading("Output Resolution", true));

    container.appendChild(Controls.makeNumericInput({
      label: "Width (px)", min: 128, max: 2048, step: 64, value: s.width,
      decimals: 0, advanced: true,
      onChange: (v) => State.updateAcquisition({ width: Math.round(v), output_resolution: [State.getState().scene.acquisition.height, Math.round(v)] }),
    }));

    container.appendChild(Controls.makeNumericInput({
      label: "Height (px)", min: 128, max: 2048, step: 64, value: s.height,
      decimals: 0, advanced: true,
      onChange: (v) => State.updateAcquisition({ height: Math.round(v), output_resolution: [Math.round(v), State.getState().scene.acquisition.width] }),
    }));

    container.appendChild(Controls.makeSectionHeading("Optics", true));

    container.appendChild(Controls.makeSlider({
      label: "Axial Resolution", unit: "μm FWHM", min: 1.0, max: 20.0, step: 0.5, value: s.axial_resolution,
      decimals: 1, advanced: true,
      onChange: (v) => State.updateAcquisition({ axial_resolution: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Lateral Resolution", unit: "μm FWHM", min: 2.0, max: 50.0, step: 0.5, value: s.lateral_resolution,
      decimals: 1, advanced: true,
      onChange: (v) => State.updateAcquisition({ lateral_resolution: v }),
    }));
  }

  // ── Realism Panel ─────────────────────────────────────────────────────
  function buildRealismPanel() {
    const container = document.getElementById("panel-realism-body");
    if (!container) return;
    container.innerHTML = "";

    const s = State.getState().scene.realism;

    container.appendChild(Controls.makeSlider({
      label: "Speckle Amount", min: 0.0, max: 2.0, step: 0.05, value: s.speckle_amount,
      decimals: 2,
      onChange: (v) => State.updateRealism({ speckle_amount: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Contrast", min: 0.2, max: 3.0, step: 0.05, value: s.contrast,
      decimals: 2,
      onChange: (v) => State.updateRealism({ contrast: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Display Gamma", min: 0.5, max: 4.0, step: 0.05, value: s.display_gamma,
      decimals: 2,
      onChange: (v) => State.updateRealism({ display_gamma: v }),
    }));

    container.appendChild(Controls.makeSectionHeading("Window Level", true));

    container.appendChild(Controls.makeSlider({
      label: "Black Point", unit: "dB", min: -80, max: 0, step: 0.5, value: s.db_lo,
      decimals: 1, advanced: true,
      onChange: (v) => State.updateRealism({ db_lo: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "White Point", unit: "dB", min: -30, max: 10, step: 0.5, value: s.db_hi,
      decimals: 1, advanced: true,
      onChange: (v) => State.updateRealism({ db_hi: v }),
    }));

    container.appendChild(Controls.makeSectionHeading("Noise & Blur", true));

    container.appendChild(Controls.makeSlider({
      label: "Background Noise", unit: "dB", min: -60, max: -10, step: 0.5, value: s.background_noise,
      decimals: 1, advanced: true,
      onChange: (v) => State.updateRealism({ background_noise: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Intensity Nonuniformity", min: 0.0, max: 1.0, step: 0.01, value: s.intensity_nonuniformity,
      decimals: 2, advanced: true,
      onChange: (v) => State.updateRealism({ intensity_nonuniformity: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Detector Blur", unit: "px σ", min: 0.0, max: 5.0, step: 0.05, value: s.blur,
      decimals: 2, advanced: true,
      onChange: (v) => State.updateRealism({ blur: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Axial PSF Blur", unit: "px σ", min: 0.1, max: 10.0, step: 0.1, value: s.axial_blur,
      decimals: 1, advanced: true,
      onChange: (v) => State.updateRealism({ axial_blur: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Lateral PSF Blur", unit: "px σ", min: 0.1, max: 10.0, step: 0.1, value: s.lateral_blur,
      decimals: 1, advanced: true,
      onChange: (v) => State.updateRealism({ lateral_blur: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Boundary Jitter", unit: "px", min: 0.0, max: 10.0, step: 0.1, value: s.boundary_uncertainty,
      decimals: 1, advanced: true,
      onChange: (v) => State.updateRealism({ boundary_uncertainty: v }),
    }));

    container.appendChild(Controls.makeSlider({
      label: "Speckle Avg Count", min: 1, max: 32, step: 1, value: s.speckle_avg_count,
      decimals: 0, advanced: true,
      onChange: (v) => State.updateRealism({ speckle_avg_count: Math.round(v) }),
    }));
  }

  // ── Artifacts Panel ───────────────────────────────────────────────────
  let _artifactListCtrl = null;

  function buildArtifactsPanel() {
    const container = document.getElementById("panel-artifacts-body");
    if (!container) return;
    container.innerHTML = "";

    const s = State.getState().scene.artifacts;

    container.appendChild(Controls.makeToggle({
      label: "Enable Artifacts",
      value: s.enabled,
      onChange: (v) => State.updateArtifacts({ enabled: v }),
    }));

    _artifactListCtrl = Controls.makeInstanceList({
      getInstances: () => State.getState().scene.artifacts.instances || [],
      setInstances: (arr) => State.updateArtifacts({ enabled: arr.length > 0 || State.getState().scene.artifacts.enabled, instances: arr }),
      typeOptions:  ARTIFACT_TYPE_OPTIONS,
      paramSchema:  ARTIFACT_PARAM_SCHEMA,
      makeDefault:  (type) => ({
        type,
        severity:     0.50,
        location:     0.50,
        size:         0.15,
        count:        1,
        extent:       0.50,
        asymmetry:    0.00,
        reflectivity: 0.50,
        seed_offset:  (_artifactSeedCounter++ * 97) % 10000,
      }),
      cardClass:       "artifact-card",
      titleColorClass: "artifact-title-color",
    });

    container.appendChild(_artifactListCtrl.el);
  }

  // ── Rebuild all panels (called after replaceScene / randomize) ────────
  function rebuildAll() {
    buildAnatomyPanel();
    buildPathologyPanel();
    buildAcquisitionPanel();
    buildRealismPanel();
    buildArtifactsPanel();
  }

  // ── Trigger final render ───────────────────────────────────────────────
  async function triggerFinalRender() {
    const currentState = State.getState();
    State.setState({ isRendering: true });
    const startTime = performance.now();
    try {
      const res = await API.renderFinal(currentState.scene);
      const latency = performance.now() - startTime;
      State.setState({
        previewImage:     res.image,
        renderId:         res.render_id,
        cacheKey:         res.cache_key,
        isCached:         res.cached,
        renderLatencyMs:  latency,
        isRendering:      false,
        lastFinalResult:  res,
      });
      return res;
    } catch (err) {
      console.error("Final render failed:", err);
      State.setState({ isRendering: false });
      throw err;
    }
  }

  // ── Public init ────────────────────────────────────────────────────────
  function init() {
    buildAnatomyPanel();
    buildPathologyPanel();
    buildAcquisitionPanel();
    buildRealismPanel();
    buildArtifactsPanel();

    // Wire panel-header collapse (controls.js handles .panel-header clicks,
    // but panels are dynamically built so re-init after DOM population)
    document.querySelectorAll(".panel-header").forEach((header) => {
      // Guard: already wired by Controls.init() for existing panels.
      // Nothing extra needed here — collapse is delegated.
    });
  }

  return {
    init,
    rebuildAll,
    buildAnatomyPanel,
    buildPathologyPanel,
    buildAcquisitionPanel,
    buildRealismPanel,
    buildArtifactsPanel,
    triggerFinalRender,
    // Expose for external refresh after state restore
    refreshPathologyList: () => _pathologyListCtrl && _pathologyListCtrl.refresh(),
    refreshArtifactList:  () => _artifactListCtrl  && _artifactListCtrl.refresh(),
  };
})();
