/**
 * OCTForge Visualization Module.
 *
 * Responsibilities:
 *  - Colormap mode switching (grayscale, colored_layer, hybrid, educational).
 *  - Per-layer color overrides, opacity, and visibility controls.
 *  - Palette preset management (Clinical, Colorblind-Safe, Educational, Custom).
 *  - Live visualization updates via POST /api/visualize using cached RenderEntry.
 *
 * CRITICAL ARCHITECTURAL LAW:
 * All visualization changes modify state.visualization only and call /api/visualize.
 * They NEVER call /api/render/preview or /api/render/final.
 */

const Visualization = (function () {

  const PALETTE_DEFINITIONS = {
    clinical: {
      RNFL:    "#FF9800",
      GCL:     "#00BCD4",
      IPL:     "#2196F3",
      INL:     "#4CAF50",
      OPL:     "#8BC34A",
      ONL:     "#3F51B5",
      EZ:      "#FFEB3B",
      RPE:     "#E91E63",
      CHOROID: "#795548",
      SCLERA:  "#9E9E9E",
      VESSEL:  "#D32F2F",
    },
    colorblind_safe: {
      RNFL:    "#E69F00",
      GCL:     "#56B4E9",
      IPL:     "#009E73",
      INL:     "#F0E442",
      OPL:     "#0072B2",
      ONL:     "#D55E00",
      EZ:      "#CC79A7",
      RPE:     "#882255",
      CHOROID: "#44AA99",
      SCLERA:  "#999999",
      VESSEL:  "#117733",
    },
    educational: {
      RNFL:    "#FF5722",
      GCL:     "#00E5FF",
      IPL:     "#2979FF",
      INL:     "#00E676",
      OPL:     "#AEEA00",
      ONL:     "#AA00FF",
      EZ:      "#FFD600",
      RPE:     "#FF1744",
      CHOROID: "#8D6E63",
      SCLERA:  "#78909C",
      VESSEL:  "#C2185B",
    },
  };

  const LAYER_NAMES = [
    "RNFL", "GCL", "IPL", "INL", "OPL", "ONL", "EZ", "RPE", "CHOROID", "SCLERA", "VESSEL"
  ];

  let debounceTimer = null;

  // ── Debounced API application ────────────────────────────────────────────
  function applyVisualization(delayMs = 120) {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      _executeVisualize();
    }, delayMs);
  }

  async function _executeVisualize() {
    const currentState = State.getState();
    const renderId = currentState.renderId;
    if (!renderId) return;

    try {
      const res = await API.visualize(renderId, currentState.visualization);
      if (res && res.image) {
        State.setState({ styledPreviewImage: res.image });
        const imgEl = document.getElementById("bscan-img");
        if (imgEl) {
          imgEl.src = res.image;
        }
      }
    } catch (err) {
      console.warn("Visualization API call failed:", err);
    }
  }

  // ── UI Construction ──────────────────────────────────────────────────────
  function buildPanel() {
    const container = document.getElementById("visualization-body");
    if (!container) return;
    container.innerHTML = "";

    const visConfig = State.getState().visualization;

    // ── 1. Visualization Mode Card ─────────────────────────────────────────
    const modeCard = document.createElement("div");
    modeCard.className = "section-card";

    const modeHeader = document.createElement("div");
    modeHeader.className = "section-card-title";
    modeHeader.textContent = "Visualization Mode";
    modeCard.appendChild(modeHeader);

    const modeSelect = Controls.makeSelect({
      label: "Mode",
      value: visConfig.mode,
      options: [
        { value: "colored_layer", label: "Colored Layers (Anatomical)" },
        { value: "grayscale",     label: "Grayscale (Raw OCT)" },
        { value: "hybrid",        label: "Hybrid (Pathology Focus)" },
        { value: "educational",   label: "Educational (Vivid Contrast)" },
      ],
      onChange: (mode) => {
        State.updateVisualization({ mode });
        applyVisualization();
      },
    });
    modeCard.appendChild(modeSelect);

    const paletteSelect = Controls.makeSelect({
      label: "Palette Preset",
      value: visConfig.palette,
      options: [
        { value: "clinical",        label: "Clinical (Standard)" },
        { value: "colorblind_safe", label: "Color-Blind Safe (Okabe-Ito / Tol)" },
        { value: "educational",     label: "Educational (High Contrast)" },
        { value: "custom",          label: "Custom (User Overrides)" },
      ],
      onChange: (palette) => {
        if (PALETTE_DEFINITIONS[palette]) {
          const updatedLayers = { ...visConfig.layers };
          Object.entries(PALETTE_DEFINITIONS[palette]).forEach(([layer, col]) => {
            updatedLayers[layer] = {
              ...(updatedLayers[layer] || { opacity: 0.6, visible: true }),
              color: col,
            };
          });
          State.updateVisualization({ palette, layers: updatedLayers });
        } else {
          State.updateVisualization({ palette });
        }
        buildPanel();
        applyVisualization();
      },
    });
    modeCard.appendChild(paletteSelect);

    const opacitySlider = Controls.makeSlider({
      label: "Global Opacity",
      min: 0.0, max: 1.0, step: 0.05, value: visConfig.global_opacity,
      decimals: 2,
      onChange: (v) => {
        State.updateVisualization({ global_opacity: v });
        applyVisualization();
      },
    });
    modeCard.appendChild(opacitySlider);

    const pathologyToggle = Controls.makeToggle({
      label: "Highlight Pathology / Fluid (Hybrid)",
      value: visConfig.highlight_pathology,
      onChange: (v) => {
        State.updateVisualization({ highlight_pathology: v });
        applyVisualization();
      },
    });
    modeCard.appendChild(pathologyToggle);

    container.appendChild(modeCard);

    // ── 2. Per-Layer Styling Table ─────────────────────────────────────────
    const layerCard = document.createElement("div");
    layerCard.className = "section-card";

    const layerHeader = document.createElement("div");
    layerHeader.className = "section-card-title";
    layerHeader.innerHTML = `
      <span>Layer styling</span>
      <button class="btn-icon" id="btn-reset-vis" title="Reset to defaults" style="width:24px;height:24px;"><svg class="icon" aria-hidden="true"><use href="assets/icons.svg#shuffle"></use></svg></button>
    `;
    layerCard.appendChild(layerHeader);

    const tableWrapper = document.createElement("div");
    tableWrapper.style.overflowX = "auto";

    const table = document.createElement("table");
    table.className = "layer-table";
    table.innerHTML = `
      <thead>
        <tr>
          <th style="width:24px;">Vis</th>
          <th>Layer</th>
          <th style="width:28px;">Color</th>
          <th style="width:100px;">Opacity</th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    const tbody = table.querySelector("tbody");

    LAYER_NAMES.forEach((name) => {
      const layerData = visConfig.layers[name] || {
        color: PALETTE_DEFINITIONS.clinical[name] || "#FFFFFF",
        opacity: 0.6,
        visible: true,
      };

      const tr = document.createElement("tr");

      // 1. Visibility checkbox
      const tdVis = document.createElement("td");
      const visCb = document.createElement("input");
      visCb.type = "checkbox";
      visCb.checked = layerData.visible !== false;
      visCb.title = `Toggle ${name} visibility`;
      visCb.addEventListener("change", (e) => {
        const nextLayers = { ...State.getState().visualization.layers };
        nextLayers[name] = { ...nextLayers[name], visible: e.target.checked };
        State.updateVisualization({ layers: nextLayers, palette: "custom" });
        applyVisualization();
      });
      tdVis.appendChild(visCb);
      tr.appendChild(tdVis);

      // 2. Layer name
      const tdName = document.createElement("td");
      tdName.textContent = name;
      tdName.style.fontWeight = "600";
      tdName.className = `lc-${name.toLowerCase()}`;
      tr.appendChild(tdName);

      // 3. Color input
      const tdColor = document.createElement("td");
      const colInput = document.createElement("input");
      colInput.type = "color";
      colInput.className = "layer-color-input";
      colInput.value = layerData.color || "#FFFFFF";
      colInput.title = `Choose ${name} color`;
      colInput.addEventListener("input", (e) => {
        const nextLayers = { ...State.getState().visualization.layers };
        nextLayers[name] = { ...nextLayers[name], color: e.target.value };
        State.updateVisualization({ layers: nextLayers, palette: "custom" });
        applyVisualization();
      });
      tdColor.appendChild(colInput);
      tr.appendChild(tdColor);

      // 4. Opacity slider
      const tdOpacity = document.createElement("td");
      const opSlider = document.createElement("input");
      opSlider.type = "range";
      opSlider.min = 0.0;
      opSlider.max = 1.0;
      opSlider.step = 0.05;
      opSlider.value = layerData.opacity !== undefined ? layerData.opacity : 0.6;
      opSlider.title = `${name} opacity (${Math.round(opSlider.value * 100)}%)`;
      opSlider.addEventListener("input", (e) => {
        const val = parseFloat(e.target.value);
        const nextLayers = { ...State.getState().visualization.layers };
        nextLayers[name] = { ...nextLayers[name], opacity: val };
        State.updateVisualization({ layers: nextLayers, palette: "custom" });
        applyVisualization();
      });
      tdOpacity.appendChild(opSlider);
      tr.appendChild(tdOpacity);

      tbody.appendChild(tr);
    });

    tableWrapper.appendChild(table);
    layerCard.appendChild(tableWrapper);
    container.appendChild(layerCard);

    // Reset button
    const resetBtn = layerHeader.querySelector("#btn-reset-vis");
    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        State.updateVisualization(State.getDefaultVisualization());
        buildPanel();
        applyVisualization();
      });
    }
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    buildPanel();

    // Re-apply visualization whenever renderId changes from a fresh render
    let lastRenderId = null;
    State.subscribe((s) => {
      if (s.renderId && s.renderId !== lastRenderId) {
        lastRenderId = s.renderId;
        // If mode is not grayscale, apply styled visualization
        if (s.visualization && s.visualization.mode !== "grayscale") {
          applyVisualization(50);
        }
      }
    });
  }

  return {
    init,
    buildPanel,
    applyVisualization,
  };
})();
