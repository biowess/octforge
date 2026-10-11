/**
 * Help menu (Documentation / About / Privacy) in the workspace application bar.
 * Opens the information pages in a new tab so the current scene is never lost.
 * Keyboard: Enter/Space/ArrowDown opens, ArrowUp/Down move, Escape closes.
 */
(function () {
  "use strict";
  const btn = document.getElementById("btn-help-menu");
  const menu = document.getElementById("help-popover");
  if (!btn || !menu) return;

  const items = () => Array.from(menu.querySelectorAll("a"));

  function open(focusFirst) {
    menu.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    if (focusFirst) {
      const first = items()[0];
      if (first) first.focus();
    }
  }

  function close(returnFocus) {
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    if (returnFocus) btn.focus();
  }

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (menu.hidden) open(e.detail === 0);   // keyboard activation moves focus into the menu
    else close(true);
  });

  btn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      open(true);
    }
  });

  document.addEventListener("click", (e) => {
    if (!menu.hidden && !menu.contains(e.target) && e.target !== btn && !btn.contains(e.target)) close(false);
  });

  document.addEventListener("keydown", (e) => {
    if (menu.hidden) return;
    if (e.key === "Escape") {
      e.preventDefault();
      close(true);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const list = items();
      const idx = list.indexOf(document.activeElement);
      const next = e.key === "ArrowDown"
        ? (idx < list.length - 1 ? idx + 1 : 0)
        : (idx > 0 ? idx - 1 : list.length - 1);
      list[next].focus();
    } else if (e.key === "Tab") {
      close(false);
    }
  });
})();
