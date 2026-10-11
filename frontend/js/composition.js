/**
 * OCTForge Composition Module.
 *
 * Responsibilities:
 *  - Multi-panel educational and publication figure composition.
 *  - Layout arrangements: Single, 1x2, 2x1, 1x3, 3x1, 2x2, 1x4, 4x1, Grid.
 *  - Panel manager (add, remove, arrange, assign source render, badges, titles).
 *  - Per-panel visualization, auto-labeling, and scale bar overrides.
 *  - Figure title, subtitle, layer color legend, and synthetic metadata banner.
 *  - Live figure assembly via POST /api/compose.
 *
 * CRITICAL ARCHITECTURAL LAW:
 * Composition operates purely on existing cached RenderEntry references.
 * It NEVER invokes forward optical simulation or calls /api/render/*.
 */

const Composition = (function () {

  let debounceTimer = null;

  // ── Compose Figure from Backend ──────────────────────────────────────────
  async function executeCompose() {
    const currentState = State.getState();
    let compConfig = State.deepClone(currentState.composition);

    // If no panels yet, auto-populate with the current render as panel 1
    if (!compConfig.panels || compConfig.panels.length === 0) {
      if (currentState.renderId) {
        compConfig.panels = [
          {
            render_id: currentState.renderId,
            label: "A",
            title: currentState.scene.meta.name || "Macula",
            auto_label: false,
            show_scale_bar: true,
          }
        ];
        State.updateComposition({ panels: compConfig.panels });
      } else {
        return;
      }
    }

    try {
      const res = await API.composeFigure(compConfig);
      if (res && res.image) {
        State.setState({ composedImage: res.image });
        const compImg = document.getElementById("composed-img");
        if (compImg) {
          compImg.src = res.image;
        }
      }
    } catch (err) {
      console.warn("Composition API call failed:", err);
    }
  }

  function scheduleCompose(delayMs = 200) {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      executeCompose();
    }, delayMs);
  }

  // ── Switch Canvas View Mode ──────────────────────────────────────────────
  function setViewMode(mode) {
    State.setViewMode(mode);

    const bscanVp = document.getElementById("preview-viewport");
    const compVp  = document.getElementById("composed-viewport");
    const badge   = document.getElementById("canvas-mode-badge");

    const btnBscan = document.getElementById("view-btn-bscan");
    const btnComp  = document.getElementById("view-btn-composition");

    if (mode === "composition") {
      if (bscanVp) bscanVp.style.display = "none";
      if (compVp)  compVp.style.display = "flex";
      if (badge)   badge.textContent = "COMPOSED";
      if (btnBscan) btnBscan.classList.remove("active");
      if (btnComp)  btnComp.classList.add("active");
      executeCompose();
    } else {
      if (bscanVp) bscanVp.style.display = "block";
      if (compVp)  compVp.style.display = "none";
      if (badge)   badge.textContent = "B-SCAN";
      if (btnBscan) btnBscan.classList.add("active");
      if (btnComp)  btnComp.classList.remove("active");
    }
  }

  // ── Add Panel Helpers ────────────────────────────────────────────────────
  function addPanel(renderId = null, label = null, title = null) {
    const currentState = State.getState();
    const panels = [...(currentState.composition.panels || [])];
    const nextIdx = panels.length;
    const defaultLabels = ["A", "B", "C", "D", "E", "F", "G", "H"];

    const rId = renderId || currentState.renderId || "placeholder";
    const lbl = label || defaultLabels[nextIdx % defaultLabels.length];
    const ttl = title || (nextIdx === 0 ? "Normal Control" : `Pathology Case ${nextIdx}`);

    panels.push({
      render_id: rId,
      label: lbl,
      title: ttl,
      auto_label: false,
      show_scale_bar: true,
      scale_bar_color: "#FFFFFF",
    });

    State.updateComposition({ panels });
    buildPanel();
    scheduleCompose();
  }

  function removePanel(idx) {
    const panels = (State.getState().composition.panels || []).filter((_, i) => i !== idx);
    State.updateComposition({ panels });
    buildPanel();
    scheduleCompose();
  }

  // ── UI Construction ──────────────────────────────────────────────────────
  function buildPanel() {
    const container = document.getElementById("composition-body");
    if (!container) return;
    container.innerHTML = "";

    const comp = State.getState().composition;
    const panels = comp.panels || [];

    // ── 1. Layout & Geometry Card ──────────────────────────────────────────
    const layoutCard = document.createElement("div");
    layoutCard.className = "section-card";

    const layoutHeader = document.createElement("div");
    layoutHeader.className = "section-card-title";
    layoutHeader.innerHTML = `
      <span>Figure composition</span>
      <button class="btn-primary" id="btn-compose-now" style="font-size:11px;padding:3px 8px;">Compose</button>
    `;
    layoutCard.appendChild(layoutHeader);

    const layoutSelect = Controls.makeSelect({
      label: "Layout Arrangement",
      value: comp.layout,
      options: [
        { value: "1x2",    label: "1 × 2 Side-by-Side (A-B)" },
        { value: "2x1",    label: "2 × 1 Vertical Stack" },
        { value: "1x3",    label: "1 × 3 Horizontal Series (A-B-C)" },
        { value: "3x1",    label: "3 × 1 Vertical Series" },
        { value: "2x2",    label: "2 × 2 Quad Grid (A-B-C-D)" },
        { value: "1x4",    label: "1 × 4 Horizontal Strip" },
        { value: "single", label: "Single Panel" },
        { value: "grid",   label: "Auto-Fit Grid" },
      ],
      onChange: (layout) => {
        State.updateComposition({ layout });
        scheduleCompose();
      },
    });
    layoutCard.appendChild(layoutSelect);

    const spacingSlider = Controls.makeSlider({
      label: "Panel Spacing", unit: "px", min: 0, max: 80, step: 2, value: comp.spacing,
      decimals: 0,
      onChange: (spacing) => {
        State.updateComposition({ spacing });
        scheduleCompose();
      },
    });
    layoutCard.appendChild(spacingSlider);

    const paddingSlider = Controls.makeSlider({
      label: "Canvas Padding", unit: "px", min: 0, max: 80, step: 2, value: comp.padding,
      decimals: 0,
      onChange: (padding) => {
        State.updateComposition({ padding });
        scheduleCompose();
      },
    });
    layoutCard.appendChild(paddingSlider);

    container.appendChild(layoutCard);

    // ── 2. Figure Titles & Legends Card ────────────────────────────────────
    const titleCard = document.createElement("div");
    titleCard.className = "section-card";

    const titleHeader = document.createElement("div");
    titleHeader.className = "section-card-title";
    titleHeader.textContent = "Headers & Legends";
    titleCard.appendChild(titleHeader);

    // Title input
    const tRow = document.createElement("div");
    tRow.className = "ctrl-row";
    tRow.innerHTML = `<div class="ctrl-label"><span class="ctrl-name">Figure Title</span></div>`;
    const tInput = document.createElement("input");
    tInput.type = "text";
    tInput.value = comp.title || "";
    tInput.style.width = "100%";
    tInput.style.padding = "4px 8px";
    tInput.style.background = "var(--inset)";
    tInput.style.border = "1px solid var(--border-strong)";
    tInput.style.borderRadius = "var(--r-sm)";
    tInput.style.color = "var(--text-strong)";
    tInput.addEventListener("input", (e) => {
      State.updateComposition({ title: e.target.value });
      scheduleCompose(400);
    });
    tRow.appendChild(tInput);
    titleCard.appendChild(tRow);

    // Subtitle input
    const subRow = document.createElement("div");
    subRow.className = "ctrl-row";
    subRow.innerHTML = `<div class="ctrl-label"><span class="ctrl-name">Subtitle / Clinical Vignette</span></div>`;
    const subInput = document.createElement("input");
    subInput.type = "text";
    subInput.value = comp.subtitle || "";
    subInput.style.width = "100%";
    subInput.style.padding = "4px 8px";
    subInput.style.background = "var(--inset)";
    subInput.style.border = "1px solid var(--border-strong)";
    subInput.style.borderRadius = "var(--r-sm)";
    subInput.style.color = "var(--text-strong)";
    subInput.addEventListener("input", (e) => {
      State.updateComposition({ subtitle: e.target.value });
      scheduleCompose(400);
    });
    subRow.appendChild(subInput);
    titleCard.appendChild(subRow);

    const legendToggle = Controls.makeToggle({
      label: "Show Layer Color Legend Bar",
      value: comp.show_legend,
      onChange: (v) => {
        State.updateComposition({ show_legend: v });
        scheduleCompose();
      },
    });
    titleCard.appendChild(legendToggle);

    const metaToggle = Controls.makeToggle({
      label: "Render Synthetic Notice Banner",
      value: comp.show_metadata_block,
      onChange: (v) => {
        State.updateComposition({ show_metadata_block: v });
        scheduleCompose();
      },
    });
    titleCard.appendChild(metaToggle);

    container.appendChild(titleCard);

    // ── 3. Panel Slots Manager ─────────────────────────────────────────────
    const panelsCard = document.createElement("div");
    panelsCard.className = "section-card";

    const panelsHeader = document.createElement("div");
    panelsHeader.className = "section-card-title";
    panelsHeader.innerHTML = `
      <span>Panels List (${panels.length})</span>
      <button class="btn-tool-action" id="btn-add-panel" style="font-size:11px;">+ Add Panel</button>
    `;
    panelsCard.appendChild(panelsHeader);

    const panelsList = document.createElement("div");
    panelsList.className = "instance-list-items";

    if (panels.length === 0) {
      panelsList.innerHTML = `<div class="instance-empty">No panels configured. Click "+ Add Panel" or "Switch to Composed View".</div>`;
    } else {
      panels.forEach((p, idx) => {
        const pEl = document.createElement("div");
        pEl.className = "instance-card";

        const pHeader = document.createElement("div");
        pHeader.className = "instance-card-header";
        pHeader.innerHTML = `
          <span class="instance-title">Panel ${idx + 1} (${p.label || "?"}): ${p.title || "Untitled"}</span>
          <button class="btn-remove" title="Remove panel">&times;</button>
        `;
        pHeader.querySelector(".btn-remove").addEventListener("click", () => removePanel(idx));
        pEl.appendChild(pHeader);

        const pBody = document.createElement("div");
        pBody.className = "instance-card-body";

        // Label input
        const lblRow = document.createElement("div");
        lblRow.className = "inst-row";
        lblRow.innerHTML = `<div class="inst-label"><span class="inst-name">Badge Label</span></div>`;
        const lblInput = document.createElement("input");
        lblInput.type = "text";
        lblInput.value = p.label || "";
        lblInput.style.width = "60px";
        lblInput.style.padding = "2px 6px";
        lblInput.style.background = "var(--inset)";
        lblInput.style.border = "1px solid var(--border-strong)";
        lblInput.style.borderRadius = "var(--r-sm)";
        lblInput.style.color = "var(--text-strong)";
        lblInput.addEventListener("input", (e) => {
          panels[idx].label = e.target.value;
          State.updateComposition({ panels: [...panels] });
          scheduleCompose(300);
        });
        lblRow.appendChild(lblInput);
        pBody.appendChild(lblRow);

        // Title input
        const ttlRow = document.createElement("div");
        ttlRow.className = "inst-row";
        ttlRow.innerHTML = `<div class="inst-label"><span class="inst-name">Panel Subtitle</span></div>`;
        const ttlInput = document.createElement("input");
        ttlInput.type = "text";
        ttlInput.value = p.title || "";
        ttlInput.style.width = "100%";
        ttlInput.style.padding = "2px 6px";
        ttlInput.style.background = "var(--inset)";
        ttlInput.style.border = "1px solid var(--border-strong)";
        ttlInput.style.borderRadius = "var(--r-sm)";
        ttlInput.style.color = "var(--text-strong)";
        ttlInput.addEventListener("input", (e) => {
          panels[idx].title = e.target.value;
          State.updateComposition({ panels: [...panels] });
          scheduleCompose(300);
        });
        ttlRow.appendChild(ttlInput);
        pBody.appendChild(ttlRow);

        // Auto-label toggle
        const autoLabelToggle = Controls.makeToggle({
          label: "Auto-Label Layers",
          value: p.auto_label,
          onChange: (v) => {
            panels[idx].auto_label = v;
            State.updateComposition({ panels: [...panels] });
            scheduleCompose();
          },
        });
        pBody.appendChild(autoLabelToggle);

        // Scale bar toggle
        const scaleToggle = Controls.makeToggle({
          label: "Draw Scale Bars",
          value: p.show_scale_bar,
          onChange: (v) => {
            panels[idx].show_scale_bar = v;
            State.updateComposition({ panels: [...panels] });
            scheduleCompose();
          },
        });
        pBody.appendChild(scaleToggle);

        // Assign current active render button
        const assignBtn = document.createElement("button");
        assignBtn.className = "btn-secondary";
        assignBtn.style.fontSize = "10px";
        assignBtn.style.marginTop = "4px";
        assignBtn.textContent = "Assign Current Active Render Reference";
        assignBtn.addEventListener("click", () => {
          const curRenderId = State.getState().renderId;
          if (curRenderId) {
            panels[idx].render_id = curRenderId;
            State.updateComposition({ panels: [...panels] });
            scheduleCompose();
          }
        });
        pBody.appendChild(assignBtn);

        pEl.appendChild(pBody);
        panelsList.appendChild(pEl);
      });
    }

    panelsCard.appendChild(panelsList);
    container.appendChild(panelsCard);

    // Wire Add Panel & Compose Now buttons
    const addBtn = panelsHeader.querySelector("#btn-add-panel");
    if (addBtn) {
      addBtn.addEventListener("click", () => addPanel());
    }

    const composeBtn = layoutHeader.querySelector("#btn-compose-now");
    if (composeBtn) {
      composeBtn.addEventListener("click", () => {
        setViewMode("composition");
        executeCompose();
      });
    }
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    buildPanel();

    const btnBscan = document.getElementById("view-btn-bscan");
    const btnComp  = document.getElementById("view-btn-composition");

    if (btnBscan) btnBscan.addEventListener("click", () => setViewMode("bscan"));
    if (btnComp)  btnComp.addEventListener("click", () => setViewMode("composition"));

    // If renderId becomes available for the first time and composition has no panels,
    // prepare a default panel
    let initialRenderSet = false;
    State.subscribe((s) => {
      if (s.renderId && !initialRenderSet && (!s.composition.panels || s.composition.panels.length === 0)) {
        initialRenderSet = true;
        addPanel(s.renderId, "A", s.scene.meta.name || "Normal Macula");
      }
    });
  }

  return {
    init,
    buildPanel,
    executeCompose,
    setViewMode,
    addPanel,
    removePanel,
  };
})();
