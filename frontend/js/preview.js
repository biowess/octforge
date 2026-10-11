/**
 * OCTForge Preview Module.
 *
 * Responsibilities:
 *  - Debounced (400ms), abortable preview requests to /api/render/preview.
 *    A new sceneConfig change cancels the in-flight request.
 *  - Explicit "Render Final" path via Generator.triggerFinalRender().
 *  - Zoom / pan of the result using CSS transforms (no library).
 *  - Image display and status bar updates.
 *
 * NOTE: Only sceneConfig changes trigger renders.  Presentation-layer config
 */

const Preview = (function () {

  // ── Debounce / abort state ────────────────────────────────────────────
  let debounceTimer  = null;
  const DEBOUNCE_MS  = 400;
  let activeController = null;   // AbortController for in-flight fetch

  // ── Zoom / pan state ──────────────────────────────────────────────────
  let zoom   = 1.0;
  const MIN_ZOOM = 0.1;
  const MAX_ZOOM = 8.0;
  let panX = 0, panY = 0;        // Current pan offset in pixels (screen space)
  let isPanning = false;
  let panStartX = 0, panStartY = 0;
  let panOriginX = 0, panOriginY = 0;

  // DOM refs (assigned in init)
  let viewportEl   = null;
  let transformEl  = null;
  let imgEl        = null;
  let emptyEl      = null;
  let indicatorEl  = null;
  let zoomDisplayEl = null;

  // ── Render ────────────────────────────────────────────────────────────
  /**
   * Schedule a debounced preview render.  Cancels the previous timer and any
   * in-flight fetch.
   */
  function scheduleRender(delayMs = DEBOUNCE_MS) {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => { _executeRender(); }, delayMs);
  }

  /** Immediately cancel pending render and fire one now. */
  function triggerRender() {
    if (debounceTimer) clearTimeout(debounceTimer);
    _executeRender();
  }

  async function _executeRender() {
    // Cancel any in-flight request
    if (activeController) {
      activeController.abort();
      activeController = null;
    }

    activeController = new AbortController();
    const signal = activeController.signal;

    State.setState({ isRendering: true });
    _showIndicator(true);

    const scene = State.getState().scene;
    const startTime = performance.now();

    try {
      const res = await API.renderPreview(scene, signal);
      if (signal.aborted) return;   // superseded

      const latency = performance.now() - startTime;
      State.setState({
        previewImage:    res.image,
        renderId:        res.render_id,
        cacheKey:        res.cache_key,
        isCached:        res.cached,
        renderLatencyMs: latency,
        isRendering:     false,
      });
      activeController = null;
    } catch (err) {
      if (err.name === "AbortError" || (err instanceof DOMException && err.code === 20)) {
        // Request aborted — normal, not an error
        return;
      }
      console.error("Preview render failed:", err);
      State.setState({ isRendering: false });
      activeController = null;
    }
  }

  // ── Zoom / Pan ────────────────────────────────────────────────────────
  let lastDrawnZoom = 0;

  function _applyTransform() {
    if (!transformEl) return;
    transformEl.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
    if (zoomDisplayEl) zoomDisplayEl.textContent = Math.round(zoom * 100) + "%";
    // Selection handles are drawn at a constant on-screen size, so repaint on zoom changes.
    if (zoom !== lastDrawnZoom) {
      lastDrawnZoom = zoom;
      if (typeof Annotation !== "undefined" && Annotation.redraw) Annotation.redraw();
    }
  }

  function _centerImage() {
    if (!viewportEl || !imgEl) return;
    const vw = viewportEl.clientWidth;
    const vh = viewportEl.clientHeight;
    const iw = imgEl.naturalWidth  || imgEl.width  || 512;
    const ih = imgEl.naturalHeight || imgEl.height || 512;
    // Center with current zoom
    panX = (vw - iw * zoom) / 2;
    panY = (vh - ih * zoom) / 2;
    _applyTransform();
  }

  function _fitZoom() {
    if (!viewportEl || !imgEl) return;
    const vw = viewportEl.clientWidth  - 32;
    const vh = viewportEl.clientHeight - 32;
    if (vw <= 0 || vh <= 0) return;           // viewport hidden (e.g. composed view) — keep current view
    const iw = imgEl.naturalWidth  || 512;
    const ih = imgEl.naturalHeight || 512;
    zoom = Math.min(vw / iw, vh / ih, 1.0);
    _centerImage();
  }

  function _setZoom(newZoom, pivotX, pivotY) {
    if (!viewportEl) return;
    const clamped = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, newZoom));
    // Pivot: keep the image point under cursor fixed
    const scaleRatio = clamped / zoom;
    panX = pivotX - scaleRatio * (pivotX - panX);
    panY = pivotY - scaleRatio * (pivotY - panY);
    zoom = clamped;
    _applyTransform();
  }

  function _initPanZoom() {
    if (!viewportEl) return;

    // Wheel → zoom around cursor
    viewportEl.addEventListener("wheel", (e) => {
      e.preventDefault();
      const rect = viewportEl.getBoundingClientRect();
      const pivotX = e.clientX - rect.left;
      const pivotY = e.clientY - rect.top;
      const delta = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      _setZoom(zoom * delta, pivotX, pivotY);
    }, { passive: false });

    // Mouse down → start pan (blocked when a drawing tool is active)
    viewportEl.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      // Do not start panning when an annotation drawing tool is engaged;
      // the annotation canvas handles those events and stops propagation.
      // We still allow panning in select mode (annotation canvas lets the
      // event bubble for blank-space clicks).
      const activeTool = (window.State && State.getState().activeAnnotationTool) || "select";
      if (activeTool !== "select") return;
      isPanning = true;
      panStartX = e.clientX;
      panStartY = e.clientY;
      panOriginX = panX;
      panOriginY = panY;
      viewportEl.classList.add("panning");
    });

    window.addEventListener("mousemove", (e) => {
      if (!isPanning) return;
      panX = panOriginX + (e.clientX - panStartX);
      panY = panOriginY + (e.clientY - panStartY);
      _applyTransform();
    });

    window.addEventListener("mouseup", () => {
      if (!isPanning) return;
      isPanning = false;
      viewportEl.classList.remove("panning");
    });

    // Touch pan (single-finger)
    viewportEl.addEventListener("touchstart", (e) => {
      if (e.touches.length !== 1) return;
      isPanning = true;
      panStartX = e.touches[0].clientX;
      panStartY = e.touches[0].clientY;
      panOriginX = panX;
      panOriginY = panY;
    }, { passive: true });

    viewportEl.addEventListener("touchmove", (e) => {
      if (!isPanning || e.touches.length !== 1) return;
      e.preventDefault();
      panX = panOriginX + (e.touches[0].clientX - panStartX);
      panY = panOriginY + (e.touches[0].clientY - panStartY);
      _applyTransform();
    }, { passive: false });

    viewportEl.addEventListener("touchend", () => { isPanning = false; });

    // Keyboard shortcuts
    viewportEl.addEventListener("keydown", (e) => {
      const pivot = { x: viewportEl.clientWidth / 2, y: viewportEl.clientHeight / 2 };
      switch (e.key) {
        case "+": case "=": _setZoom(zoom * 1.2, pivot.x, pivot.y); break;
        case "-": case "_": _setZoom(zoom / 1.2, pivot.x, pivot.y); break;
        case "f": case "F": _fitZoom(); break;
        case "1":           zoom = 1; _centerImage(); break;
      }
    });
  }

  function _initZoomButtons() {
    const btnIn  = document.getElementById("btn-zoom-in");
    const btnOut = document.getElementById("btn-zoom-out");
    const btnFit = document.getElementById("btn-zoom-fit");
    const btn100 = document.getElementById("btn-zoom-100");
    if (!viewportEl) return;

    const pivot = () => ({ x: viewportEl.clientWidth / 2, y: viewportEl.clientHeight / 2 });
    if (btnIn)  btnIn.addEventListener("click",  () => { const p = pivot(); _setZoom(zoom * 1.25, p.x, p.y); });
    if (btnOut) btnOut.addEventListener("click", () => { const p = pivot(); _setZoom(zoom / 1.25, p.x, p.y); });
    if (btnFit) btnFit.addEventListener("click", () => _fitZoom());
    if (btn100) btn100.addEventListener("click", () => { zoom = 1; _centerImage(); });
  }

  // ── Indicator ─────────────────────────────────────────────────────────
  function _showIndicator(visible) {
    if (!indicatorEl) return;
    if (visible) indicatorEl.classList.add("visible");
    else         indicatorEl.classList.remove("visible");
  }

  // ── State subscription ────────────────────────────────────────────────
  let lastPreviewSrc = null;
  let fittedFor = "";            // "WxH" the view was last fitted for

  /** Fit on the first image and whenever its pixel size changes; otherwise keep pan/zoom. */
  function _onImageLoad() {
    const key = imgEl.naturalWidth + "x" + imgEl.naturalHeight;
    if (key !== fittedFor) {
      fittedFor = key;
      _fitZoom();
    } else {
      _applyTransform();
    }
  }

  function _handleStateChange(state) {
    // Image update
    if (state.previewImage && imgEl) {
      // Assign only when the image changed — re-assigning an identical data URL reloads it.
      if (state.previewImage !== lastPreviewSrc) {
        lastPreviewSrc = state.previewImage;
        imgEl.src = state.previewImage;
      }

      // Hide empty overlay
      if (emptyEl) emptyEl.style.display = "none";
      if (viewportEl) viewportEl.classList.remove("empty");
    }

    // Rendering indicator
    _showIndicator(state.isRendering);

    // Backend status in status bar
    const backendVal = document.getElementById("val-backend-status");
    if (backendVal) {
      if (state.serverHealth === "online") {
        backendVal.textContent = "connected";
        backendVal.className = "status-val";
      } else if (state.serverHealth === "offline") {
        backendVal.textContent = "not reachable";
        backendVal.className = "status-val";
      } else {
        backendVal.textContent = "connecting…";
        backendVal.className = "status-val";
      }
    }
    const dot  = document.getElementById("health-dot");
    const text = document.getElementById("health-text");
    if (dot && text) {
      dot.classList.toggle("online",  state.serverHealth === "online");
      dot.classList.toggle("offline", state.serverHealth === "offline");
      text.textContent = state.serverHealth;
    }

    // Status bar
    const latEl  = document.getElementById("val-latency");
    const cacheEl = document.getElementById("val-cache");
    const ridEl   = document.getElementById("val-render-id");
    const sizeEl  = document.getElementById("val-image-size");

    if (latEl)   latEl.textContent  = state.renderLatencyMs > 0 ? state.renderLatencyMs.toFixed(0) + " ms" : "—";
    if (cacheEl) {
      cacheEl.textContent = state.isCached ? "Hit" : "Miss";
      cacheEl.className   = "status-val" + (state.isCached ? " ok" : "");
    }
    if (ridEl && state.renderId)    ridEl.textContent  = state.renderId.substring(0, 12) + "…";
    if (sizeEl && imgEl && imgEl.naturalWidth) {
      sizeEl.textContent = `${imgEl.naturalWidth} × ${imgEl.naturalHeight}`;
    }

    // Canvas dims in toolbar
    const dimsEl = document.getElementById("canvas-dims-display");
    if (dimsEl && imgEl && imgEl.naturalWidth) {
      dimsEl.textContent = `${imgEl.naturalWidth}×${imgEl.naturalHeight} px`;
    }
  }

  // ── Init ──────────────────────────────────────────────────────────────
  function init() {
    viewportEl   = document.getElementById("preview-viewport");
    transformEl  = document.getElementById("preview-transform");
    imgEl        = document.getElementById("bscan-img");
    emptyEl      = document.getElementById("preview-empty");
    indicatorEl  = document.getElementById("render-indicator");
    zoomDisplayEl = document.getElementById("zoom-level-display");

    if (!viewportEl) {
      console.warn("Preview: #preview-viewport not found");
      return;
    }

    _initPanZoom();
    _initZoomButtons();
    if (imgEl) imgEl.addEventListener("load", _onImageLoad);

    // Subscribe to state for UI updates
    State.subscribe(_handleStateChange);

    // Re-center on viewport resize
    const resizeObserver = new ResizeObserver(() => {
      if (imgEl && imgEl.naturalWidth && viewportEl.clientWidth > 0) _fitZoom();
    });
    resizeObserver.observe(viewportEl);
  }

  return {
    init,
    scheduleRender,
    triggerRender,
  };
})();
