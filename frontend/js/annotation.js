/**
 * OCTForge Annotation Module.
 *
 * Responsibilities:
 *  - Interactive canvas overlay positioned directly over #bscan-img.
 *  - Coordinate mapping (screen ↔ canvas ↔ image pixel) invariant to zoom/pan.
 *  - Shape primitives: text, arrow, leader_line, dot, numbered_marker,
 *    bracket, reference_line, caliper.
 *  - Direct manipulation: create, select, move, resize, rotate, edit, delete.
 *  - "Auto-Label Layers" integration via POST /api/annotate.
 *  - Item inspector and active-item list.
 *
 * Text boxes
 *  - The Text tool creates a box by click-dragging on the canvas (a single
 *    click creates a default-width box). Typing starts immediately in an
 *    in-place <textarea>; no browser dialogs are involved.
 *  - A selected box shows 8 resize handles, a rotation handle and a small
 *    delete control. Double-click edits the text in place.
 *  - Boxed text keeps `metadata.lines` (the wrapped lines measured here) so
 *    the backend export reproduces exactly the same line breaks.
 *
 * ARCHITECTURAL LAW: annotations are structured vector data in
 * state.annotations. Editing them NEVER touches the B-scan pixels or calls
 * /api/render/*.
 */

const Annotation = (function () {
  "use strict";

  const CANVAS_FONT_FAMILY = '"Outfit", "Segoe UI", system-ui, -apple-system, sans-serif';
  const LINE_HEIGHT_MULT = 1.25;
  const MIN_BOX_W = 40;
  const DEFAULT_BOX_W = 160;
  const ANNOTATE_WORKSPACE = "annotation";

  const TOOL_LABELS = {
    select: "Select",
    text: "Text",
    arrow: "Arrow",
    leader_line: "Leader line",
    dot: "Dot",
    numbered_marker: "Badge",
    bracket: "Bracket",
    reference_line: "Reference line",
    caliper: "Caliper",
  };

  const TOOL_KEYS = {
    v: "select", t: "text", a: "arrow", l: "leader_line", d: "dot",
    n: "numbered_marker", b: "bracket", r: "reference_line", m: "caliper",
  };

  // Chrome colours — resolved from the design tokens at init().
  let ACCENT = "#1B5A97";
  let DANGER = "#B3362E";

  let canvasEl = null;
  let ctx = null;

  // Interaction state
  let currentDrawingItem = null;   // item being drawn, not yet committed
  let isDragging = false;
  let dragMode = null;             // "item" | "handle_p1" | "handle_p2" | "handle_rot" | "handle_<dir>" | "handle_del" | "text_create"
  let dragOffset = { x: 0, y: 0 };
  let dragOrigin = { x: 0, y: 0 };
  let activeTool = "select";
  let selectedId = null;

  let activeColor = "#FFEB3B";
  let activeStrokeWidth = 2;
  let activeFontSize = 12;
  let markerCounter = 1;

  let activeEditor = null;         // the in-place <textarea>, if any
  let rotFlipAtStart = false;      // which side the rotation handle sat on when a rotate drag began

  // ── Small helpers ────────────────────────────────────────────────────────
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function getItems() {
    return State.getState().annotations.items || [];
  }

  function findItem(id) {
    return getItems().find((i) => i.id === id) || null;
  }

  function newId() {
    return `annot_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
  }

  function isBoxedText(item) {
    return item.type === "text" && item.width !== undefined && item.width !== null && item.width > 0;
  }

  function refreshCaliperMetrics() {
    if (window.Measurement && typeof window.Measurement.refreshMetrics === "function") {
      window.Measurement.refreshMetrics();
    }
  }

  // ── Coordinate mapping ───────────────────────────────────────────────────
  /** Client (screen) coordinates → image pixel coordinates, clamped to the image. */
  function screenToImageCoords(e) {
    if (!canvasEl) return { x: 0, y: 0 };
    const rect = canvasEl.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return { x: 0, y: 0 };

    const clientX = e.clientX !== undefined ? e.clientX : (e.touches && e.touches[0] ? e.touches[0].clientX : 0);
    const clientY = e.clientY !== undefined ? e.clientY : (e.touches && e.touches[0] ? e.touches[0].clientY : 0);

    const x = (clientX - rect.left) * (canvasEl.width / rect.width);
    const y = (clientY - rect.top) * (canvasEl.height / rect.height);

    return {
      x: Math.max(0, Math.min(canvasEl.width, Math.round(x * 10) / 10)),
      y: Math.max(0, Math.min(canvasEl.height, Math.round(y * 10) / 10)),
    };
  }

  /** On-screen pixels per image pixel (CSS zoom of the overlay). */
  function getZoom() {
    if (!canvasEl) return 1;
    const rect = canvasEl.getBoundingClientRect();
    return rect.width > 0 && canvasEl.width > 0 ? rect.width / canvasEl.width : 1;
  }

  function rotatePoint(px, py, cx, cy, angleDeg) {
    const rad = angleDeg * Math.PI / 180;
    const dx = px - cx;
    const dy = py - cy;
    return {
      x: cx + dx * Math.cos(rad) - dy * Math.sin(rad),
      y: cy + dx * Math.sin(rad) + dy * Math.cos(rad),
    };
  }

  function distSq(x1, y1, x2, y2) {
    return (x1 - x2) ** 2 + (y1 - y2) ** 2;
  }

  function distToSegment(px, py, x1, y1, x2, y2) {
    const l2 = distSq(x1, y1, x2, y2);
    if (l2 === 0) return Math.sqrt(distSq(px, py, x1, y1));
    let t = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / l2;
    t = Math.max(0, Math.min(1, t));
    return Math.sqrt(distSq(px, py, x1 + t * (x2 - x1), y1 + t * (y2 - y1)));
  }

  // ── Text layout ──────────────────────────────────────────────────────────
  function setCanvasFont(fontSize) {
    if (ctx) ctx.font = `600 ${fontSize}px ${CANVAS_FONT_FAMILY}`;
  }

  function measureWidth(str, fontSize) {
    return ctx ? ctx.measureText(str).width : str.length * fontSize * 0.6;
  }

  /** Break one over-wide word into chunks; pushes all but the last chunk. */
  function breakWord(word, maxW, fontSize, out) {
    let chunk = "";
    for (const ch of word) {
      if (chunk && measureWidth(chunk + ch, fontSize) > maxW) {
        out.push(chunk);
        chunk = ch;
      } else {
        chunk += ch;
      }
    }
    return chunk;
  }

  function wrapText(text, maxW, fontSize) {
    setCanvasFont(fontSize);
    const out = [];
    for (const paragraph of String(text).split("\n")) {
      let line = "";
      for (const word of paragraph.split(" ")) {
        const test = line ? line + " " + word : word;
        if (measureWidth(test, fontSize) <= maxW) {
          line = test;
          continue;
        }
        if (line) {
          out.push(line);
          line = "";
        }
        line = measureWidth(word, fontSize) <= maxW ? word : breakWord(word, maxW, fontSize, out);
      }
      out.push(line);
    }
    return out;
  }

  /**
   * Layout of a text item. Boxed items wrap to their width; legacy items
   * (e.g. auto-labels, no width) are a single baseline-anchored line.
   * For boxed items this also keeps `metadata.lines` current for the backend.
   */
  function layoutTextItem(item) {
    const fontSize = item.font_size || 12;
    const text = item.text || item.label || "";

    if (!isBoxedText(item)) {
      setCanvasFont(fontSize);
      const w = measureWidth(text, fontSize);
      return { isLegacy: true, lines: [text], textHeight: fontSize, effWidth: w, effHeight: fontSize };
    }

    const lines = wrapText(text, item.width, fontSize);
    const prev = item.metadata && item.metadata.lines;
    if (!prev || prev.length !== lines.length || prev.some((l, i) => l !== lines[i])) {
      item.metadata = { ...(item.metadata || {}), lines };
    }
    const textHeight = lines.length * fontSize * LINE_HEIGHT_MULT;
    return {
      isLegacy: false,
      lines,
      textHeight,
      effWidth: item.width,
      effHeight: Math.max(item.height || 0, textHeight),
    };
  }

  /** Grow a boxed item so its text always fits (never shrinks the box). */
  function fitTextBox(item) {
    if (!isBoxedText(item)) return;
    const lay = layoutTextItem(item);
    if (!item.height || item.height < lay.textHeight) item.height = lay.textHeight;
  }

  // ── Selection chrome for text boxes ──────────────────────────────────────
  const HANDLE_DIRS = ["nw", "n", "ne", "w", "e", "sw", "s", "se"];

  /**
   * Geometry (in the box's own, unrotated frame) of the resize handles, the
   * rotation handle and the delete control. The rotation handle and delete
   * control flip to whichever side keeps them inside the image, so they stay
   * reachable for boxes placed against an image edge.
   */
  function textChrome(item, lay) {
    const z = getZoom();
    const w = lay.effWidth;
    const h = lay.effHeight;
    const x = item.x;
    const y = item.y;
    const cx = x + w / 2;
    const cy = y + h / 2;
    const rot = item.rotation || 0;
    const margin = 8 / z;
    const W = canvasEl ? canvasEl.width : 512;
    const H = canvasEl ? canvasEl.height : 512;

    const inside = (p) => {
      const q = rotatePoint(p.x, p.y, cx, cy, rot);
      return q.x >= margin && q.x <= W - margin && q.y >= margin && q.y <= H - margin;
    };

    const handleAt = {
      nw: [x, y], n: [x + w / 2, y], ne: [x + w, y],
      w: [x, y + h / 2], e: [x + w, y + h / 2],
      sw: [x, y + h], s: [x + w / 2, y + h], se: [x + w, y + h],
    };
    const handles = HANDLE_DIRS.map((n) => ({ n, x: handleAt[n][0], y: handleAt[n][1] }));

    const above = { x: x + w / 2, y: y - 24 / z, stemY: y, flipped: false };
    const below = { x: x + w / 2, y: y + h + 24 / z, stemY: y + h, flipped: true };
    let rotHandle;
    if (isDragging && dragMode === "handle_rot" && item.id === selectedId) {
      rotHandle = rotFlipAtStart ? below : above;       // no side-flipping mid-drag
    } else {
      rotHandle = inside(above) || !inside(below) ? above : below;
    }

    const d = 15 / z;
    const delCandidates = [
      { x: x + w + d, y: y - d },
      { x: x + w + d, y: y + h + d },
      { x: x - d, y: y - d },
      { x: x - d, y: y + h + d },
      { x: x + w - 10 / z, y: y + 10 / z },
    ];
    const del = delCandidates.find(inside) || delCandidates[0];

    return { z, x, y, w, h, cx, cy, rot, handles, rotHandle, del };
  }

  /** Legacy (width-less) text: simple bounding box + delete control. */
  function legacyChrome(item, lay) {
    const z = getZoom();
    const pad = 3;
    const x = item.x - pad;
    const y = item.y - lay.effHeight - pad;
    const w = lay.effWidth + pad * 2;
    const h = lay.effHeight + pad * 2 + 2;
    const d = 12 / z;
    const W = canvasEl ? canvasEl.width : 512;
    let del = { x: x + w + d, y: y - d };
    if (del.x > W - 8 / z || del.y < 8 / z) del = { x: x + w - 8 / z, y: y + h + d };
    return { z, x, y, w, h, del };
  }

  // ── Hit testing ──────────────────────────────────────────────────────────
  function hitTestSelectedChrome(pt) {
    const item = findItem(selectedId);
    if (!item || item.type !== "text") return null;

    const lay = layoutTextItem(item);
    if (lay.isLegacy) {
      const ch = legacyChrome(item, lay);
      const hr = 8 / ch.z;
      if (Math.abs(pt.x - ch.del.x) <= hr && Math.abs(pt.y - ch.del.y) <= hr) {
        return { item, mode: "handle_del" };
      }
      return null;
    }

    const ch = textChrome(item, lay);
    const local = rotatePoint(pt.x, pt.y, ch.cx, ch.cy, -ch.rot);   // pointer in the box's own frame
    const hr = 8 / ch.z;

    if (distSq(local.x, local.y, ch.rotHandle.x, ch.rotHandle.y) <= hr * hr) {
      return { item, mode: "handle_rot" };
    }
    if (Math.abs(local.x - ch.del.x) <= hr && Math.abs(local.y - ch.del.y) <= hr) {
      return { item, mode: "handle_del" };
    }
    for (const hnd of ch.handles) {
      if (Math.abs(local.x - hnd.x) <= hr && Math.abs(local.y - hnd.y) <= hr) {
        return { item, mode: "handle_" + hnd.n };
      }
    }
    return null;
  }

  function hitTest(pt) {
    const chromeHit = hitTestSelectedChrome(pt);
    if (chromeHit) return chromeHit;

    const items = getItems();
    const threshold = 8.0;

    // Selected line-type items expose their end-point handles first.
    const sel = findItem(selectedId);
    if (sel && sel.x2 !== undefined && sel.y2 !== undefined && sel.type !== "text") {
      if (Math.sqrt(distSq(pt.x, pt.y, sel.x, sel.y)) <= 7) return { item: sel, mode: "handle_p1" };
      if (Math.sqrt(distSq(pt.x, pt.y, sel.x2, sel.y2)) <= 7) return { item: sel, mode: "handle_p2" };
    }

    // Top-down (newest first)
    for (let i = items.length - 1; i >= 0; i--) {
      const item = items[i];

      if (item.type === "dot" || item.type === "numbered_marker") {
        const r = item.radius || (item.type === "numbered_marker" ? 9 : 4);
        if (Math.sqrt(distSq(pt.x, pt.y, item.x, item.y)) <= r + 4) return { item, mode: "item" };
      } else if (item.type === "text") {
        const lay = layoutTextItem(item);
        if (lay.isLegacy) {
          if (pt.x >= item.x - 4 && pt.x <= item.x + lay.effWidth + 4 &&
              pt.y >= item.y - lay.effHeight - 2 && pt.y <= item.y + 4) {
            return { item, mode: "item" };
          }
        } else {
          const cx = item.x + lay.effWidth / 2;
          const cy = item.y + lay.effHeight / 2;
          const local = rotatePoint(pt.x, pt.y, cx, cy, -(item.rotation || 0));
          if (local.x >= item.x && local.x <= item.x + lay.effWidth &&
              local.y >= item.y && local.y <= item.y + lay.effHeight) {
            return { item, mode: "item" };
          }
        }
      } else if (item.type in { arrow: 1, leader_line: 1, reference_line: 1, bracket: 1, caliper: 1 }) {
        if (item.x2 !== undefined && item.y2 !== undefined) {
          if (Math.sqrt(distSq(pt.x, pt.y, item.x, item.y)) <= 6) return { item, mode: "handle_p1" };
          if (Math.sqrt(distSq(pt.x, pt.y, item.x2, item.y2)) <= 6) return { item, mode: "handle_p2" };
          if (distToSegment(pt.x, pt.y, item.x, item.y, item.x2, item.y2) <= threshold) return { item, mode: "item" };
        }
      }
    }
    return null;
  }

  /** Cursor that matches the hit target (resize cursors follow box rotation). */
  function cursorForHit(hit) {
    if (!hit) return null;
    if (hit.mode === "item") return "move";
    if (hit.mode === "handle_del") return "pointer";
    if (hit.mode === "handle_rot") return "grab";
    if (hit.mode === "handle_p1" || hit.mode === "handle_p2") return "pointer";
    const dir = hit.mode.replace("handle_", "");
    const base = { n: 0, ne: 45, e: 90, se: 135, s: 180, sw: 225, w: 270, nw: 315 }[dir];
    if (base === undefined) return "pointer";
    const rot = hit.item.rotation || 0;
    const step = Math.round((((base + rot) % 180) + 180) % 180 / 45) % 4;
    return ["ns-resize", "nesw-resize", "ew-resize", "nwse-resize"][step];
  }

  // ── Overlay rendering ────────────────────────────────────────────────────
  const overlayRenderers = [];
  function addOverlayRenderer(fn) {
    if (typeof fn === "function" && !overlayRenderers.includes(fn)) overlayRenderers.push(fn);
  }

  function redraw() {
    if (!canvasEl || !ctx) return;
    ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);

    const toggleAnnotations = document.getElementById("toggle-show-annotations");
    const showAnnotations = toggleAnnotations ? toggleAnnotations.checked : true;
    const toggleMeasurements = document.getElementById("toggle-show-measurements");
    const showMeasurements = toggleMeasurements ? toggleMeasurements.checked : true;

    // 1. Committed items (respecting the visibility toggles)
    getItems().forEach((item) => {
      const isCaliper = item.type === "caliper";
      if (isCaliper && !showMeasurements) return;
      if (!isCaliper && !showAnnotations) return;
      _drawItem(ctx, item, item.id === selectedId);
    });

    // 2. In-progress item
    if (currentDrawingItem) {
      if (currentDrawingItem.type === "text") {
        _drawTextRubberBand(ctx, currentDrawingItem);
      } else {
        const isCaliper = currentDrawingItem.type === "caliper";
        if (!((isCaliper && !showMeasurements) || (!isCaliper && !showAnnotations))) {
          _drawItem(ctx, currentDrawingItem, true);
        }
      }
    }

    // 3. External overlays (e.g. calibrated scale bars)
    for (let i = 0; i < overlayRenderers.length; i++) {
      try {
        overlayRenderers[i](ctx, canvasEl.width, canvasEl.height);
      } catch (err) {
        console.error("Overlay renderer error:", err);
      }
    }
  }

  function _drawTextRubberBand(c, item) {
    const z = getZoom();
    const x = Math.min(item.x, item.x + item.width);
    const y = Math.min(item.y, item.y + item.height);
    const w = Math.abs(item.width);
    const h = Math.abs(item.height);
    c.save();
    c.fillStyle = "rgba(27, 90, 151, 0.12)";
    c.fillRect(x, y, w, h);
    c.strokeStyle = ACCENT;
    c.lineWidth = 1 / z;
    c.setLineDash([4 / z, 3 / z]);
    c.strokeRect(x, y, w, h);
    c.restore();
  }

  function _drawHandleSquare(c, x, y, half, z) {
    c.fillStyle = "#FFFFFF";
    c.strokeStyle = ACCENT;
    c.lineWidth = 1 / z;
    c.fillRect(x - half, y - half, half * 2, half * 2);
    c.strokeRect(x - half, y - half, half * 2, half * 2);
  }

  function _drawDeleteChip(c, x, y, z) {
    const half = 7 / z;
    c.fillStyle = "#FFFFFF";
    c.strokeStyle = DANGER;
    c.lineWidth = 1 / z;
    c.fillRect(x - half, y - half, half * 2, half * 2);
    c.strokeRect(x - half, y - half, half * 2, half * 2);
    const a = 3 / z;
    c.lineWidth = 1.4 / z;
    c.beginPath();
    c.moveTo(x - a, y - a); c.lineTo(x + a, y + a);
    c.moveTo(x + a, y - a); c.lineTo(x - a, y + a);
    c.stroke();
  }

  function _drawTextItem(c, item, isSelected) {
    const fontSize = item.font_size || 12;
    const z = getZoom();
    const lay = layoutTextItem(item);
    const isEditing = !!(activeEditor && activeEditor.dataset.itemId === item.id);

    c.font = `600 ${fontSize}px ${CANVAS_FONT_FAMILY}`;
    c.save();

    if (lay.isLegacy) {
      if (!isEditing) {
        const txt = item.text || item.label || "";
        c.textBaseline = "alphabetic";
        c.strokeStyle = "#101214";
        c.lineWidth = 2.5;
        c.setLineDash([]);
        c.strokeText(txt, item.x, item.y);
        c.fillText(txt, item.x, item.y);
      }
      if (isSelected && !isEditing) {
        const ch = legacyChrome(item, lay);
        c.setLineDash([]);
        c.strokeStyle = ACCENT;
        c.lineWidth = 1 / z;
        c.strokeRect(ch.x, ch.y, ch.w, ch.h);
        _drawDeleteChip(c, ch.del.x, ch.del.y, z);
      }
      c.restore();
      return;
    }

    // Boxed text — drawn in the box's rotated frame
    const cx = item.x + lay.effWidth / 2;
    const cy = item.y + lay.effHeight / 2;
    c.translate(cx, cy);
    c.rotate((item.rotation || 0) * Math.PI / 180);
    c.translate(-cx, -cy);

    if (!isEditing) {
      c.setLineDash([]);
      c.textBaseline = "top";
      c.strokeStyle = "#101214";
      c.lineWidth = 2.5;
      let ty = item.y;
      for (const line of lay.lines) {
        c.strokeText(line, item.x, ty);
        c.fillText(line, item.x, ty);
        ty += fontSize * LINE_HEIGHT_MULT;
      }
    } else {
      c.strokeStyle = ACCENT;
      c.lineWidth = 1 / z;
      c.setLineDash([4 / z, 3 / z]);
      c.strokeRect(item.x, item.y, lay.effWidth, lay.effHeight);
    }

    if (isSelected && !isEditing) {
      const ch = textChrome(item, lay);
      const half = 4 / z;
      c.setLineDash([]);
      c.strokeStyle = ACCENT;
      c.lineWidth = 1 / z;
      c.strokeRect(ch.x, ch.y, ch.w, ch.h);

      // rotation stem + handle
      c.beginPath();
      c.moveTo(ch.x + ch.w / 2, ch.rotHandle.stemY);
      c.lineTo(ch.rotHandle.x, ch.rotHandle.y);
      c.stroke();
      c.beginPath();
      c.arc(ch.rotHandle.x, ch.rotHandle.y, half * 1.2, 0, Math.PI * 2);
      c.fillStyle = "#FFFFFF";
      c.fill();
      c.stroke();

      for (const hnd of ch.handles) _drawHandleSquare(c, hnd.x, hnd.y, half, z);
      _drawDeleteChip(c, ch.del.x, ch.del.y, z);
    }
    c.restore();
  }

  function _drawItem(c, item, isSelected = false) {
    c.save();

    const color = item.color || "#FFEB3B";
    const strokeW = item.stroke_width || 2;
    c.strokeStyle = color;
    c.fillStyle = color;
    c.lineWidth = strokeW;
    c.setLineDash(item.dashed ? [4, 4] : []);

    if (item.type === "dot") {
      const r = item.radius || 3.5;
      c.beginPath();
      c.arc(item.x, item.y, r, 0, 2 * Math.PI);
      c.fill();
      c.stroke();
    } else if (item.type === "leader_line" || item.type === "reference_line") {
      c.beginPath();
      c.moveTo(item.x, item.y);
      c.lineTo(item.x2, item.y2);
      c.stroke();
      c.beginPath();
      c.arc(item.x, item.y, 3, 0, 2 * Math.PI);
      c.fill();
    } else if (item.type === "arrow") {
      c.beginPath();
      c.moveTo(item.x, item.y);
      c.lineTo(item.x2, item.y2);
      c.stroke();

      const angle = Math.atan2(item.y2 - item.y, item.x2 - item.x);
      const headLen = Math.max(8, strokeW * 3.5);
      c.beginPath();
      c.moveTo(item.x2, item.y2);
      c.lineTo(item.x2 - headLen * Math.cos(angle - Math.PI / 6), item.y2 - headLen * Math.sin(angle - Math.PI / 6));
      c.lineTo(item.x2 - headLen * Math.cos(angle + Math.PI / 6), item.y2 - headLen * Math.sin(angle + Math.PI / 6));
      c.closePath();
      c.fill();
    } else if (item.type === "text") {
      _drawTextItem(c, item, isSelected);
      c.restore();
      return;
    } else if (item.type === "numbered_marker") {
      const r = item.radius || 9;
      c.beginPath();
      c.arc(item.x, item.y, r, 0, 2 * Math.PI);
      c.fillStyle = "#101214";
      c.fill();
      c.strokeStyle = color;
      c.lineWidth = strokeW;
      c.stroke();

      c.font = `600 11px ${CANVAS_FONT_FAMILY}`;
      c.fillStyle = color;
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillText(String(item.text || item.label || "1"), item.x, item.y);
    } else if (item.type === "bracket") {
      const arm = 6;
      const yTop = Math.min(item.y, item.y2);
      const yBot = Math.max(item.y, item.y2);
      c.beginPath();
      c.moveTo(item.x + arm, yTop);
      c.lineTo(item.x, yTop);
      c.lineTo(item.x, yBot);
      c.lineTo(item.x + arm, yBot);
      c.stroke();
    } else if (item.type === "caliper") {
      _drawCaliper(c, item, color, strokeW);
    }

    // End-point handles for selected line-type / point items
    if (isSelected) {
      const z = getZoom();
      c.setLineDash([]);
      _drawHandleSquare(c, item.x, item.y, 4 / z, z);
      if (item.x2 !== undefined && item.y2 !== undefined) {
        _drawHandleSquare(c, item.x2, item.y2, 4 / z, z);
      }
    }

    c.restore();
  }

  function _drawCaliper(c, item, color, strokeW) {
    const p1x = item.x;
    const p1y = item.y;
    const p2x = item.x2 !== undefined ? item.x2 : item.x;
    const p2y = item.y2 !== undefined ? item.y2 : item.y;

    const dx = p2x - p1x;
    const dy = p2y - p1y;
    const length = Math.hypot(dx, dy);
    const nx = length > 0.1 ? -dy / length : 0;
    const ny = length > 0.1 ? dx / length : -1;
    const tickLen = 6;

    c.save();
    c.lineCap = "round";

    // Dark outline for contrast against tissue
    c.strokeStyle = "rgba(0, 0, 0, 0.85)";
    c.lineWidth = strokeW + 2;
    c.beginPath();
    c.moveTo(p1x, p1y);
    c.lineTo(p2x, p2y);
    c.stroke();

    c.strokeStyle = color;
    c.lineWidth = strokeW;
    c.beginPath();
    c.moveTo(p1x, p1y);
    c.lineTo(p2x, p2y);
    c.stroke();

    // Cross-ticks at both end-points
    c.strokeStyle = "rgba(0, 0, 0, 0.85)";
    c.lineWidth = strokeW + 2;
    c.beginPath();
    c.moveTo(p1x - nx * tickLen, p1y - ny * tickLen);
    c.lineTo(p1x + nx * tickLen, p1y + ny * tickLen);
    c.moveTo(p2x - nx * tickLen, p2y - ny * tickLen);
    c.lineTo(p2x + nx * tickLen, p2y + ny * tickLen);
    c.stroke();

    c.strokeStyle = color;
    c.lineWidth = strokeW;
    c.beginPath();
    c.moveTo(p1x - nx * tickLen, p1y - ny * tickLen);
    c.lineTo(p1x + nx * tickLen, p1y + ny * tickLen);
    c.moveTo(p2x - nx * tickLen, p2y - ny * tickLen);
    c.lineTo(p2x + nx * tickLen, p2y + ny * tickLen);
    c.stroke();

    c.fillStyle = color;
    c.strokeStyle = "#101214";
    c.lineWidth = 1;
    [[p1x, p1y], [p2x, p2y]].forEach(([px, py]) => {
      c.beginPath();
      c.arc(px, py, 3, 0, 2 * Math.PI);
      c.fill();
      c.stroke();
    });

    // Physical distance from the anisotropic pixel pitch
    const scene = State.getState().scene;
    const scanWidthMm = (scene && scene.acquisition && scene.acquisition.scan_width) || 6.0;
    const scanDepthMm = (scene && scene.acquisition && scene.acquisition.scan_depth) || 2.0;
    const canvasW = canvasEl.width || 512;
    const canvasH = canvasEl.height || 512;
    const dxUm = Math.abs(dx) * (scanWidthMm * 1000.0) / canvasW;
    const dzUm = Math.abs(dy) * (scanDepthMm * 1000.0) / canvasH;
    const distUm = Math.hypot(dxUm, dzUm);
    const label = distUm >= 1000.0 ? `${(distUm / 1000.0).toFixed(2)} mm` : `${distUm.toFixed(1)} μm`;

    const offset = 14;
    const badgeX = (p1x + p2x) / 2 + nx * offset;
    const badgeY = (p1y + p2y) / 2 + ny * offset;

    c.font = `600 11px ${CANVAS_FONT_FAMILY}`;
    const pw = Math.max(52, c.measureText(label).width + 12);
    const ph = 18;

    c.fillStyle = "rgba(18, 20, 26, 0.94)";
    c.beginPath();
    c.rect(badgeX - pw / 2, badgeY - ph / 2, pw, ph);
    c.fill();
    c.strokeStyle = color;
    c.lineWidth = 1;
    c.stroke();

    c.fillStyle = color;
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.fillText(label, badgeX, badgeY);
    c.restore();
  }

  // ── In-place text editor ─────────────────────────────────────────────────
  /** Close the editor, keeping its text. Empty text discards the box. */
  function commitEditor() {
    if (!activeEditor) return;
    const ta = activeEditor;
    activeEditor = null;                 // clear first: removing a focused node fires blur
    const value = ta.value;
    const id = ta.dataset.itemId;
    if (ta.parentNode) ta.parentNode.removeChild(ta);

    const item = findItem(id);
    if (!item) {
      redraw();
      return;
    }
    if (!value.trim()) {
      selectedId = id;
      deleteSelected();
      return;
    }
    item.text = value;
    item.label = value;
    fitTextBox(item);
    State.updateAnnotations({ items: [...getItems()] });
    buildPanel();
    redraw();
  }

  function openTextEditor(item, isNew) {
    commitEditor();
    selectedId = item.id;
    State.setSelectedAnnotationId(selectedId);

    const host = document.getElementById("preview-transform");
    if (!host) return;

    const fontSize = item.font_size || 12;
    const lay = layoutTextItem(item);

    const ta = document.createElement("textarea");
    ta.className = "annot-editor";
    ta.dataset.itemId = item.id;
    ta.setAttribute("aria-label", "Annotation text");
    ta.spellcheck = false;
    ta.value = isNew ? "" : (item.text || item.label || "");
    ta.style.fontSize = `${fontSize}px`;
    ta.style.lineHeight = String(LINE_HEIGHT_MULT);
    ta.style.color = item.color || "#FFFFFF";

    if (lay.isLegacy) {
      ta.style.left = `${item.x}px`;
      ta.style.top = `${item.y - fontSize}px`;
      ta.style.width = `${Math.max(DEFAULT_BOX_W, lay.effWidth + 10)}px`;
      ta.style.height = `${fontSize * LINE_HEIGHT_MULT + 2}px`;
    } else {
      ta.style.left = `${item.x}px`;
      ta.style.top = `${item.y}px`;
      ta.style.width = `${lay.effWidth}px`;
      ta.style.height = `${lay.effHeight}px`;
      if (item.rotation) {
        ta.style.transformOrigin = "center center";
        ta.style.transform = `rotate(${item.rotation}deg)`;
      }
    }

    ta.addEventListener("input", () => {
      item.text = ta.value;
      item.label = ta.value;
      if (!lay.isLegacy) {
        fitTextBox(item);
        ta.style.height = `${layoutTextItem(item).effHeight}px`;
      }
    });

    ta.addEventListener("keydown", (e) => {
      e.stopPropagation();               // keep tool shortcuts / delete from firing
      if (e.key === "Escape" || ((e.ctrlKey || e.metaKey) && e.key === "Enter")) {
        e.preventDefault();
        commitEditor();
      }
    });
    ta.addEventListener("keyup", (e) => e.stopPropagation());
    ta.addEventListener("mousedown", (e) => e.stopPropagation());   // no viewport pan while editing
    ta.addEventListener("dblclick", (e) => e.stopPropagation());
    ta.addEventListener("blur", () => commitEditor());

    host.appendChild(ta);
    activeEditor = ta;
    ta.focus();
    if (!isNew) ta.setSelectionRange(ta.value.length, ta.value.length);
    redraw();
  }

  // ── Mouse interaction on the overlay canvas ──────────────────────────────
  function selectItem(id) {
    selectedId = id;
    State.setSelectedAnnotationId(id);
    buildPanel();
    redraw();
  }

  function beginItemDrag(e, hit, pt) {
    e.stopPropagation();
    e.preventDefault();
    selectedId = hit.item.id;
    if (hit.mode === "handle_rot") {
      const lay = layoutTextItem(hit.item);
      rotFlipAtStart = !lay.isLegacy && textChrome(hit.item, lay).rotHandle.flipped;
    }
    isDragging = true;
    dragMode = hit.mode;
    dragOffset = { x: pt.x - hit.item.x, y: pt.y - hit.item.y };
    State.setSelectedAnnotationId(selectedId);
    buildPanel();
    redraw();
  }

  function onCanvasMouseDown(e) {
    if (e.button !== 0) return;
    commitEditor();
    const pt = screenToImageCoords(e);

    if (activeTool === "select") {
      const hit = hitTest(pt);
      if (hit) {
        beginItemDrag(e, hit, pt);
      } else {
        selectItem(null);               // blank space: deselect, allow viewport pan to start
      }
      return;
    }

    if (activeTool === "caliper") {
      const hit = hitTest(pt);
      if (hit && hit.item.type === "caliper") {
        beginItemDrag(e, hit, pt);
        return;
      }
      e.stopPropagation();
      e.preventDefault();
      currentDrawingItem = {
        id: newId(), type: "caliper", x: pt.x, y: pt.y, x2: pt.x, y2: pt.y,
        color: activeColor, stroke_width: activeStrokeWidth, font_size: activeFontSize,
      };
      isDragging = true;
      dragMode = "handle_p2";
      redraw();
      return;
    }

    e.stopPropagation();
    e.preventDefault();

    if (activeTool === "text") {
      // Clicking an existing text box edits it instead of stacking a new one.
      const hit = hitTest(pt);
      if (hit && hit.item.type === "text") {
        selectItem(hit.item.id);
        openTextEditor(hit.item, false);
        return;
      }
      currentDrawingItem = {
        id: newId(), type: "text", x: pt.x, y: pt.y, width: 0, height: 0, rotation: 0,
        color: activeColor, stroke_width: activeStrokeWidth, font_size: activeFontSize,
      };
      dragOrigin = { x: pt.x, y: pt.y };
      isDragging = true;
      dragMode = "text_create";
      redraw();
      return;
    }

    const item = {
      id: newId(), type: activeTool, x: pt.x, y: pt.y,
      color: activeColor, stroke_width: activeStrokeWidth, font_size: activeFontSize,
    };

    if (activeTool === "dot") {
      item.radius = 3.5;
      _commitNewItem(item);
    } else if (activeTool === "numbered_marker") {
      item.text = String(markerCounter++);
      item.radius = 9;
      _commitNewItem(item);
    } else {
      item.x2 = pt.x;
      item.y2 = pt.y;
      currentDrawingItem = item;
      isDragging = true;
      dragMode = "handle_p2";
      redraw();
    }
  }

  function applyResize(item, mode, pt) {
    const lay = layoutTextItem(item);
    const cx = item.x + lay.effWidth / 2;
    const cy = item.y + lay.effHeight / 2;
    const w0 = lay.effWidth;
    const h0 = lay.effHeight;
    const local = rotatePoint(pt.x, pt.y, cx, cy, -(item.rotation || 0));

    let nx = item.x, ny = item.y, nw = w0, nh = h0;
    if (mode.includes("w")) { nw = (item.x + w0) - local.x; nx = local.x; }
    if (mode.includes("e")) { nw = local.x - item.x; }
    if (mode.includes("n")) { nh = (item.y + h0) - local.y; ny = local.y; }
    if (mode.includes("s")) { nh = local.y - item.y; }

    if (nw < MIN_BOX_W) {
      if (mode.includes("w")) nx = item.x + w0 - MIN_BOX_W;
      nw = MIN_BOX_W;
    }

    const fontSize = item.font_size || 12;
    const minH = wrapText(item.text || item.label || "", nw, fontSize).length * fontSize * LINE_HEIGHT_MULT;
    if (nh < minH) {
      if (mode.includes("n")) ny = item.y + h0 - minH;
      nh = minH;
    }

    // Keep the opposite edge fixed in world space for rotated boxes.
    const shift = rotatePoint(nx + nw / 2, ny + nh / 2, cx, cy, item.rotation || 0);
    item.width = nw;
    item.height = nh;
    item.x = shift.x - nw / 2;
    item.y = shift.y - nh / 2;
  }

  function onWindowMouseMove(e) {
    if (!isDragging) {
      if (!canvasEl) return;
      const pt = screenToImageCoords(e);
      const hit = (e.target === canvasEl || canvasEl.contains(e.target)) ? hitTest(pt) : null;
      if (activeTool === "select") {
        canvasEl.style.cursor = cursorForHit(hit) || "default";
      } else {
        canvasEl.style.cursor = activeTool === "text" ? (hit && hit.item.type === "text" ? "text" : "crosshair") : "crosshair";
        if (hit && hit.mode && hit.mode.startsWith("handle")) canvasEl.style.cursor = cursorForHit(hit);
      }
      return;
    }

    e.preventDefault();
    const pt = screenToImageCoords(e);

    if (dragMode === "text_create" && currentDrawingItem) {
      currentDrawingItem.width = pt.x - dragOrigin.x;
      currentDrawingItem.height = pt.y - dragOrigin.y;
      redraw();
      return;
    }

    if (currentDrawingItem) {
      currentDrawingItem.x2 = pt.x;
      currentDrawingItem.y2 = pt.y;
      redraw();
      return;
    }

    if (dragMode === "handle_del") return;

    const item = findItem(selectedId);
    if (!item) return;

    if (dragMode === "item") {
      const dx = pt.x - dragOffset.x - item.x;
      const dy = pt.y - dragOffset.y - item.y;
      item.x += dx;
      item.y += dy;
      if (item.x2 !== undefined) item.x2 += dx;
      if (item.y2 !== undefined) item.y2 += dy;
    } else if (dragMode === "handle_p1") {
      item.x = pt.x;
      item.y = pt.y;
    } else if (dragMode === "handle_p2") {
      item.x2 = pt.x;
      item.y2 = pt.y;
    } else if (dragMode === "handle_rot") {
      const lay = layoutTextItem(item);
      const cx = item.x + lay.effWidth / 2;
      const cy = item.y + lay.effHeight / 2;
      let angle = Math.atan2(pt.y - cy, pt.x - cx) * 180 / Math.PI + 90;
      if (rotFlipAtStart) angle += 180;                   // handle sits below the box
      if (e.shiftKey) angle = Math.round(angle / 15) * 15;
      while (angle < -180) angle += 360;
      while (angle > 180) angle -= 360;
      item.rotation = Math.round(angle * 10) / 10;
    } else if (dragMode.startsWith("handle_")) {
      applyResize(item, dragMode.replace("handle_", ""), pt);
    }
    redraw();
  }

  function onWindowMouseUp(e) {
    if (!isDragging) return;
    isDragging = false;
    const mode = dragMode;
    dragMode = null;

    if (mode === "text_create" && currentDrawingItem) {
      finishTextCreate();
      return;
    }

    if (currentDrawingItem) {
      const item = currentDrawingItem;
      currentDrawingItem = null;
      if (Math.sqrt(distSq(item.x, item.y, item.x2, item.y2)) >= 3) {
        _commitNewItem(item);
        if (item.type === "caliper") refreshCaliperMetrics();
      } else {
        redraw();
      }
      return;
    }

    if (mode === "handle_del") {
      const hit = hitTest(screenToImageCoords(e));
      if (hit && hit.mode === "handle_del" && hit.item.id === selectedId) deleteSelected();
      else redraw();
      return;
    }

    if (selectedId) {
      const item = findItem(selectedId);
      if (item && item.type === "text") fitTextBox(item);
      State.updateAnnotations({ items: [...getItems()] });
      if (item && item.type === "caliper") refreshCaliperMetrics();
      buildPanel();
      redraw();
    }
  }

  function finishTextCreate() {
    const draft = currentDrawingItem;
    currentDrawingItem = null;

    const fontSize = draft.font_size || 12;
    const minH = fontSize * LINE_HEIGHT_MULT;
    let { x, y, width: w, height: h } = draft;
    if (w < 0) { x += w; w = -w; }
    if (h < 0) { y += h; h = -h; }

    if (w < 5 && h < 5) {                // plain click → default box
      w = DEFAULT_BOX_W;
      h = minH;
    } else {
      w = Math.max(MIN_BOX_W, w);
      h = Math.max(minH, h);
    }

    // keep the box on the image
    const W = canvasEl.width;
    const H = canvasEl.height;
    x = Math.max(0, Math.min(x, W - Math.min(w, W)));
    y = Math.max(0, Math.min(y, H - Math.min(h, H)));

    const item = {
      id: draft.id, type: "text", x, y, width: w, height: h, rotation: 0,
      text: "", label: "", color: draft.color, stroke_width: draft.stroke_width, font_size: fontSize,
    };

    State.updateAnnotations({ items: [...getItems(), item] });
    setTool("select");                   // tool returns to Select; the new box stays selected
    selectedId = item.id;
    openTextEditor(item, true);
  }

  function _commitNewItem(item) {
    selectedId = item.id;
    State.updateAnnotations({ items: [...getItems(), item] });
    State.setSelectedAnnotationId(selectedId);
    buildPanel();
    redraw();
  }

  function onCanvasDblClick(e) {
    const hit = hitTest(screenToImageCoords(e));
    if (hit && hit.item.type === "text" && hit.mode !== "handle_del") {
      e.stopPropagation();
      selectedId = hit.item.id;
      openTextEditor(hit.item, false);
    }
  }

  function _bindCanvasEvents() {
    if (!canvasEl) return;
    canvasEl.addEventListener("mousedown", onCanvasMouseDown);
    canvasEl.addEventListener("dblclick", onCanvasDblClick);
    window.addEventListener("mousemove", onWindowMouseMove);
    window.addEventListener("mouseup", onWindowMouseUp);
  }

  // ── Keyboard (registered exactly once) ───────────────────────────────────
  function activeWorkspace() {
    return typeof window.getActiveWorkspace === "function" ? window.getActiveWorkspace() : ANNOTATE_WORKSPACE;
  }

  function onKeyDown(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (activeEditor) return;
    const el = document.activeElement;
    const tag = el ? el.tagName : "";
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (el && el.isContentEditable);
    if (typing) return;
    if (activeWorkspace() !== ANNOTATE_WORKSPACE) return;

    if ((e.key === "Delete" || e.key === "Backspace") && selectedId) {
      e.preventDefault();
      deleteSelected();
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      if (activeTool !== "select") setTool("select");
      if (selectedId) selectItem(null);
      return;
    }
    const tool = TOOL_KEYS[e.key.toLowerCase()];
    if (tool) {
      e.preventDefault();
      setTool(tool);
    }
  }

  // ── Commands ─────────────────────────────────────────────────────────────
  function deleteSelected() {
    if (!selectedId) return;
    const hadCaliper = getItems().some((i) => i.id === selectedId && i.type === "caliper");
    const items = getItems().filter((i) => i.id !== selectedId);
    selectedId = null;
    State.updateAnnotations({ items });
    State.setSelectedAnnotationId(null);
    if (hadCaliper) refreshCaliperMetrics();
    buildPanel();
    redraw();
  }

  function clearAll() {
    commitEditor();
    selectedId = null;
    State.updateAnnotations({ items: [] });
    State.setSelectedAnnotationId(null);
    refreshCaliperMetrics();
    buildPanel();
    redraw();
  }

  async function autoLabelLayers(probeX = null) {
    const currentState = State.getState();
    const renderId = currentState.renderId;
    if (!renderId) {
      if (typeof Dialogs !== "undefined") {
        Dialogs.notify("Please wait for a scene render before auto-labeling.", "warn");
      }
      return;
    }

    try {
      const palette = currentState.visualization.palette || "clinical";
      const res = await API.annotate(renderId, true, probeX, palette, currentState.annotations);
      if (res && res.annotations) {
        State.updateAnnotations({
          items: res.annotations.items || [],
          legend: res.annotations.legend || currentState.annotations.legend,
        });
        buildPanel();
        redraw();
      }
    } catch (err) {
      console.error("Auto-label failed:", err);
      if (typeof Dialogs !== "undefined") {
        Dialogs.notify(`Auto-label failed: ${err.message}`, "error");
      }
    }
  }

  // ── Panel (left rail): controls, inspector, item list ────────────────────
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function buildInspector(container, selectedItem) {
    const card = el("div", "section-card section-card-selected");

    const header = el("div", "section-card-title");
    header.appendChild(el("span", "insp-title", `Selected: ${TOOL_LABELS[selectedItem.type] || selectedItem.type}`));
    const del = el("button", "btn-remove", "×");
    del.type = "button";
    del.title = "Delete selected item";
    del.setAttribute("aria-label", "Delete selected item");
    del.addEventListener("click", () => deleteSelected());
    header.appendChild(del);
    card.appendChild(header);

    const touch = (fn) => (ev) => {
      fn(ev);
      State.updateAnnotations({ items: [...getItems()] });
      redraw();
    };

    if (selectedItem.text !== undefined || selectedItem.label !== undefined) {
      const row = el("div", "ctrl-row");
      const label = el("div", "ctrl-label");
      label.appendChild(el("span", "ctrl-name", "Text"));
      row.appendChild(label);
      const ta = el("textarea", "insp-textarea");
      ta.value = selectedItem.text || selectedItem.label || "";
      ta.addEventListener("input", touch((ev) => {
        selectedItem.text = ev.target.value;
        selectedItem.label = ev.target.value;
        fitTextBox(selectedItem);
      }));
      row.appendChild(ta);
      card.appendChild(row);

      if (selectedItem.type === "text") {
        const fsRow = el("div", "ctrl-row");
        const fsLabel = el("div", "ctrl-label");
        fsLabel.appendChild(el("span", "ctrl-name", "Font size"));
        fsLabel.appendChild(el("span", "ctrl-unit", "px"));
        fsRow.appendChild(fsLabel);
        const fs = el("input");
        fs.type = "number";
        fs.min = "6";
        fs.max = "72";
        fs.value = selectedItem.font_size || 12;
        fs.addEventListener("input", touch((ev) => {
          const v = parseInt(ev.target.value, 10);
          selectedItem.font_size = Math.max(6, Math.min(72, isNaN(v) ? 12 : v));
          fitTextBox(selectedItem);
        }));
        fsRow.appendChild(fs);
        card.appendChild(fsRow);

        if (isBoxedText(selectedItem)) {
          const rotRow = el("div", "ctrl-row");
          const rotLabel = el("div", "ctrl-label");
          rotLabel.appendChild(el("span", "ctrl-name", "Rotation"));
          rotLabel.appendChild(el("span", "ctrl-unit", "deg"));
          rotRow.appendChild(rotLabel);
          const rot = el("input");
          rot.type = "number";
          rot.min = "-180";
          rot.max = "180";
          rot.step = "1";
          rot.value = Math.round(selectedItem.rotation || 0);
          rot.addEventListener("input", touch((ev) => {
            const v = parseFloat(ev.target.value);
            selectedItem.rotation = isNaN(v) ? 0 : Math.max(-180, Math.min(180, v));
          }));
          rotRow.appendChild(rot);
          card.appendChild(rotRow);
        }
      }
    }

    const colRow = el("div", "ctrl-row");
    const colLabel = el("div", "ctrl-label");
    colLabel.appendChild(el("span", "ctrl-name", "Color"));
    colRow.appendChild(colLabel);
    const col = el("input", "insp-color");
    col.type = "color";
    col.value = selectedItem.color || "#FFFFFF";
    col.addEventListener("input", touch((ev) => { selectedItem.color = ev.target.value; }));
    colRow.appendChild(col);
    card.appendChild(colRow);

    container.appendChild(card);
  }

  function buildPanel() {
    const container = document.getElementById("annotation-body");
    if (!container) return;
    container.innerHTML = "";

    const items = getItems();
    const selectedItem = items.find((i) => i.id === selectedId);

    // 1. Controls
    const toolsCard = el("div", "section-card");
    toolsCard.appendChild(el("div", "section-card-title", "Annotation controls"));

    toolsCard.appendChild(Controls.makeSlider({
      label: "Stroke width", unit: "px", min: 1, max: 6, step: 1, value: activeStrokeWidth, decimals: 0,
      onChange: (v) => {
        activeStrokeWidth = v;
        const swInput = document.getElementById("tool-stroke-width");
        if (swInput) swInput.value = v;
      },
    }));

    const actionRow = el("div", "action-row");
    const autoBtn = el("button", "btn-primary btn-grow");
    autoBtn.type = "button";
    autoBtn.innerHTML = '<svg class="icon" aria-hidden="true"><use href="assets/icons.svg#tag"></use></svg><span>Auto-label layers</span>';
    autoBtn.addEventListener("click", () => autoLabelLayers());
    actionRow.appendChild(autoBtn);

    const clearBtn = el("button", "btn-secondary", "Clear all");
    clearBtn.type = "button";
    clearBtn.addEventListener("click", () => clearAll());
    actionRow.appendChild(clearBtn);
    toolsCard.appendChild(actionRow);
    container.appendChild(toolsCard);

    // 2. Inspector for the selected item
    if (selectedItem) buildInspector(container, selectedItem);

    // 3. Active items list
    const listCard = el("div", "section-card");
    listCard.appendChild(el("div", "section-card-title", `Items (${items.length})`));
    const list = el("div", "instance-list-items");

    if (items.length === 0) {
      list.appendChild(el("div", "instance-empty", "No annotations yet. Choose a tool in the toolbar, or use Auto-label."));
    } else {
      items.forEach((item, idx) => {
        const row = el("div", "item-row" + (item.id === selectedId ? " is-selected" : ""));
        const left = el("div", "item-row-main");
        const swatch = el("span", "item-swatch");
        swatch.style.background = item.color || "#fff";
        left.appendChild(swatch);
        const text = (item.text || item.label || "").replace(/\s+/g, " ").trim();
        left.appendChild(el("span", "item-row-label", `${idx + 1}. ${text || TOOL_LABELS[item.type] || item.type}`));
        row.appendChild(left);

        const rm = el("button", "btn-remove", "×");
        rm.type = "button";
        rm.title = "Delete item";
        rm.setAttribute("aria-label", "Delete item");
        row.appendChild(rm);

        row.addEventListener("click", (ev) => {
          if (ev.target === rm) {
            selectedId = item.id;
            deleteSelected();
          } else {
            selectItem(item.id);
          }
        });
        list.appendChild(row);
      });
    }
    listCard.appendChild(list);
    container.appendChild(listCard);
  }

  // ── Tools ────────────────────────────────────────────────────────────────
  function setTool(toolName) {
    if (!TOOL_LABELS[toolName]) toolName = "select";
    const changed = toolName !== activeTool;
    if (changed) commitEditor();
    activeTool = toolName;
    if (changed && toolName !== "select") selectedId = null;   // choosing a drawing tool deselects
    State.setActiveAnnotationTool(toolName);
    if (changed && toolName !== "select") State.setSelectedAnnotationId(null);

    document.querySelectorAll(".btn-tool").forEach((btn) => {
      const on = btn.getAttribute("data-tool") === toolName;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });

    const statusTool = document.getElementById("val-active-tool");
    if (statusTool) statusTool.textContent = TOOL_LABELS[toolName];

    if (canvasEl) canvasEl.style.cursor = toolName === "select" ? "default" : (toolName === "text" ? "text" : "crosshair");
    buildPanel();
    redraw();
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    ACCENT = cssVar("--accent", ACCENT);
    DANGER = cssVar("--danger", DANGER);

    canvasEl = document.getElementById("annotation-overlay-canvas");
    if (canvasEl) ctx = canvasEl.getContext("2d");

    _bindCanvasEvents();
    window.addEventListener("keydown", onKeyDown);

    document.querySelectorAll(".btn-tool").forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.classList.contains("active") ? "true" : "false");
      btn.addEventListener("click", () => {
        const tool = btn.getAttribute("data-tool");
        if (tool) setTool(tool);
      });
    });

    const quickAutoBtn = document.getElementById("btn-quick-autolabel");
    if (quickAutoBtn) quickAutoBtn.addEventListener("click", () => autoLabelLayers());

    const delSelectedBtn = document.getElementById("btn-delete-selected");
    if (delSelectedBtn) delSelectedBtn.addEventListener("click", () => deleteSelected());

    const colorPicker = document.getElementById("tool-color-picker");
    if (colorPicker) {
      activeColor = colorPicker.value || activeColor;        // new items use the colour the picker shows
      colorPicker.addEventListener("input", (e) => { activeColor = e.target.value; });
    }

    const strokeInput = document.getElementById("tool-stroke-width");
    if (strokeInput) {
      activeStrokeWidth = parseInt(strokeInput.value, 10) || activeStrokeWidth;
      strokeInput.addEventListener("input", (e) => {
        activeStrokeWidth = parseInt(e.target.value, 10) || 2;
      });
    }

    const toggleAnn = document.getElementById("toggle-show-annotations");
    if (toggleAnn) toggleAnn.addEventListener("change", () => redraw());
    const toggleMeas = document.getElementById("toggle-show-measurements");
    if (toggleMeas) toggleMeas.addEventListener("change", () => redraw());

    // Overlay resolution follows the natural image resolution
    const imgEl = document.getElementById("bscan-img");
    if (imgEl) {
      imgEl.addEventListener("load", () => {
        if (!canvasEl) return;
        canvasEl.width = imgEl.naturalWidth || 512;
        canvasEl.height = imgEl.naturalHeight || 512;
        redraw();
      });
    }

    State.subscribe((s) => {
      const countEl = document.getElementById("val-annot-count");
      if (countEl) countEl.textContent = (s.annotations.items || []).length;
      redraw();
    });

    buildPanel();

    if (document.fonts && document.fonts.load) {
      document.fonts.load('600 12px "Outfit"').then(() => redraw());
    }
  }

  return {
    init,
    buildPanel,
    redraw,
    setTool,
    autoLabelLayers,
    deleteSelected,
    clearAll,
    addOverlayRenderer,
  };
})();
