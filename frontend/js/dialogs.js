/**
 * OCTForge Dialogs.
 *
 * Provides:
 *   Dialogs.confirmAsync(opts)  → Promise<boolean>
 *     opts: { title, message, confirmLabel, cancelLabel, danger }
 *
 *   Dialogs.promptAsync(opts)   → Promise<string|null>
 *     opts: { title, message, defaultValue, placeholder, confirmLabel, beforeConfirm }
 *     beforeConfirm(value) may be async; resolving false keeps the dialog (and the
 *     entered text) open. Buttons are disabled while it runs, so it cannot be re-submitted.
 *
 *   Dialogs.choiceAsync(opts)   → Promise<string>
 *     opts: { title, message, choices: [{ label, value, kind }], cancelValue }
 *     kind: "primary" | "secondary" | "danger". Escape resolves cancelValue.
 *
 *   Dialogs.notify(message, kind)
 *     kind: "info" | "ok" | "warn" | "error"
 *     Auto-dismisses after ~4s (errors persist until dismissed or replaced).
 *
 * All dialogs:
 *  - Are built on native <dialog> (modal, platform focus-trap, Escape cancels).
 *  - Return focus to the invoking element on close.
 *  - Use flat rectangular design matching Phase 1/2 tokens.
 *  - No external dependencies.
 */

