/**
 * OCTForge Controls Module, Reusable DOM Control Factory Functions.
 *
 * Exports plain functions that build labeled DOM control widgets with no
 * dependencies beyond the browser. Each function returns an HTMLElement
 * (or a compound container) that can be appended anywhere in the rail.
 *
 * Public API:
 *   Controls.makeSlider(opts)        → .ctrl-row div (slider + live value)
 *   Controls.makeNumericInput(opts)  → .ctrl-row div (number input)
 *   Controls.makeToggle(opts)        → .ctrl-row div (checkbox toggle)
 *   Controls.makeSelect(opts)        → .ctrl-row div (select dropdown)
 *   Controls.makeInstanceList(opts)  → compound editor (add/remove/edit instances)
 *   Controls.makeSectionHeading(text)→ .ctrl-sub-heading div
 *   Controls.init()                  → wire panel collapse, mode toggle
 */

const Controls = (function () {

  // ── Slider ─────────────────────────────────────────────────────────────
  /**
   * Build a labeled range slider row.
   * @param {Object} opts
   *   label    {string}   – human-readable name
   *   unit     {string}   – optional unit string shown after value
   *   min, max, step, value {number}
   *   decimals {number}   – decimal places for the value display
   *   advanced {boolean}  – if true, adds class adv-only (hidden in basic mode)
   *   layerColor {string} – optional CSS class for value color (e.g. 'lc-rnfl')
   *   onChange {function(value:number)} – called on every input event
   * @returns {HTMLDivElement}
   */
  function makeSlider(opts) {
    const {
      label, unit = "", min, max, step, value,
      decimals = 2, advanced = false, layerColor = "",
      onChange,
    } = opts;

    const row = document.createElement("div");
    row.className = "ctrl-row" + (advanced ? " adv-only" : "");

    const labelEl = document.createElement("div");
    labelEl.className = "ctrl-label";

    const nameEl = document.createElement("span");
    nameEl.className = "ctrl-name";
    nameEl.textContent = label;
    labelEl.appendChild(nameEl);

    const rightGroup = document.createElement("span");
    rightGroup.style.display = "flex";
    rightGroup.style.alignItems = "baseline";
    rightGroup.style.gap = "3px";

    const valEl = document.createElement("span");
    valEl.className = "ctrl-val" + (layerColor ? " " + layerColor : "");
    valEl.textContent = Number(value).toFixed(decimals);
    rightGroup.appendChild(valEl);

    if (unit) {
      const unitEl = document.createElement("span");
      unitEl.className = "ctrl-unit";
      unitEl.textContent = unit;
      rightGroup.appendChild(unitEl);
    }

    labelEl.appendChild(rightGroup);
    row.appendChild(labelEl);

    const slider = document.createElement("input");
    slider.type  = "range";
    slider.min   = min;
    slider.max   = max;
    slider.step  = step;
    slider.value = value;

    slider.addEventListener("input", (e) => {
      const v = parseFloat(e.target.value);
      valEl.textContent = v.toFixed(decimals);
      if (onChange) onChange(v);
    });

    row.appendChild(slider);

    // Expose a programmatic setter so state restoration can sync the display.
    row._setValue = (v) => {
      slider.value = v;
      valEl.textContent = Number(v).toFixed(decimals);
    };

    return row;
  }

  // ── Numeric Input ──────────────────────────────────────────────────────
  /**
   * Build a labeled number-input row.
   * @param {Object} opts
   *   label, unit, min, max, step, value, decimals, advanced, onChange
   * @returns {HTMLDivElement}
   */
  function makeNumericInput(opts) {
    const {
      label, unit = "", min, max, step = 1, value,
      decimals = 0, advanced = false, onChange,
    } = opts;

    const row = document.createElement("div");
    row.className = "ctrl-row" + (advanced ? " adv-only" : "");

    const labelEl = document.createElement("div");
    labelEl.className = "ctrl-label";

    const nameEl = document.createElement("span");
    nameEl.className = "ctrl-name";
    nameEl.textContent = label;
    labelEl.appendChild(nameEl);

    if (unit) {
      const unitEl = document.createElement("span");
      unitEl.className = "ctrl-unit";
      unitEl.textContent = unit;
      labelEl.appendChild(unitEl);
    }

    row.appendChild(labelEl);

    const input = document.createElement("input");
    input.type  = "number";
    input.min   = min;
    input.max   = max;
    input.step  = step;
    input.value = value;

    input.addEventListener("change", (e) => {
      let v = parseFloat(e.target.value);
      if (isNaN(v)) return;
      v = Math.min(max, Math.max(min, v));
      input.value = v;
      if (onChange) onChange(v);
    });

    row.appendChild(input);

    row._setValue = (v) => { input.value = v; };

    return row;
  }

  // ── Toggle (checkbox) ─────────────────────────────────────────────────
  /**
   * Build a labeled toggle (checkbox styled as a pill-switch).
   * @param {Object} opts
   *   label, value {boolean}, advanced, onChange {function(checked:boolean)}
   * @returns {HTMLDivElement}
   */
  function makeToggle(opts) {
    const { label, value = false, advanced = false, onChange } = opts;

    const row = document.createElement("div");
    row.className = "ctrl-row" + (advanced ? " adv-only" : "");

    const labelEl = document.createElement("label");
    labelEl.className = "ctrl-toggle";

    const checkbox = document.createElement("input");
    checkbox.type    = "checkbox";
    checkbox.checked = !!value;

    checkbox.addEventListener("change", (e) => {
      if (onChange) onChange(e.target.checked);
    });

    const textEl = document.createElement("span");
    textEl.className = "ctrl-toggle-label";
    textEl.textContent = label;

    labelEl.appendChild(checkbox);
    labelEl.appendChild(textEl);
    row.appendChild(labelEl);

    row._setValue = (v) => { checkbox.checked = !!v; };

    return row;
  }

  // ── Select ────────────────────────────────────────────────────────────
  /**
   * Build a labeled select row.
   * @param {Object} opts
   *   label, options [{value, label}], value, advanced, onChange
   * @returns {HTMLDivElement}
   */
  function makeSelect(opts) {
    const { label, options = [], value, advanced = false, onChange } = opts;

    const row = document.createElement("div");
    row.className = "ctrl-row" + (advanced ? " adv-only" : "");

    if (label) {
      const labelEl = document.createElement("div");
      labelEl.className = "ctrl-label";
      const nameEl = document.createElement("span");
      nameEl.className = "ctrl-name";
      nameEl.textContent = label;
      labelEl.appendChild(nameEl);
      row.appendChild(labelEl);
    }

    const sel = document.createElement("select");
    options.forEach((opt) => {
      const el = document.createElement("option");
      el.value       = opt.value;
      el.textContent = opt.label || opt.value;
      if (opt.value === value) el.selected = true;
      sel.appendChild(el);
    });

    sel.addEventListener("change", (e) => {
      if (onChange) onChange(e.target.value);
    });

    row.appendChild(sel);
    row._setValue = (v) => { sel.value = v; };

    return row;
  }

  // ── Section heading ────────────────────────────────────────────────────
  function makeSectionHeading(text, advanced = false) {
    const el = document.createElement("div");
    el.className = "ctrl-sub-heading" + (advanced ? " adv-only" : "");
    el.textContent = text;
    return el;
  }

  // ── Instance List Editor ───────────────────────────────────────────────
  /**
   * Build a generic instance-list editor (add/remove/edit) for
   * pathology or artifact instances.
   *
   * @param {Object} opts
   *   getInstances  {function(): Array}       – read current instances from state
   *   setInstances  {function(Array): void}   – write updated instances to state
   *   typeOptions   {Array<{value,label}>}     – select options for type picker
   *   paramSchema   {Array<{key,label,min,max,step,decimals,default}>} – sliders
   *   makeDefault   {function(type): Object}  – build default instance for type
   *   cardClass     {string}                  – extra CSS class on card (pathology-card|artifact-card)
   *   titleColorClass {string}                – CSS class for title text color
   *
   * @returns {{ el: HTMLElement, refresh: function }}
   */
  function makeInstanceList(opts) {
    const {
      getInstances,
      setInstances,
      typeOptions,
      paramSchema,
      makeDefault,
      cardClass      = "",
      titleColorClass = "",
    } = opts;

    const wrapper = document.createElement("div");

    // ── Toolbar: type picker + add button ────────────────────────────────
    const toolbar = document.createElement("div");
    toolbar.className = "instance-list-toolbar";

    const typeSelect = document.createElement("select");
    typeOptions.forEach((opt) => {
      const el = document.createElement("option");
      el.value       = opt.value;
      el.textContent = opt.label;
      typeSelect.appendChild(el);
    });
    toolbar.appendChild(typeSelect);

    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "btn-add-instance";
    addBtn.textContent = "+ Add";
    addBtn.addEventListener("click", () => {
      const type    = typeSelect.value;
      const current = getInstances();
      setInstances([...current, makeDefault(type)]);
      render();
    });
    toolbar.appendChild(addBtn);

    wrapper.appendChild(toolbar);

    // ── Item list ────────────────────────────────────────────────────────
    const listEl = document.createElement("div");
    listEl.className = "instance-list-items";
    wrapper.appendChild(listEl);

    // ── Render all cards ─────────────────────────────────────────────────
    function render() {
      listEl.innerHTML = "";
      const instances = getInstances();

      if (instances.length === 0) {
        const empty = document.createElement("div");
        empty.className = "instance-empty";
        empty.textContent = "No instances added.";
        listEl.appendChild(empty);
        return;
      }

      instances.forEach((inst, idx) => {
        const card = buildCard(inst, idx, instances);
        listEl.appendChild(card);
      });
    }

    function buildCard(inst, idx, allInstances) {
      const typLabel = (typeOptions.find(o => o.value === inst.type) || { label: inst.type }).label;

      const card = document.createElement("div");
      card.className = "instance-card" + (cardClass ? " " + cardClass : "");

      // ── Card header ───────────────────────────────────────────────────
      const header = document.createElement("div");
      header.className = "instance-card-header";

      const titleEl = document.createElement("span");
      titleEl.className = "instance-title" + (titleColorClass ? " " + titleColorClass : "");
      titleEl.textContent = `${idx + 1}. ${typLabel}`;
      header.appendChild(titleEl);

      const actions = document.createElement("div");
      actions.className = "instance-card-actions";

      // Collapse button
      const collapseBtn = document.createElement("button");
      collapseBtn.type      = "button";
      collapseBtn.className = "btn-collapse-instance";
      collapseBtn.innerHTML = '<svg class="icon" style="width:12px;height:12px;transition:transform 120ms ease;"><use href="assets/icons.svg#chevron-down"></use></svg>';
      collapseBtn.title     = "Collapse / expand";
      collapseBtn.addEventListener("click", () => {
        card.classList.toggle("collapsed");
        const svg = collapseBtn.querySelector("svg");
        if (svg) {
          svg.style.transform = card.classList.contains("collapsed") ? "rotate(-90deg)" : "none";
        }
      });
      actions.appendChild(collapseBtn);

      // Remove button
      const removeBtn = document.createElement("button");
      removeBtn.type      = "button";
      removeBtn.className = "btn-remove";
      removeBtn.innerHTML = "&times;";
      removeBtn.title     = "Remove instance";
      removeBtn.addEventListener("click", () => {
        const current = getInstances();
        setInstances(current.filter((_, i) => i !== idx));
        render();
      });
      actions.appendChild(removeBtn);

      header.appendChild(actions);
      card.appendChild(header);

      // ── Card body (parameter sliders) ─────────────────────────────────
      const body = document.createElement("div");
      body.className = "instance-card-body";

      paramSchema.forEach((param) => {
        const currentVal = inst[param.key] !== undefined ? inst[param.key] : param.default;

        const instRow = document.createElement("div");
        instRow.className = "inst-row";

        const labelRow = document.createElement("div");
        labelRow.className = "inst-label";

        const nameSpan = document.createElement("span");
        nameSpan.className = "inst-name";
        nameSpan.textContent = param.label;
        labelRow.appendChild(nameSpan);

        const valSpan = document.createElement("span");
        valSpan.className = "inst-val";
        valSpan.textContent = Number(currentVal).toFixed(param.decimals);
        labelRow.appendChild(valSpan);

        instRow.appendChild(labelRow);

        const slider = document.createElement("input");
        slider.type  = "range";
        slider.min   = param.min;
        slider.max   = param.max;
        slider.step  = param.step;
        slider.value = currentVal;

        slider.addEventListener("input", (e) => {
          const v = parseFloat(e.target.value);
          valSpan.textContent = v.toFixed(param.decimals);
          // Update the instance value in state
          const list = getInstances();
          if (list[idx]) {
            const updated = [...list];
            updated[idx] = {
              ...updated[idx],
              [param.key]: param.decimals === 0 ? Math.round(v) : v,
            };
            setInstances(updated);
          }
        });

        instRow.appendChild(slider);
        body.appendChild(instRow);
      });

      card.appendChild(body);
      return card;
    }

    // Initial render
    render();

    return { el: wrapper, refresh: render };
  }

  // ── Panel collapse / expand (delegated) ─────────────────────────────────
  function initPanelCollapse() {
    document.querySelectorAll(".panel-header").forEach((header) => {
      const panel = header.closest(".panel");
      if (!panel) return;

      // Sync initial aria state
      const isCollapsed = panel.classList.contains("collapsed");
      header.setAttribute("aria-expanded", (!isCollapsed).toString());

      header.addEventListener("click", () => {
        panel.classList.toggle("collapsed");
        const expanded = !panel.classList.contains("collapsed");
        header.setAttribute("aria-expanded", expanded.toString());
      });

      header.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          header.click();
        }
      });
    });
  }

  // ── Basic / Advanced mode toggle ─────────────────────────────────────────
  function initModeToggle() {
    const basicBtn    = document.getElementById("mode-basic");
    const advancedBtn = document.getElementById("mode-advanced");
    if (!basicBtn || !advancedBtn) return;

    function setMode(mode) {
      if (mode === "advanced") {
        document.body.classList.add("mode-advanced");
        advancedBtn.classList.add("active");
        basicBtn.classList.remove("active");
      } else {
        document.body.classList.remove("mode-advanced");
        basicBtn.classList.add("active");
        advancedBtn.classList.remove("active");
      }
    }

    basicBtn.addEventListener("click",    () => setMode("basic"));
    advancedBtn.addEventListener("click", () => setMode("advanced"));
  }

  // ── Public init ──────────────────────────────────────────────────────────
  function init() {
    initPanelCollapse();
    initModeToggle();

    // Delegate Generator init
    if (typeof Generator !== "undefined" && Generator.init) {
      Generator.init();
    }
  }

  return {
    init,
    makeSlider,
    makeNumericInput,
    makeToggle,
    makeSelect,
    makeInstanceList,
    makeSectionHeading,
  };
})();
