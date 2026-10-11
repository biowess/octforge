/**
 * OCTForge Presets & Randomization Module.
 *
 * Responsibilities:
 *  - Browse curated clinical pathology presets from GET /api/presets/pathology.
 *  - Apply presets to current scene or add them directly into composition figures.
 *  - Controlled / bounded physiological scene randomization via POST /api/presets/randomize.
 *  - Pathology variant sequence generator via POST /api/presets/variants.
 *
 * CRITICAL ARCHITECTURAL LAW:
 * Loading a preset replaces state.scene via State.replaceScene() (which pushes an
 * undo snapshot) and triggers the generator preview path. Presets never bypass
 * the canonical SceneConfig validation.
 */

const Presets = (function () {

  let pathologyPresetsList = [];
  let generatedVariantsList = [];

  // ── Load Presets from Backend ────────────────────────────────────────────
  async function loadPathologyPresets() {
    try {
      const presets = await API.getPathologyPresets();
      if (Array.isArray(presets)) {
        pathologyPresetsList = presets;
        buildPanel();
      }
    } catch (err) {
      console.warn("Failed to load pathology presets:", err);
    }
  }

  // ── Apply Preset to Scene ────────────────────────────────────────────────
  async function applyPreset(presetId) {
    try {
      let scene = null;
      const found = pathologyPresetsList.find((p) => p.id === presetId);
      if (found && found.scene) {
        scene = found.scene;
      } else {
        scene = await API.getPathologyPreset(presetId);
      }

      if (scene) {
        State.replaceScene(scene);
        Generator.rebuildAll();
        Preview.triggerRender();
        console.log(`[OCTForge] Applied pathology preset: ${presetId}`);
      }
    } catch (err) {
      console.error("Failed to apply pathology preset:", err);
      if (typeof Dialogs !== "undefined") Dialogs.notify(`Failed to apply preset: ${err.message}`, "error");
    }
  }

  // ── Add Preset Directly to Multi-Panel Composition ───────────────────────
  async function addPresetToComposition(presetId, label = null) {
    try {
      let scene = null;
      const found = pathologyPresetsList.find((p) => p.id === presetId);
      if (found && found.scene) {
        scene = found.scene;
      } else {
        scene = await API.getPathologyPreset(presetId);
      }

      if (scene) {
        // Render preview of this preset to obtain a stable cached render_id
        const renderRes = await API.renderPreview(scene);
        if (renderRes && renderRes.render_id) {
          Composition.addPanel(renderRes.render_id, label, scene.meta.name || presetId);
          console.log(`[OCTForge] Added preset '${presetId}' to composition with render_id: ${renderRes.render_id}`);
        }
      }
    } catch (err) {
      console.error("Failed to add preset to composition:", err);
      if (typeof Dialogs !== "undefined") Dialogs.notify(`Failed to add preset to composition: ${err.message}`, "error");
    }
  }

  // ── Controlled Randomization ─────────────────────────────────────────────
  async function generateRandomScene(mode = "balanced", variance = 0.25, seed = null) {
    try {
      const currentScene = State.getState().scene;
      const res = await API.randomizeScene(currentScene, seed, mode, variance);
      if (res && res.anatomy) {
        State.replaceScene(res);
        Generator.rebuildAll();
        Preview.triggerRender();
        console.log("[OCTForge] Bounded randomize complete, seed:", res.anatomy.render_seed);
      }
    } catch (err) {
      console.error("Randomization failed:", err);
      if (typeof Dialogs !== "undefined") Dialogs.notify(`Randomization failed: ${err.message}`, "error");
    }
  }

  // ── Pathology Variant Generation ─────────────────────────────────────────
  async function generateVariants(presetId, count = 4, baseSeed = 42) {
    try {
      const res = await API.generateVariants(presetId, null, count, baseSeed);
      if (res && res.variants) {
        generatedVariantsList = res.variants;
        buildPanel();
        console.log(`[OCTForge] Generated ${res.variants.length} pathology variants for '${presetId}'`);
      }
    } catch (err) {
      console.error("Variant generation failed:", err);
      if (typeof Dialogs !== "undefined") Dialogs.notify(`Variant generation failed: ${err.message}`, "error");
    }
  }

  // ── UI Construction ──────────────────────────────────────────────────────
  function buildPanel() {
    const container = document.getElementById("presets-body");
    if (!container) return;
    container.innerHTML = "";

    // ── 1. Curated Clinical Pathology Presets Card ──────────────────────────
    const presetsCard = document.createElement("div");
    presetsCard.className = "section-card";

    const presetsHeader = document.createElement("div");
    presetsHeader.className = "section-card-title";
    presetsHeader.innerHTML = `
      <span>Clinical pathology library (${pathologyPresetsList.length})</span>
      <button class="btn-tool-action" id="btn-reload-presets" style="font-size:11px;">Reload</button>
    `;
    presetsCard.appendChild(presetsHeader);

    const presetsGrid = document.createElement("div");
    presetsGrid.className = "presets-grid";

    if (pathologyPresetsList.length === 0) {
      presetsGrid.innerHTML = `<div class="instance-empty">Loading curated pathology presets…</div>`;
    } else {
      pathologyPresetsList.forEach((preset) => {
        const pCard = document.createElement("div");
        pCard.className = "preset-card";

        pCard.innerHTML = `
          <div class="preset-card-header">
            <span class="preset-name">${preset.name}</span>
            <span class="preset-cat-badge">${preset.category || "Clinical"}</span>
          </div>
          <div class="preset-desc">${preset.description || ""}</div>
          <div class="preset-actions">
            <button class="btn-primary btn-apply" style="flex:1;font-size:11px;padding:3px 6px;">Load scene</button>
            <button class="btn-secondary btn-comp" style="font-size:11px;padding:3px 6px;">Add to figure</button>
          </div>
        `;

        pCard.querySelector(".btn-apply").addEventListener("click", () => applyPreset(preset.id));
        pCard.querySelector(".btn-comp").addEventListener("click", () => addPresetToComposition(preset.id));

        presetsGrid.appendChild(pCard);
      });
    }

    presetsCard.appendChild(presetsGrid);
    container.appendChild(presetsCard);

    // ── 2. Controlled / Bounded Randomize Card ──────────────────────────────
    const randCard = document.createElement("div");
    randCard.className = "section-card";

    const randHeader = document.createElement("div");
    randHeader.className = "section-card-title";
    randHeader.textContent = "Physiological Randomization";
    randCard.appendChild(randHeader);

    let randMode = "balanced";
    let randVariance = 0.25;

    const modeSelect = Controls.makeSelect({
      label: "Randomization Scope",
      value: randMode,
      options: [
        { value: "balanced",      label: "Balanced (Anatomy + Realism + Signal)" },
        { value: "anatomy_only",  label: "Anatomy Only (Fovea & Layers)" },
        { value: "realism_only",  label: "Realism Only (Speckle & Contrast)" },
        { value: "all",           label: "All Parameters" },
      ],
      onChange: (v) => { randMode = v; },
    });
    randCard.appendChild(modeSelect);

    const varSlider = Controls.makeSlider({
      label: "Variance Amplitude", unit: "%", min: 5, max: 100, step: 5, value: 25,
      decimals: 0,
      onChange: (v) => { randVariance = v / 100.0; },
    });
    randCard.appendChild(varSlider);

    const randBtn = document.createElement("button");
    randBtn.className = "btn-primary";
    randBtn.style.width = "100%";
    randBtn.style.marginTop = "4px";
    randBtn.textContent = "Generate random scene";
    randBtn.addEventListener("click", () => {
      generateRandomScene(randMode, randVariance);
    });
    randCard.appendChild(randBtn);

    container.appendChild(randCard);

    // ── 3. Pathology Variant Generator Card ─────────────────────────────────
    const varCard = document.createElement("div");
    varCard.className = "section-card";

    const varHeader = document.createElement("div");
    varHeader.className = "section-card-title";
    varHeader.textContent = "Pathology Variant Sequence";
    varCard.appendChild(varHeader);

    let varPreset = "cme";
    let varCount = 4;

    const varPresetSelect = Controls.makeSelect({
      label: "Base Pathology",
      value: varPreset,
      options: [
        { value: "cme",                      label: "Cystoid Macular Edema (CME)" },
        { value: "srf",                      label: "Subretinal Fluid (SRF)" },
        { value: "drusen",                   label: "Drusen (Dry AMD)" },
        { value: "ped",                      label: "Pigment Epithelial Detachment (PED)" },
        { value: "erm",                      label: "Epiretinal Membrane (ERM)" },
        { value: "macular_hole",             label: "Full-Thickness Macular Hole" },
        { value: "lamellar_hole",            label: "Lamellar Macular Hole" },
        { value: "geographic_atrophy",        label: "Geographic Atrophy (GA)" },
        { value: "vmt",                      label: "Vitreomacular Traction (VMT)" },
        { value: "outer_retinal_disruption", label: "Outer Retinal Disruption" },
        { value: "hyperreflective_foci",     label: "Hyperreflective Foci" },
      ],
      onChange: (v) => { varPreset = v; },
    });
    varCard.appendChild(varPresetSelect);

    const countSlider = Controls.makeSlider({
      label: "Variant Count", min: 2, max: 8, step: 1, value: 4,
      decimals: 0,
      onChange: (v) => { varCount = Math.round(v); },
    });
    varCard.appendChild(countSlider);

    const genVarBtn = document.createElement("button");
    genVarBtn.className = "btn-secondary";
    genVarBtn.style.width = "100%";
    genVarBtn.style.marginTop = "4px";
    genVarBtn.textContent = "Generate variants";
    genVarBtn.addEventListener("click", () => {
      generateVariants(varPreset, varCount, 42);
    });
    varCard.appendChild(genVarBtn);

    // Render generated variants gallery if any
    if (generatedVariantsList.length > 0) {
      const varGallery = document.createElement("div");
      varGallery.className = "instance-list-items";
      varGallery.style.marginTop = "8px";

      generatedVariantsList.forEach((variantScene, vIdx) => {
        const vEl = document.createElement("div");
        vEl.className = "instance-card";
        vEl.style.padding = "6px 8px";

        vEl.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:11px;font-weight:600;color:var(--accent);">${variantScene.meta.name}</span>
            <span style="font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums;">Seed: ${variantScene.anatomy.render_seed}</span>
          </div>
          <div style="display:flex;gap:4px;margin-top:4px;">
            <button class="btn-primary btn-load-var" style="flex:1;font-size:11px;padding:2px 4px;">Load variant</button>
            <button class="btn-secondary btn-comp-var" style="font-size:11px;padding:2px 4px;">Add to figure</button>
          </div>
        `;

        vEl.querySelector(".btn-load-var").addEventListener("click", () => {
          State.replaceScene(variantScene);
          Generator.rebuildAll();
          Preview.triggerRender();
        });

        vEl.querySelector(".btn-comp-var").addEventListener("click", async () => {
          const res = await API.renderPreview(variantScene);
          if (res && res.render_id) {
            Composition.addPanel(res.render_id, String.fromCharCode(65 + vIdx), variantScene.meta.name);
          }
        });

        varGallery.appendChild(vEl);
      });

      varCard.appendChild(varGallery);
    }

    container.appendChild(varCard);

    // Wire reload button
    const reloadBtn = presetsHeader.querySelector("#btn-reload-presets");
    if (reloadBtn) {
      reloadBtn.addEventListener("click", () => loadPathologyPresets());
    }
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    loadPathologyPresets();
    buildPanel();
  }

  return {
    init,
    buildPanel,
    loadPathologyPresets,
    applyPreset,
    addPresetToComposition,
    generateRandomScene,
    generateVariants,
  };
})();
