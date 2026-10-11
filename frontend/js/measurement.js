/**
 * OCTForge Measurement Module.
 *
 * Responsibilities:
 *  - Click-to-measure caliper tool on the canvas overlay.
 *  - Quantitative morphology readout panel derived from /api/measure.
 *  - Foveal pit depth, FWHM width, and Central Retinal Thickness (CRT / CFT).
 *  - Per-layer thickness table (mean, min, max, foveal, parafoveal).
 *  - Fluid and cystic lesion volumetric and spatial morphometry.
 *  - Calibrated physical scale bars (1.0 mm lateral, 200 μm axial).
 *
 * CRITICAL ARCHITECTURAL LAW:
 * Measurement operates strictly on cached RenderEntry data via /api/measure.
 * It NEVER calls /api/render/preview or /api/render/final.
 */

const Measurement = (function () {

  let latestMetrics = null;

  // ── Query Measurement Data from Backend ──────────────────────────────────
  async function refreshMetrics() {
    const currentState = State.getState();
    const renderId = currentState.renderId;
    if (!renderId) return;

    try {
      const calipers = currentState.annotations.items
        ? currentState.annotations.items
            .filter((i) => i.type === "caliper")
            .map((c) => ({
              id: c.id,
              label: c.label || "Caliper",
              p1: { x: c.x, y: c.y },
              p2: { x: c.x2 || c.x, y: c.y2 || c.y },
            }))
        : [];

      const res = await API.measure(renderId, calipers.length > 0 ? calipers : null, false);
      if (res && res.measurements) {
        latestMetrics = res.measurements;
        State.updateMeasurements({ data: latestMetrics });
        buildPanel();
        Annotation.redraw();
      }
    } catch (err) {
      console.warn("Measurement API call failed:", err);
    }
  }

  // ── Draw Scale Bars onto Canvas Overlay ──────────────────────────────────
  function drawScaleBars(ctx, canvasW, canvasH) {
    const measState = State.getState().measurements;
    const toggleScalebars = document.getElementById("toggle-show-scalebars");
    const showScaleBars = toggleScalebars ? toggleScalebars.checked : (measState ? measState.showScaleBars : true);

    if (!showScaleBars) return;

    const scene = State.getState().scene;
    const scanWidthMm = (scene && scene.acquisition && scene.acquisition.scan_width) || 6.0;
    const scanDepthMm = (scene && scene.acquisition && scene.acquisition.scan_depth) || 2.0;

    const umPerPxLat = (scanWidthMm * 1000.0) / canvasW;
    const umPerPxAx = (scanDepthMm * 1000.0) / canvasH;

    // 1.0 mm lateral bar
    const latBarPx = 1000.0 / umPerPxLat;
    // 200 μm axial bar
    const axBarPx = 200.0 / umPerPxAx;

    const color = (measState && measState.scaleBarColor) || "#FFFFFF";
    const margin = 24;

    const startX = canvasW - margin - latBarPx;
    const endX = canvasW - margin;
    const bottomY = canvasH - margin;
    const topY = bottomY - axBarPx;

    ctx.save();
    ctx.lineCap = "round";
    ctx.setLineDash([]);

    // 1. High contrast drop-shadow outlines (5px black)
    ctx.strokeStyle = "rgba(0, 0, 0, 0.9)";
    ctx.lineWidth = 5;

    // Lateral outline
    ctx.beginPath();
    ctx.moveTo(startX, bottomY);
    ctx.lineTo(endX, bottomY);
    ctx.stroke();

    // Axial outline
    ctx.beginPath();
    ctx.moveTo(endX, bottomY);
    ctx.lineTo(endX, topY);
    ctx.stroke();

    // End tick caps outlines
    const tickLen = 4;
    ctx.beginPath();
    ctx.moveTo(startX, bottomY - tickLen);
    ctx.lineTo(startX, bottomY + tickLen);
    ctx.moveTo(endX - tickLen, topY);
    ctx.lineTo(endX + tickLen, topY);
    ctx.stroke();

    // 2. Foreground scale bar lines (3px white / color)
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;

    // Lateral bar
    ctx.beginPath();
    ctx.moveTo(startX, bottomY);
    ctx.lineTo(endX, bottomY);
    ctx.stroke();

    // Axial bar
    ctx.beginPath();
    ctx.moveTo(endX, bottomY);
    ctx.lineTo(endX, topY);
    ctx.stroke();

    // End tick caps
    ctx.beginPath();
    ctx.moveTo(startX, bottomY - tickLen);
    ctx.lineTo(startX, bottomY + tickLen);
    ctx.moveTo(endX - tickLen, topY);
    ctx.lineTo(endX + tickLen, topY);
    ctx.stroke();

    // 3. Labels with high-contrast black outline
    ctx.font = '600 11px "Outfit", "Segoe UI", system-ui, -apple-system, sans-serif';
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(0, 0, 0, 0.95)";
    ctx.fillStyle = color;

    // Lateral label
    const latLabel = "1.0 mm";
    ctx.strokeText(latLabel, startX, bottomY - 8);
    ctx.fillText(latLabel, startX, bottomY - 8);

    // Axial label
    const axLabel = "200 μm";
    ctx.strokeText(axLabel, endX - 52, topY - 6);
    ctx.fillText(axLabel, endX - 52, topY - 6);

    ctx.restore();
  }

  // ── UI Construction ──────────────────────────────────────────────────────
  function buildPanel() {
    const container = document.getElementById("measurement-body");
    if (!container) return;
    container.innerHTML = "";

    const meas = latestMetrics || (State.getState().measurements && State.getState().measurements.data);
    const scene = State.getState().scene;

    // ── 1. Scale Bar & Tool Actions Card ───────────────────────────────────
    const toolCard = document.createElement("div");
    toolCard.className = "section-card";

    const toolHeader = document.createElement("div");
    toolHeader.className = "section-card-title";
    toolHeader.innerHTML = `
      <span>Measurement Tools</span>
      <button class="btn-tool-action" id="btn-refresh-meas" title="Re-derive measurements from current image">Refresh</button>
    `;
    toolCard.appendChild(toolHeader);

    const caliperBtn = document.createElement("button");
    caliperBtn.className = "btn-primary";
    caliperBtn.style.width = "100%";
    caliperBtn.innerHTML = '<svg class="icon" aria-hidden="true"><use href="assets/icons.svg#ruler"></use></svg> Activate caliper tool';
    caliperBtn.addEventListener("click", () => {
      Annotation.setTool("caliper");
    });
    toolCard.appendChild(caliperBtn);

    const scaleToggle = Controls.makeToggle({
      label: "Show Calibrated Scale Bars (1mm × 200μm)",
      value: State.getState().measurements.showScaleBars,
      onChange: (v) => {
        State.updateMeasurements({ showScaleBars: v });
        const cb = document.getElementById("toggle-show-scalebars");
        if (cb) cb.checked = v;
        Annotation.redraw();
      },
    });
    const inp = scaleToggle.querySelector("input");
    if (inp) inp.id = "toggle-meas-panel-scalebars";
    toolCard.appendChild(scaleToggle);

    container.appendChild(toolCard);

    // ── 2. Summary Morphometry Readout Card ────────────────────────────────
    const summaryCard = document.createElement("div");
    summaryCard.className = "section-card";

    const sumHeader = document.createElement("div");
    sumHeader.className = "section-card-title";
    sumHeader.textContent = "Quantitative Morphology";
    summaryCard.appendChild(sumHeader);

    const crtVal = meas ? `${meas.central_retinal_thickness_um.toFixed(1)} μm` : "—";
    const meanTrtVal = meas ? `${meas.total_retinal_thickness_mean_um.toFixed(1)} μm` : "—";
    const pitDepthVal = meas && meas.foveal_metrics ? `${meas.foveal_metrics.foveal_pit_depth_um.toFixed(1)} μm` : "—";
    const pitWidthVal = meas && meas.foveal_metrics ? `${meas.foveal_metrics.foveal_pit_width_mm.toFixed(2)} mm` : "—";

    const grid = document.createElement("div");
    grid.className = "metric-grid";
    grid.innerHTML = `
      <div class="metric-box">
        <span class="metric-label">Central Foveal (CFT)</span>
        <span class="metric-value">${crtVal}</span>
      </div>
      <div class="metric-box">
        <span class="metric-label">Mean Thickness (TRT)</span>
        <span class="metric-value">${meanTrtVal}</span>
      </div>
      <div class="metric-box">
        <span class="metric-label">Foveal Pit Depth</span>
        <span class="metric-value">${pitDepthVal}</span>
      </div>
      <div class="metric-box">
        <span class="metric-label">Foveal Pit Width</span>
        <span class="metric-value">${pitWidthVal}</span>
      </div>
    `;
    summaryCard.appendChild(grid);
    container.appendChild(summaryCard);

    // ── 3. Per-Layer Thickness Table ───────────────────────────────────────
    if (meas && meas.layer_thicknesses) {
      const layerCard = document.createElement("div");
      layerCard.className = "section-card";

      const layerHeader = document.createElement("div");
      layerHeader.className = "section-card-title";
      layerHeader.textContent = "Layer Thicknesses (μm)";
      layerCard.appendChild(layerHeader);

      const tableWrapper = document.createElement("div");
      tableWrapper.style.overflowX = "auto";

      const table = document.createElement("table");
      table.className = "meas-table";
      table.innerHTML = `
        <thead>
          <tr>
            <th>Layer</th>
            <th>Mean</th>
            <th>Min</th>
            <th>Max</th>
            <th>Fovea</th>
            <th>Parafov</th>
          </tr>
        </thead>
        <tbody></tbody>
      `;
      const tbody = table.querySelector("tbody");

      Object.entries(meas.layer_thicknesses).forEach(([lname, metric]) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td class="lc-${lname.toLowerCase()}">${lname}</td>
          <td>${metric.mean_thickness_um.toFixed(1)}</td>
          <td>${metric.min_thickness_um.toFixed(1)}</td>
          <td>${metric.max_thickness_um.toFixed(1)}</td>
          <td>${metric.foveal_thickness_um.toFixed(1)}</td>
          <td>${metric.parafoveal_thickness_um.toFixed(1)}</td>
        `;
        tbody.appendChild(tr);
      });

      tableWrapper.appendChild(table);
      layerCard.appendChild(tableWrapper);
      container.appendChild(layerCard);
    }

    // ── 4. Fluid & Lesion Morphometry (if pathology present) ───────────────
    if (meas && meas.fluid_regions && meas.fluid_regions.length > 0) {
      const fluidCard = document.createElement("div");
      fluidCard.className = "section-card";

      const fluidHeader = document.createElement("div");
      fluidHeader.className = "section-card-title";
      fluidHeader.textContent = `Fluid / Lesion Regions (${meas.fluid_regions.length})`;
      fluidCard.appendChild(fluidHeader);

      const fluidList = document.createElement("div");
      fluidList.className = "instance-list-items";

      meas.fluid_regions.forEach((reg) => {
        const regEl = document.createElement("div");
        regEl.className = "instance-card";
        regEl.style.padding = "6px 8px";
        regEl.innerHTML = `
          <div style="display:flex;justify-content:space-between;font-size:11px;font-weight:600;">
            <span class="pathology-title-color">${reg.pathology_type || "Fluid Pocket"} #${reg.id}</span>
            <span style="color:var(--accent);font-variant-numeric:tabular-nums;">${(reg.area_um2 / 1000).toFixed(1)}k μm²</span>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums;margin-top:2px;">
            <span>W: ${reg.width_um.toFixed(1)} μm | H: ${reg.height_um.toFixed(1)} μm</span>
            <span>X: ${reg.centroid_x_mm.toFixed(2)} mm</span>
          </div>
        `;
        fluidList.appendChild(regEl);
      });

      fluidCard.appendChild(fluidList);
      container.appendChild(fluidCard);
    }

    // Wire refresh button
    const refreshBtn = toolHeader.querySelector("#btn-refresh-meas");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => refreshMetrics());
    }
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    buildPanel();

    // Register overlay renderer with Annotation module so scale bars are ALWAYS drawn on every redraw!
    if (window.Annotation && typeof window.Annotation.addOverlayRenderer === "function") {
      Annotation.addOverlayRenderer((ctx, w, h) => {
        drawScaleBars(ctx, w, h);
      });
    }

    // Hook into Annotation redraw as well
    const originalRedraw = Annotation.redraw;
    Annotation.redraw = function () {
      originalRedraw();
      const canvas = document.getElementById("annotation-overlay-canvas");
      if (canvas) {
        const ctx = canvas.getContext("2d");
        drawScaleBars(ctx, canvas.width, canvas.height);
      }
    };

    // Toggle scale bars in preview toolbar
    const toggleScale = document.getElementById("toggle-show-scalebars");
    if (toggleScale) {
      toggleScale.addEventListener("change", (e) => {
        State.updateMeasurements({ showScaleBars: e.target.checked });
        const panelCb = document.getElementById("toggle-meas-panel-scalebars");
        if (panelCb) panelCb.checked = e.target.checked;
        Annotation.redraw();
      });
    }

    // Toggle calipers in preview toolbar
    const toggleMeas = document.getElementById("toggle-show-measurements");
    if (toggleMeas) {
      toggleMeas.addEventListener("change", () => {
        Annotation.redraw();
      });
    }

    // Toggle labels in preview toolbar
    const toggleAnn = document.getElementById("toggle-show-annotations");
    if (toggleAnn) {
      toggleAnn.addEventListener("change", () => {
        Annotation.redraw();
      });
    }

    // Re-query metrics when renderId changes
    let lastRenderId = null;
    State.subscribe((s) => {
      if (s.renderId && s.renderId !== lastRenderId) {
        lastRenderId = s.renderId;
        refreshMetrics();
      }
    });
  }

  return {
    init,
    buildPanel,
    refreshMetrics,
    drawScaleBars,
  };
})();
