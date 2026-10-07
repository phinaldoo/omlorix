/* Injected runtime-owned controls. No external package or application DOM access. */
window.createOmlorixVisualizationControls = function ({ request, initialState = {}, labels, standalone, followup, reportHeight }) {
    const clone = (value) => JSON.parse(JSON.stringify(value));
    let widgetState = initialState.widgetState || null;
    let design = clone(initialState.design || {});
    const groups = new Map();
    const carousels = new Map();
    let panel, fields, originalButton, previewOriginal = false, counter = 0;
    let pendingDesign = Promise.resolve();
    const text = (key) => labels[key] || key;
    const make = (tag, className, value) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (value) node.textContent = value;
        return node;
    };
    const button = (label, action) => {
        const node = make('button', 'btn btn-ghost', label);
        node.type = 'button';
        node.addEventListener('click', action);
        return node;
    };
    function notice(error) {
        if (!panel) return;
        const status = panel.querySelector('[role="status"]');
        status.textContent = error ? text('saveError') : '';
    }
    function saveDesign() {
        if (standalone) return Promise.resolve();
        pendingDesign = request('save-design', { design: clone(design) });
        pendingDesign.then(() => notice(), () => notice(true));
        return pendingDesign;
    }
    function emitState() {
        window.dispatchEvent(new CustomEvent('omlorix:statechange', { detail: { widgetState: clone(widgetState) } }));
    }
    function setWidgetState(value) {
        try {
            const next = { modelContent: value?.modelContent ?? null, privateContent: value?.privateContent ?? null };
            const serialized = JSON.stringify(next);
            if (new TextEncoder().encode(serialized).length > 16384) throw new Error(text('invalid'));
            if (serialized === JSON.stringify(widgetState)) return Promise.resolve({ unchanged: true });
            widgetState = JSON.parse(serialized);
            emitState();
            return standalone ? Promise.resolve({ persisted: false }) : request('save-state', { widgetState });
        } catch (error) { return Promise.reject(error); }
    }
    function visible(group) {
        return group.container.isConnected && !group.container.closest('[hidden]');
    }
    function refreshGroups() {
        for (const group of groups.values()) {
            if (group.fieldset) group.fieldset.hidden = !visible(group);
        }
        reportHeight();
    }
    function restoreOriginal(enabled) {
        previewOriginal = enabled;
        originalButton?.setAttribute('aria-pressed', String(enabled));
        for (const group of groups.values()) {
            for (const control of group.controls) {
                control.object[control.property] = enabled ? control.original : control.value;
                control.input.disabled = enabled;
            }
            group.onChange();
        }
    }
    function ensurePanel() {
        if (panel || !document.body || !groups.size) return;
        panel = make('section', 'omlorix-design-panel');
        panel.hidden = true;
        panel.setAttribute('aria-label', text('design'));
        const header = make('div', 'viz-row');
        header.append(make('strong', '', text('design')), button(text('close'), () => setDesignOpen(false)));
        fields = make('div', 'omlorix-design-groups');
        const actions = make('div', 'viz-controls');
        originalButton = button(text('original'), () => restoreOriginal(!previewOriginal));
        originalButton.setAttribute('aria-pressed', 'false');
        actions.append(originalButton, button(text('resetDesign'), () => {
            if (previewOriginal) restoreOriginal(false);
            design.values = {};
            for (const group of groups.values()) {
                for (const control of group.controls) apply(control, control.original, false);
                group.onChange();
            }
            void saveDesign();
        }));
        if (followup && !standalone) actions.append(button(text('submit'), async () => {
            if (previewOriginal) restoreOriginal(false);
            const changes = [];
            for (const group of groups.values()) {
                for (const control of group.controls) {
                    if (control.value !== control.original) changes.push({ component: group.label, property: control.reference || control.property, value: control.value });
                }
            }
            try {
                await pendingDesign;
                await request('send-follow-up', { prompt: text('submitPrompt') + '\n' + JSON.stringify({ variants: design.variants || {}, changes }, null, 2), title: text('submit') });
            } catch (_) { notice(true); }
        }));
        const status = make('p', 'text-small text-muted');
        status.setAttribute('role', 'status');
        panel.append(header, fields, actions, status);
        document.body.append(panel);
        // Portable exports get the same controls, entirely local to the file.
        if (standalone) panel.before(button(text('design'), () => setDesignOpen(panel.hidden)));
        for (const group of groups.values()) renderGroup(group);
    }
    function setDesignOpen(open) {
        ensurePanel();
        if (!panel) return;
        panel.hidden = !open;
        if (!open && previewOriginal) restoreOriginal(false);
        if (open) panel.querySelector('button')?.focus({ preventScroll: true });
        refreshGroups();
        if (!standalone) request('design-open', { open }).catch(() => {});
    }
    function validValue(control, value) {
        if (control.type === 'slider') {
            if (typeof value !== 'number' || !Number.isFinite(value)) return control.original;
            return Math.min(control.max, Math.max(control.min, value));
        }
        if (control.type === 'color') return /^#[0-9a-f]{6}$/i.test(value) ? value : control.original;
        if (control.type === 'toggle') return typeof value === 'boolean' ? value : control.original;
        return control.options.some((option) => option.value === value) ? value : control.original;
    }
    function apply(control, value, persist = true) {
        control.value = validValue(control, value);
        control.object[control.property] = control.value;
        if (control.input) {
            if (control.type === 'toggle') control.input.checked = control.value;
            else control.input.value = control.value;
            control.output.textContent = control.type === 'slider' ? `${control.value}${control.unit}` : '';
        }
        if (persist) {
            design.values ||= {};
            design.values[control.group.id] ||= {};
            design.values[control.group.id][control.property] = control.value;
            control.group.onChange();
            void saveDesign();
        }
    }
    function renderControl(control, fieldset) {
        const row = make('label', 'omlorix-design-control');
        const name = make('span', '', control.label);
        const input = make(control.type === 'select' ? 'select' : 'input', control.type === 'slider' ? 'form-range' : 'form-control');
        input.dataset.designProperty = control.property;
        if (control.type === 'select') {
            for (const option of control.options) {
                const item = make('option', '', option.label);
                item.value = option.value;
                input.append(item);
            }
        } else {
            input.type = { slider: 'range', color: 'color', toggle: 'checkbox' }[control.type];
            if (control.type === 'slider') {
                input.min = control.min; input.max = control.max; input.step = control.step;
            }
        }
        control.input = input;
        control.output = make('output', 'tabular-nums text-small');
        row.append(name, input, control.output);
        fieldset.append(row);
        apply(control, control.value, false);
        input.addEventListener('input', () => apply(control, control.type === 'toggle' ? input.checked : control.type === 'slider' ? Number(input.value) : input.value));
    }
    function renderGroup(group) {
        if (!fields || group.fieldset) return;
        const fieldset = make('fieldset', 'omlorix-design-group');
        fieldset.dataset.designGroup = group.id;
        fieldset.append(make('legend', '', group.label));
        for (const control of group.controls) renderControl(control, fieldset);
        fields.append(fieldset);
        group.fieldset = fieldset;
        refreshGroups();
    }
    class Tweak {
        constructor({ container, onChange = () => {} } = {}) {
            if (!(container instanceof Element) || groups.size >= 24) throw new Error(text('invalid'));
            this.id = container.id || `component-${++counter}`;
            if (['__proto__', 'constructor', 'prototype'].includes(this.id) || groups.has(this.id)) throw new Error(text('invalid'));
            this.container = container;
            this.label = (container.getAttribute('aria-label') || this.id).slice(0, 160);
            this.onChange = onChange;
            this.controls = [];
            this.supported = true;
            groups.set(this.id, this);
            ensurePanel();
            renderGroup(this);
            if (!standalone) request('design-available', {}).catch(() => {});
        }
        add(object, property, options, type) {
            if (['__proto__', 'constructor', 'prototype'].includes(property) || !object || typeof property !== 'string' || this.controls.length >= 12 || this.controls.some((c) => c.property === property)) throw new Error(text('invalid'));
            const original = object[property];
            const control = { group: this, object, property, type, original, value: original,
                label: String(options.label || property).slice(0, 160), reference: /^[\w.\/:@$#-]{1,160}$/.test(options.reference || '') ? options.reference : '',
                min: Number(options.min ?? 0), max: Number(options.max ?? 100), step: Number(options.step ?? 1), unit: String(options.unit || '').slice(0, 16),
                options: (options.options || []).slice(0, 12).map((o) => typeof o === 'string' ? { label: o, value: o } : { label: String(o.label), value: String(o.value) }) };
            if (type === 'slider' && (![control.min, control.max, control.step].every(Number.isFinite) || control.min > control.max || control.step <= 0)) throw new Error(text('invalid'));
            this.controls.push(control);
            const saved = design.values?.[this.id]?.[property];
            apply(control, saved === undefined ? original : saved, false);
            if (this.fieldset) renderControl(control, this.fieldset);
            if (saved !== undefined) this.onChange();
            return this;
        }
        addSlider(object, property, options = {}) { return this.add(object, property, options, 'slider'); }
        addColorPicker(object, property, options = {}) { return this.add(object, property, options, 'color'); }
        addToggle(object, property, options = {}) { return this.add(object, property, options, 'toggle'); }
        addSelect(object, property, options = {}) { return this.add(object, property, options, 'select'); }
        dispose() { this.fieldset?.remove(); groups.delete(this.id); reportHeight(); }
    }
    window.Tweak = Tweak;
    function initialize() {
        ensurePanel();
        document.querySelectorAll('.viz-carousel').forEach((root, index) => {
            const variants = Array.from(root.children).filter((node) => node.dataset.variant);
            if (!variants.length || variants.length > 24) return;
            const id = root.id || `carousel-${index}`;
            if (carousels.has(id)) return;
            carousels.set(id, root);
            const nav = make('nav', 'omlorix-variant-nav');
            nav.setAttribute('aria-label', root.getAttribute('aria-label') || text('variants'));
            const select = make('select', 'form-select');
            select.setAttribute('aria-label', text('variants'));
            variants.forEach((variant) => {
                const option = make('option', '', variant.dataset.variant);
                option.value = variant.dataset.variant;
                select.append(option);
            });
            const count = make('span', 'text-small tabular-nums');
            count.setAttribute('aria-live', 'polite');
            let active = Math.max(0, variants.findIndex((variant) => variant.dataset.variant === design.variants?.[id]));
            const choose = (next, persist = true) => {
                active = (next + variants.length) % variants.length;
                variants.forEach((variant, i) => { variant.hidden = i !== active; });
                select.value = variants[active].dataset.variant;
                count.textContent = `${active + 1} / ${variants.length}`;
                if (persist) {
                    design.variants ||= {};
                    design.variants[id] = select.value;
                    void saveDesign();
                }
                refreshGroups();
            };
            select.addEventListener('change', () => choose(variants.findIndex((v) => v.dataset.variant === select.value)));
            nav.append(button(root.dataset.previousLabel || text('previous'), () => choose(active - 1)), select, count, button(root.dataset.nextLabel || text('next'), () => choose(active + 1)));
            root.after(nav);
            choose(active, false);
        });
        refreshGroups();
        const cleanup = new MutationObserver(() => {
            for (const group of groups.values()) if (!group.container.isConnected) group.dispose();
        });
        cleanup.observe(document.body, { childList: true, subtree: true });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, { once: true });
    else initialize();
    return { get widgetState() { return clone(widgetState); }, setWidgetState, setDesignOpen };
};
