// Test-only DOM probe appended to the examples when ?verify is present.
// It runs inside the same opaque production sandbox as authored content.
window.installExampleProbe = source => source + '<script>(' + function () {
    const instance = Math.random();
    function hash(values) {
        let result = 2166136261;
        for (const value of values) result = Math.imul(result ^ value, 16777619);
        return result >>> 0;
    }
    addEventListener('message', event => {
        const request = event.data?.exampleProbe;
        if (!request) return;
        const element = request.selector ? document.querySelector(request.selector) : null;
        if (element) {
            if (request.value !== undefined) element.value = request.value;
            if (request.checked !== undefined) element.checked = request.checked;
            if (request.event === 'click') {
                if (typeof element.click === 'function') element.click();
                else element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            }
            else if (request.event === 'pointermove' || request.event === 'pointerdown' || request.event === 'pointerup') {
                const box = element.getBoundingClientRect();
                element.dispatchEvent(new PointerEvent(request.event, { bubbles: true, clientX: box.x + box.width * (request.x ?? .5), clientY: box.y + box.height * (request.y ?? .5), pointerId: 1, pointerType: 'mouse', buttons: 0 }));
            } else if (request.event === 'keydown') element.dispatchEvent(new KeyboardEvent('keydown', { key: request.key, bubbles: true, cancelable: true }));
            else if (request.event) element.dispatchEvent(new Event(request.event, { bubbles: true }));
        }
        const canvas = document.querySelector('canvas');
        const pixels = canvas?.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        const outputs = Object.fromEntries([...document.querySelectorAll('output, #week-detail, #selected-country, #selected-unit, #color-legend')].map(node => [node.id, node.textContent.trim()]));
        const controls = Object.fromEntries([...document.querySelectorAll('input, select')].map(node => [node.id, node.type === 'checkbox' ? node.checked : node.value]));
        const state = {
            instance, outputs, controls,
            widgetState: window.omlorix?.visualization.widgetState,
            designPanelOpen: Boolean(document.querySelector('.omlorix-design-panel') && !document.querySelector('.omlorix-design-panel').hidden),
            designGroups: [...document.querySelectorAll('[data-design-group]')].filter(node => !node.hidden).map(node => node.dataset.designGroup),
            variants: [...document.querySelectorAll('[data-variant]')].map(node => ({ name: node.dataset.variant, hidden: node.hidden })),
            cards: Object.fromEntries([...document.querySelectorAll('.card[id]')].map(node => [node.id, { radius: node.style.borderRadius, accent: node.style.getPropertyValue('--mockup-accent'), hint: !node.querySelector('.hint')?.hidden, heading: node.querySelector('h3')?.style.fontSize }])),
            // Do not cause the production loader to include otherwise unused libraries.
            libraries: Object.fromEntries([['d', '3'], ['topo', 'json']].map(parts => [parts.join(''), typeof window[parts.join('')]])),
            canvasHash: pixels ? hash(pixels) : null,
            geometryHash: hash(new TextEncoder().encode([...document.querySelectorAll('svg path')].map(node => node.getAttribute('d') + node.getAttribute('fill')).join(''))),
            paths: document.querySelectorAll('svg path').length,
            series: document.querySelectorAll('.series').length,
            heatCells: document.querySelectorAll('[data-cell]').length,
            canvas: canvas ? { width: canvas.width, height: canvas.height } : null,
            width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth,
            background: getComputedStyle(document.documentElement).getPropertyValue('--background').trim(),
            remoteResources: performance.getEntriesByType('resource').filter(entry => /^https?:/.test(entry.name)).map(entry => entry.name),
        };
        parent.postMessage({ type: 'omlorix:visualization-status', exampleReply: request.id, state }, '*');
    });
}.toString() + ')();</script>';

window.inspectExample = (request = {}) => new Promise((resolve, reject) => {
    const id = crypto.randomUUID();
    const timer = setTimeout(() => { removeEventListener('message', receive); reject(new Error('Example probe timeout')); }, 4000);
    function receive(event) {
        if (event.data?.exampleReply !== id) return;
        clearTimeout(timer); removeEventListener('message', receive); resolve(event.data.state);
    }
    addEventListener('message', receive);
    document.querySelector('.visualizer-preview-frame').contentWindow.postMessage({ type: 'omlorix:visualization-response', exampleProbe: { ...request, id } }, location.origin);
});

