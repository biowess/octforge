/**
 * OCTForge Export Module.
 *
 * Responsibilities:
 *  - Multi-format publication export (PNG, JPEG, TIFF, PDF, SVG).
 *  - Curated export presets (Publication, Lecture, PowerPoint, A4, Web, High-Res, Low-Resource).
 *  - DPI, color mode, compression, quality, inclusions, and watermark controls.
 *  - Single B-scan or composed multi-panel figure export via POST /api/export.
 *  - Automatic browser download trigger.
 *
 * CRITICAL ARCHITECTURAL LAW:
 * Export consumes already-rendered images and structured annotations.
 * It NEVER calls /api/render/preview or /api/render/final.
 */

const Export = (function () {

  const PRESET_CONFIGS = {
    publication: {
      preset: "publication",
      format: "tiff",
      dpi: 600,
      color_mode: "RGB",
      compression: "lzw",
      quality: 100,
      include_annotations: true,
      include_measurements: true,
      include_scale_bars: true,
      watermark: false,
    },
    lecture: {
      preset: "lecture",
      format: "png",
      dpi: 150,
      width: 1920,
      height: 1080,
      color_mode: "RGB",
      quality: 95,
      include_annotations: true,
      include_measurements: true,
      include_scale_bars: true,
      watermark: true,
      watermark_text: "SYNTHETIC — FOR EDUCATION ONLY",
    },
    powerpoint: {
      preset: "powerpoint",
      format: "png",
      dpi: 150,
      width: 1920,
      height: 1080,
      color_mode: "RGB",
      quality: 95,
      include_annotations: true,
      include_measurements: true,
      include_scale_bars: true,
      watermark: false,
    },
    a4: {
      preset: "a4",
      format: "pdf",
      dpi: 300,
      width: 2480,
      height: 3508,
      color_mode: "RGB",
      quality: 100,
      include_annotations: true,
      include_measurements: true,
      include_scale_bars: true,
      watermark: false,
    },
    web: {
      preset: "web",
      format: "jpeg",
      dpi: 72,
      width: 800,
      height: 800,
      color_mode: "RGB",
      quality: 85,
      include_annotations: true,
      include_measurements: true,
      include_scale_bars: true,
      watermark: false,
    },
    high_resolution: {
      preset: "high_resolution",
      format: "png",
      dpi: 600,
      color_mode: "RGB",
      quality: 100,
      include_annotations: true,
      include_measurements: true,
      include_scale_bars: true,
      watermark: false,
    },
    low_resource: {
      preset: "low_resource",
      format: "jpeg",
      dpi: 72,
      width: 400,
      height: 400,
      color_mode: "L",
      quality: 70,
      include_annotations: true,
      include_measurements: true,
      include_scale_bars: true,
      watermark: false,
    },
  };

  let lastExportResult = null;
  let exportScope = "single"; // "single" or "composition"

  // ── Execute Export Action ────────────────────────────────────────────────
  async function downloadExport() {
    const s = State.getState();
    const expConfig = s.export;

    let payload = {
      config: expConfig,
    };

    if (exportScope === "composition" || s.viewMode === "composition") {
      payload.composition = s.composition;
    } else {
      payload.render_id = s.renderId;
      payload.visualization = s.visualization;
      payload.annotations = s.annotations;
      payload.measurements = s.measurements.data;
    }

    const btn = document.getElementById("btn-do-download");
    try {
      if (btn) {
        btn.disabled = true;
        btn.setAttribute("aria-busy", "true");
        const lbl = btn.querySelector(".btn-label");
        if (lbl) { lbl._orig = lbl._orig || lbl.textContent; lbl.textContent = "Exporting…"; }
      }

      const res = await API.exportArtifact(payload);
      if (res && res.data) {
        lastExportResult = res;

        // Trigger automatic browser file download
        const a = document.createElement("a");
        a.href = res.data;
        a.download = res.filename || `octforge_export.${res.format || 'png'}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);

        console.log(`[OCTForge] Exported file: ${res.filename} (${(res.file_size_bytes / 1024).toFixed(1)} KB)`);
        if (typeof Dialogs !== "undefined") {
          Dialogs.notify(`Exported: ${res.filename} (${(res.file_size_bytes / 1024).toFixed(1)} KB)`, "ok");
        }
        buildPanel();
      }
    } catch (err) {
      console.error("Export failed:", err);
      if (typeof Dialogs !== "undefined") {
        Dialogs.notify(`Export failed: ${err.message}`, "error");
      }
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.removeAttribute("aria-busy");
        const lbl = btn.querySelector(".btn-label");
        if (lbl && lbl._orig) lbl.textContent = lbl._orig;
      }
    }
  }

  // ── Apply Export Preset ──────────────────────────────────────────────────
  function applyPreset(presetKey) {
    if (PRESET_CONFIGS[presetKey]) {
      const patch = State.deepClone(PRESET_CONFIGS[presetKey]);
      State.updateExport(patch);
      buildPanel();
    }
  }

  // ── UI Construction ──────────────────────────────────────────────────────
  function buildPanel() {
    const container = document.getElementById("export-body");
    if (!container) return;
    container.innerHTML = "";

    const exp = State.getState().export;

    // ── 1. Quick Presets Card ──────────────────────────────────────────────
    const presetsCard = document.createElement("div");
    presetsCard.className = "section-card";

    const presetsHeader = document.createElement("div");
    presetsHeader.className = "section-card-title";
    presetsHeader.textContent = "Export Presets";
    presetsCard.appendChild(presetsHeader);

    const presetsGrid = document.createElement("div");
    presetsGrid.className = "export-preset-grid";

    const presetButtons = [
      { key: "publication",     title: "Publication", sub: "600 DPI TIFF (LZW)" },
      { key: "lecture",         title: "Lecture Slide", sub: "1080p PNG + Notice" },
      { key: "powerpoint",      title: "PowerPoint", sub: "1080p Clean PNG" },
      { key: "a4",              title: "A4 Print", sub: "300 DPI PDF" },
      { key: "web",             title: "Web Optimized", sub: "72 DPI JPEG" },
      { key: "high_resolution", title: "High-Res Master", sub: "600 DPI PNG" },
      { key: "low_resource",    title: "Low Bandwidth", sub: "72 DPI Grayscale" },
    ];

    presetButtons.forEach((pb) => {
      const btn = document.createElement("div");
      btn.className = `export-preset-btn${exp.preset === pb.key ? " active" : ""}`;
      btn.innerHTML = `
        <span class="export-preset-title">${pb.title}</span>
        <span class="export-preset-sub">${pb.sub}</span>
      `;
      btn.addEventListener("click", () => applyPreset(pb.key));
      presetsGrid.appendChild(btn);
    });

    presetsCard.appendChild(presetsGrid);
    container.appendChild(presetsCard);

    // ── 2. Format & Technical Settings Card ────────────────────────────────
    const settingsCard = document.createElement("div");
    settingsCard.className = "section-card";

    const setHeader = document.createElement("div");
    setHeader.className = "section-card-title";
    setHeader.textContent = "Format & Resolution";
    settingsCard.appendChild(setHeader);

    // Scope selection (Single scan vs Composed figure)
    const scopeSelect = Controls.makeSelect({
      label: "Export Scope",
      value: exportScope,
      options: [
        { value: "single",      label: "Single B-Scan (Active Render)" },
        { value: "composition", label: "Multi-Panel Composed Figure" },
      ],
      onChange: (v) => {
        exportScope = v;
      },
    });
    settingsCard.appendChild(scopeSelect);

    // Format select
    const formatSelect = Controls.makeSelect({
      label: "Output Format",
      value: exp.format,
      options: [
        { value: "png",  label: "PNG (Lossless Raster)" },
        { value: "tiff", label: "TIFF (Scientific Archive)" },
        { value: "jpeg", label: "JPEG (Standard Lossy)" },
        { value: "pdf",  label: "PDF (Print Document)" },
        { value: "svg",  label: "SVG (Vector Overlay)" },
      ],
      onChange: (format) => {
        State.updateExport({ format, preset: "custom" });
        buildPanel();
      },
    });
    settingsCard.appendChild(formatSelect);

    // DPI slider
    const dpiSlider = Controls.makeSlider({
      label: "Resolution (DPI)", min: 72, max: 1200, step: 24, value: exp.dpi,
      decimals: 0,
      onChange: (dpi) => {
        State.updateExport({ dpi: Math.round(dpi), preset: "custom" });
      },
    });
    settingsCard.appendChild(dpiSlider);

    // Color Mode select
    const colorModeSelect = Controls.makeSelect({
      label: "Color Mode",
      value: exp.color_mode,
      options: [
        { value: "RGB",  label: "RGB (24-bit Full Color)" },
        { value: "L",    label: "Grayscale (8-bit Luminance)" },
        { value: "RGBA", label: "RGBA (32-bit Transparent)" },
      ],
      onChange: (color_mode) => {
        State.updateExport({ color_mode, preset: "custom" });
      },
    });
    settingsCard.appendChild(colorModeSelect);

    container.appendChild(settingsCard);

    // ── 3. Annotations & Disclaimers Inclusions Card ───────────────────────
    const inclCard = document.createElement("div");
    inclCard.className = "section-card";

    const inclHeader = document.createElement("div");
    inclHeader.className = "section-card-title";
    inclHeader.textContent = "Overlays & Disclaimers";
    inclCard.appendChild(inclHeader);

    const annToggle = Controls.makeToggle({
      label: "Include Annotations & Layer Labels",
      value: exp.include_annotations !== false,
      onChange: (v) => State.updateExport({ include_annotations: v }),
    });
    inclCard.appendChild(annToggle);

    const caliperToggle = Controls.makeToggle({
      label: "Include Two-Point Caliper Measurements",
      value: exp.include_measurements !== false,
      onChange: (v) => State.updateExport({ include_measurements: v }),
    });
    inclCard.appendChild(caliperToggle);

    const scaleToggle = Controls.makeToggle({
      label: "Include Calibrated Scale Bars",
      value: exp.include_scale_bars !== false,
      onChange: (v) => State.updateExport({ include_scale_bars: v }),
    });
    inclCard.appendChild(scaleToggle);

    const watermarkToggle = Controls.makeToggle({
      label: "Embed Educational Notice Watermark",
      value: exp.watermark,
      onChange: (v) => State.updateExport({ watermark: v }),
    });
    inclCard.appendChild(watermarkToggle);

    container.appendChild(inclCard);

    // ── 4. Download Action Card ────────────────────────────────────────────
    const actionCard = document.createElement("div");
    actionCard.className = "section-card";

    const dlBtn = document.createElement("button");
    dlBtn.id = "btn-do-download";
    dlBtn.className = "btn-primary";
    dlBtn.style.padding = "8px 14px";
    dlBtn.style.fontSize = "13px";
    dlBtn.style.fontWeight = "700";
    dlBtn.style.justifyContent = "center";
    dlBtn.innerHTML = '<svg class="icon" aria-hidden="true"><use href="assets/icons.svg#download"></use></svg><span class="btn-label">Export figure</span>';
    dlBtn.addEventListener("click", () => downloadExport());
    actionCard.appendChild(dlBtn);

    if (lastExportResult) {
      const summaryBox = document.createElement("div");
      summaryBox.style.marginTop = "6px";
      summaryBox.style.fontSize = "11px";
      summaryBox.style.color = "var(--ok)";
      summaryBox.innerHTML = `
        Last export: ${lastExportResult.filename} (${lastExportResult.width}×${lastExportResult.height}, ${(lastExportResult.file_size_bytes / 1024).toFixed(1)} KB, ${lastExportResult.dpi} DPI)
      `;
      actionCard.appendChild(summaryBox);
    }

    container.appendChild(actionCard);
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    buildPanel();
    // #btn-export was removed from the topbar in Phase 2 (Export is now a workspace tab).
    // No wiring needed here; mainSetActiveTab("export") still works for deep links.
  }

  return {
    init,
    buildPanel,
    downloadExport,
    applyPreset,
  };
})();
