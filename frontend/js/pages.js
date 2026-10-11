/**
 * Information pages: highlights the current section in the contents rail while
 * the document scrolls. Pure progressive enhancement — the rail works without it.
 */
(function () {
  "use strict";
  const links = Array.from(document.querySelectorAll('.page-toc a[href^="#"]'));
  if (!links.length) return;

  const targets = new Map();
  links.forEach((a) => {
    const el = document.getElementById(decodeURIComponent(a.getAttribute("href").slice(1)));
    if (el) targets.set(el, a);
  });
  const headings = Array.from(targets.keys());
  if (!headings.length) return;

  // A clicked entry stays current until the reader scrolls on their own — the last
  // sections of a short page cannot reach the top, so scroll position alone would
  // highlight the wrong entry.
  let chosen = null;
  ["wheel", "touchmove", "keydown"].forEach((ev) =>
    window.addEventListener(ev, () => { chosen = null; update(); }, { passive: true }));
  links.forEach((a) => a.addEventListener("click", () => { chosen = targets.size ? a : null; }));

  function update() {
    if (chosen) {
      links.forEach((a) => (a === chosen ? a.setAttribute("aria-current", "true") : a.removeAttribute("aria-current")));
      return;
    }
    const bar = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--topbar-h"), 10) || 38;
    const offset = bar + 28;
    let current = headings[0];
    for (const h of headings) {
      if (h.getBoundingClientRect().top - offset <= 0) current = h;
      else break;
    }
    const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
    if (atBottom) current = headings[headings.length - 1];

    links.forEach((a) => {
      if (a === targets.get(current)) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
  }

  window.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);
  update();
})();