window.verifyExample = async () => {
    const results = [];
    const check = (condition, name) => { if (!condition) throw new Error(name); results.push(name); };
    const tick = () => new Promise(resolve => setTimeout(resolve, 100));
    const ready = async () => {
        const deadline = Date.now() + 10000;
        while (document.querySelector('.visualizer-preview-surface')?.dataset.state !== 'ready') {
            if (document.querySelector('.visualizer-preview-surface')?.dataset.state === 'error') throw new Error('Runtime error');
            if (Date.now() > deadline) throw new Error('Example readiness timeout');
            await tick();
        }
    };
    await window.proofMount(undefined, { temporary: true, savedState: {} });
    await ready();
    const example = new URLSearchParams(location.search).get('example');
    const initial = await inspectExample();
    let state;
    if (example === 'charts') {
        check(initial.series === 3 && initial.heatCells === 168 && initial.outputs['week-total'] === '2,500', 'stacked series and all 168 hourly cells render');
        state = await inspectExample({ selector: '#counts', event: 'click' });
        check(state.geometryHash !== initial.geometryHash && state.outputs['week-detail'].includes('1,650'), 'share/count switch redraws scale and values');
        state = await inspectExample({ selector: '#week', event: 'input', value: '4' });
        check(state.outputs['week-total'] === '1,200' && state.outputs['week-detail'].includes('Nova 510'), 'week slider updates computed totals');
        await inspectExample({ selector: '#day', event: 'change', value: '0' });
        state = await inspectExample({ selector: '#hour', event: 'change', value: '6' });
        check(state.outputs['hour-detail'] === 'Mon 06:00 UTC · 30 conversations', 'keyboard-equivalent heatmap selectors update exact value');
        state = await inspectExample({ selector: '[data-cell="3-15"]', event: 'pointermove' });
        check(state.outputs['hour-detail'] === 'Thu 15:00 UTC · 242 conversations', 'heatmap pointer inspection updates the same readout');
    } else if (example === 'map') {
        check(initial.paths === 177 && initial.libraries.topojson === 'object', 'bundled TopoJSON and D3 render 177 country geometries');
        state = await inspectExample({ selector: '#country', event: 'change', value: '276' });
        check(state.outputs['selected-value'] === '2,400' && parseFloat(state.outputs['zoom-level']) > 1, 'country selector fits Germany and reports its data');
        const activity = state.geometryHash;
        state = await inspectExample({ selector: '#measure', event: 'change', value: 'latency' });
        check(state.outputs['selected-value'] === '180' && state.geometryHash !== activity, 'metric switch changes values and choropleth colors');
        state = await inspectExample({ selector: '[data-country="076"]', event: 'keydown', key: 'Enter' });
        check(state.outputs['selected-country'] === 'BRAZIL' && state.outputs['selected-value'] === '320', 'keyboard country activation selects Brazil');
        await inspectExample({ selector: '#world', event: 'click' });
        state = await inspectExample({ selector: '#zoom-in', event: 'click' });
        check(state.outputs['zoom-level'] === '1.5×', 'zoom-in changes map transform');
        state = await inspectExample({ selector: '#zoom-out', event: 'click' });
        check(state.outputs['zoom-level'] === '1.0×', 'zoom-out restores scale');
        state = await inspectExample({ selector: '[data-country="276"]', event: 'click' });
        check(state.controls.country === '276', 'pointer country selection updates controls');
        state = await inspectExample({ selector: '#zoom-in', event: 'click' });
    } else if (example === 'treemap') {
        check(initial.outputs['file-count'] === '1,679' && initial.outputs['line-count'] === '702,615' && initial.canvas.width > 0, 'canvas treemap renders the measured repository snapshot');
        state = await inspectExample({ selector: '#tests', checked: false, event: 'change' });
        check(state.outputs['test-share'] === '0%' && Number(state.outputs['file-count'].replaceAll(',', '')) < 1679 && state.canvasHash !== initial.canvasHash, 'test filter recomputes layout and totals');
        state = await inspectExample({ selector: '#folder', value: 'frontend/js/chat/rendering', event: 'change' });
        check(Number(state.outputs['file-count'].replaceAll(',', '')) > 0 && state.controls.folder === 'frontend/js/chat/rendering', 'folder selector drills into rendering source');
        const churn = state.canvasHash;
        state = await inspectExample({ selector: '#color-mode', value: 'area', event: 'change' });
        check(state.canvasHash !== churn, 'code-area colors redraw canvas');
        state = await inspectExample({ selector: 'canvas', event: 'pointermove', x: .5, y: .5 });
        check(state.outputs['file-detail'].includes('lines') && state.outputs['file-detail'].includes('touches'), 'canvas hit-testing exposes a file and its measurements');
        state = await inspectExample({ selector: '#back', event: 'click' });
        check(state.controls.folder === 'frontend/js/chat', 'zoom-out goes to the parent folder');
        state = await inspectExample({ selector: '.file-row', event: 'click' });
        check(state.outputs['file-detail'].includes('touches'), 'ranked file button selects and reveals a file');
    } else throw new Error('Unknown example');
    state = await inspectExample();
    const identity = state.instance, controls = JSON.stringify(state.controls), mapZoom = state.outputs['zoom-level'];
    const button = action => document.querySelector(`[data-preview-action="${action}"]`);
    button('source').click();
    check(!document.querySelector('.visualizer-preview-source').hidden, 'source view exposes authored code');
    button('source').click(); button('expand').click(); await tick();
    state = await inspectExample();
    check(state.instance === identity && JSON.stringify(state.controls) === controls && !state.overflow, 'expanded example preserves live state and fits width');
    if (example === 'map') check(state.outputs['zoom-level'] === mapZoom, 'map zoom survives expansion and responsive resizing');
    document.querySelector('[data-visualizer-close]').click(); await tick();
    check(!document.querySelector('header').inert, 'closing expansion restores page interaction');
    const beforeTheme = await inspectExample();
    document.getElementById('theme').click(); await tick();
    state = await inspectExample();
    check(state.background !== beforeTheme.background && state.instance === identity, 'theme updates without restarting');
    if (example === 'treemap') check(state.canvasHash !== beforeTheme.canvasHash, 'canvas redraws for the new theme');
    check(!state.overflow && !state.remoteResources.length, 'no horizontal overflow or remote widget resources');
    check(document.querySelector('.visualizer-preview-surface').dataset.state === 'ready', 'all interactions complete without a runtime error');
    button('reset').click(); await ready();
    state = await inspectExample();
    check(state.instance !== identity && JSON.stringify(state.controls) === JSON.stringify(initial.controls), 'reset restores initial authored values');
    return { example, viewport: innerWidth, checks: results };
};