const Dialogs = (function () {

  // ── Inject dialog styles once ────────────────────────────────────────────
  function _ensureStyles() {
    if (document.getElementById("dialogs-style")) return;
    const style = document.createElement("style");
    style.id = "dialogs-style";
    style.textContent = `
      /* Dialogs — OCTForge Phase 2 */
      .of-dialog::backdrop {
        background: rgba(0, 0, 0, 0.45);
      }
      .of-dialog {
        /* The global reset zeroes margins; a modal <dialog> relies on margin:auto
           (with the UA's fixed/inset:0) to sit centered in the visible viewport. */
        margin: auto;
        border: 1px solid var(--border);
        border-radius: var(--r-md);
        padding: 0;
        background: var(--surface);
        color: var(--text);
        font-family: var(--font-ui);
        font-size: var(--fs-base);
        min-width: 320px;
        max-width: min(480px, calc(100vw - 32px));
        box-shadow: 0 4px 16px rgba(0,0,0,0.18);
      }
      .of-dialog-header {
        padding: 12px 16px 10px;
        border-bottom: 1px solid var(--hairline);
        font-size: 13px;
        font-weight: 600;
        color: var(--text-strong);
      }
      .of-dialog-body {
        padding: 14px 16px;
        font-size: 13px;
        color: var(--text);
        line-height: 1.5;
      }
      .of-dialog-input {
        width: 100%;
        margin-top: 8px;
        height: var(--control-h);
        padding: 0 8px;
        background: var(--inset);
        border: 1px solid var(--border-strong);
        border-radius: var(--r-sm);
        color: var(--text-strong);
        font-family: var(--font-ui);
        font-size: var(--fs-sm);
        outline: none;
        transition: border-color var(--tr-fast);
      }
      .of-dialog-input:focus { border-color: var(--accent); }
      .of-dialog-footer {
        padding: 10px 16px 12px;
        border-top: 1px solid var(--hairline);
        display: flex;
        justify-content: flex-end;
        gap: 8px;
      }
      /* Notify toast */
      #of-notify-region {
        position: fixed;
        bottom: 32px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 9000;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 6px;
        pointer-events: none;
      }
      .of-notify {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 6px 12px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: var(--r-sm);
        font-family: var(--font-ui);
        font-size: var(--fs-sm);
        color: var(--text-strong);
        box-shadow: 0 2px 8px rgba(0,0,0,0.12);
        pointer-events: auto;
        max-width: 420px;
        opacity: 0;
        animation: ofNotifyIn 140ms ease forwards;
      }
      .of-notify.fading { animation: ofNotifyOut 200ms ease forwards; }
      .of-notify.ok    { border-left: 3px solid var(--ok);     color: var(--ok); }
      .of-notify.warn  { border-left: 3px solid var(--warn);   color: var(--warn); }
      .of-notify.error { border-left: 3px solid var(--danger); color: var(--danger); }
      @keyframes ofNotifyIn  { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:translateY(0); } }
      @keyframes ofNotifyOut { from { opacity:1; } to { opacity:0; } }
      .of-notify-dismiss {
        margin-left: 4px;
        background: transparent;
        border: none;
        color: inherit;
        opacity: 0.6;
        cursor: pointer;
        padding: 0 2px;
        font-size: 14px;
        line-height: 1;
      }
      .of-notify-dismiss:hover { opacity: 1; }
    `;
    document.head.appendChild(style);
    // Notify region
    if (!document.getElementById("of-notify-region")) {
      const region = document.createElement("div");
      region.id = "of-notify-region";
      region.setAttribute("aria-live", "polite");
      region.setAttribute("role", "status");
      document.body.appendChild(region);
    }
  }

  // ── Shared dialog builder ────────────────────────────────────────────────
  function _makeDialog(header, bodyEl, footerEl) {
    const dlg = document.createElement("dialog");
    dlg.className = "of-dialog";

    const hdr = document.createElement("div");
    hdr.className = "of-dialog-header";
    hdr.textContent = header;
    dlg.appendChild(hdr);

    const body = document.createElement("div");
    body.className = "of-dialog-body";
    body.appendChild(bodyEl);
    dlg.appendChild(body);

    dlg.appendChild(footerEl);
    document.body.appendChild(dlg);
    return dlg;
  }

  function _btn(label, cls) {
    const b = document.createElement("button");
    b.className = cls;
    b.textContent = label;
    return b;
  }

  // ── confirmAsync ─────────────────────────────────────────────────────────
  /**
   * @param {object} opts
   * @param {string} opts.title
   * @param {string} opts.message
   * @param {string} [opts.confirmLabel="Confirm"]
   * @param {string} [opts.cancelLabel="Cancel"]
   * @param {boolean} [opts.danger=false] — use danger button style for confirm
   * @returns {Promise<boolean>}
   */
  function confirmAsync(opts = {}) {
    _ensureStyles();
    const {
      title = "Confirm",
      message = "",
      confirmLabel = "Confirm",
      cancelLabel = "Cancel",
      danger = false,
    } = opts;

    return new Promise((resolve) => {
      const invoker = document.activeElement;

      const msgEl = document.createElement("p");
      msgEl.style.margin = "0";
      msgEl.textContent = message;

      const footer = document.createElement("div");
      footer.className = "of-dialog-footer";

      const cancelBtn = _btn(cancelLabel, "btn-secondary");
      const confirmBtn = _btn(confirmLabel, danger ? "btn-danger" : "btn-primary");

      footer.appendChild(cancelBtn);
      footer.appendChild(confirmBtn);

      const dlg = _makeDialog(title, msgEl, footer);

      const close = (result) => {
        dlg.close();
        dlg.remove();
        if (invoker && invoker.focus) invoker.focus();
        resolve(result);
      };

      cancelBtn.addEventListener("click", () => close(false));
      confirmBtn.addEventListener("click", () => close(true));
      dlg.addEventListener("cancel", () => close(false));   // Escape key

      // Focus confirm (or cancel for danger) so Enter works immediately
      dlg.showModal();
      if (danger) cancelBtn.focus();
      else confirmBtn.focus();
    });
  }

  // ── choiceAsync ──────────────────────────────────────────────────────────
  /**
   * Multi-button confirmation (e.g. Save / Discard / Cancel).
   * @param {object} opts
   * @param {string} opts.title
   * @param {string} opts.message
   * @param {{label:string,value:string,kind?:"primary"|"secondary"|"danger"}[]} opts.choices
   *        Rendered left→right in the order given; the first "primary" gets initial focus.
   * @param {string} opts.cancelValue — resolved on Escape
   * @returns {Promise<string>}
   */
  function choiceAsync(opts = {}) {
    _ensureStyles();
    const { title = "Confirm", message = "", choices = [], cancelValue = null } = opts;

    return new Promise((resolve) => {
      const invoker = document.activeElement;

      const msgEl = document.createElement("p");
      msgEl.style.margin = "0";
      msgEl.textContent = message;

      const footer = document.createElement("div");
      footer.className = "of-dialog-footer";

      const dlg = _makeDialog(title, msgEl, footer);
      let done = false;
      const close = (result) => {
        if (done) return;                 // a double click can only resolve once
        done = true;
        dlg.close();
        dlg.remove();
        if (invoker && invoker.focus) invoker.focus();
        resolve(result);
      };

      let focusBtn = null;
      choices.forEach((c) => {
        const cls = c.kind === "danger" ? "btn-danger" : c.kind === "primary" ? "btn-primary" : "btn-secondary";
        const b = _btn(c.label, cls);
        b.addEventListener("click", () => close(c.value));
        footer.appendChild(b);
        if (!focusBtn && c.kind === "primary") focusBtn = b;
      });

      dlg.addEventListener("cancel", () => close(cancelValue));   // Escape
      dlg.showModal();
      (focusBtn || footer.lastElementChild || dlg).focus();
    });
  }

  // ── promptAsync ──────────────────────────────────────────────────────────
  /**
   * @param {object} opts
   * @param {string} opts.title
   * @param {string} opts.message
   * @param {string} [opts.defaultValue=""]
   * @param {string} [opts.placeholder=""]
   * @param {string} [opts.confirmLabel="OK"]
   * @returns {Promise<string|null>} — null if cancelled
   */
  function promptAsync(opts = {}) {
    _ensureStyles();
    const {
      title = "Input",
      message = "",
      defaultValue = "",
      placeholder = "",
      confirmLabel = "OK",
      beforeConfirm = null,
    } = opts;

    return new Promise((resolve) => {
      const invoker = document.activeElement;

      const wrap = document.createElement("div");

      if (message) {
        const msgEl = document.createElement("p");
        msgEl.style.margin = "0 0 4px 0";
        msgEl.textContent = message;
        wrap.appendChild(msgEl);
      }

      const input = document.createElement("input");
      input.type = "text";
      input.className = "of-dialog-input";
      input.value = defaultValue;
      input.placeholder = placeholder;
      wrap.appendChild(input);

      const footer = document.createElement("div");
      footer.className = "of-dialog-footer";

      const cancelBtn = _btn("Cancel", "btn-secondary");
      const okBtn = _btn(confirmLabel, "btn-primary");

      footer.appendChild(cancelBtn);
      footer.appendChild(okBtn);

      const dlg = _makeDialog(title, wrap, footer);

      const close = (result) => {
        dlg.close();
        dlg.remove();
        if (invoker && invoker.focus) invoker.focus();
        resolve(result);
      };

      // Confirm: optionally gated by an async beforeConfirm(value). While it runs the
      // controls are disabled (no double submit) and Escape is ignored; if it resolves
      // anything other than true the dialog stays open with the entered text intact.
      let busy = false;
      const submit = async () => {
        if (busy) return;
        const value = input.value;
        if (beforeConfirm) {
          busy = true;
          cancelBtn.disabled = okBtn.disabled = input.disabled = true;
          let proceed = false;
          try { proceed = (await beforeConfirm(value)) === true; } catch (err) { console.error(err); }
          busy = false;
          cancelBtn.disabled = okBtn.disabled = input.disabled = false;
          if (!proceed) { input.focus(); return; }
        }
        close(value);
      };

      cancelBtn.addEventListener("click", () => close(null));
      okBtn.addEventListener("click", submit);
      dlg.addEventListener("cancel", (e) => {               // Escape
        if (busy) { e.preventDefault(); return; }
        close(null);
      });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); submit(); }
      });

      dlg.showModal();
      input.select();
    });
  }

  // ── notify ───────────────────────────────────────────────────────────────
  let _activeError = null;

  /**
   * @param {string} message
   * @param {"info"|"ok"|"warn"|"error"} [kind="info"]
   */
  function notify(message, kind = "info") {
    _ensureStyles();
    const region = document.getElementById("of-notify-region");
    if (!region) return;

    // Errors replace the previous error toast
    if (kind === "error" && _activeError) {
      _activeError.remove();
      _activeError = null;
    }

    const toast = document.createElement("div");
    toast.className = "of-notify" + (kind !== "info" ? " " + kind : "");
    toast.setAttribute("role", "alert");

    const text = document.createElement("span");
    text.textContent = message;
    toast.appendChild(text);

    const closeBtn = document.createElement("button");
    closeBtn.className = "of-notify-dismiss";
    closeBtn.innerHTML = "&times;";
    closeBtn.setAttribute("aria-label", "Dismiss");
    closeBtn.addEventListener("click", () => dismiss(toast));
    toast.appendChild(closeBtn);

    region.appendChild(toast);

    if (kind === "error") {
      _activeError = toast;
    } else {
      const delay = kind === "warn" ? 6000 : 4000;
      setTimeout(() => dismiss(toast), delay);
    }
  }

  function dismiss(toast) {
    if (!toast || !toast.parentNode) return;
    toast.classList.add("fading");
    setTimeout(() => {
      if (toast.parentNode) toast.remove();
      if (toast === _activeError) _activeError = null;
    }, 210);
  }

  // ── Danger button style (not defined in style.css yet for dialogs) ───────
  // Inject .btn-danger if missing
  function _ensureDangerBtn() {
    if (document.getElementById("dialogs-danger-style")) return;
    const s = document.createElement("style");
    s.id = "dialogs-danger-style";
    s.textContent = `
      .btn-danger {
        height: var(--control-h);
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 0 12px;
        border-radius: var(--r-sm);
        font-size: var(--fs-sm);
        font-weight: 500;
        white-space: nowrap;
        background: var(--danger);
        border: 1px solid var(--danger);
        color: #fff;
        cursor: pointer;
        transition: background var(--tr-fast), border-color var(--tr-fast);
      }
      .btn-danger:hover:not(:disabled) {
        background: #91261F;
        border-color: #91261F;
      }
      .btn-danger:disabled { opacity: 0.45; cursor: default; }
    `;
    document.head.appendChild(s);
  }

  // Initialise on first import
  _ensureDangerBtn();

  return { confirmAsync, promptAsync, choiceAsync, notify };
})();
