// Browser-only checks. Load /?verify and call await verifyVisualizations() in
// the console. The probe is appended to the test fixture, never to app code.
window.installVisualizationProbe = (source) => source + '<script>(' + function probe() {
    const instance = Math.random();
    addEventListener('message', async (event) => {
        const command = event.data?.proofCommand;
        if (!command) return;
        const slider = document.getElementById('workers');
        if (command === 'input') {
            slider.value = event.data.value;
            slider.dispatchEvent(new Event('input', { bubbles: true }));
        }
        let isolated = false;
        try { void parent.document.body; } catch (_) { isolated = true; }
        parent.postMessage({ type: 'omlorix:visualization-status', proofReply: event.data.id, result: {
            value: slider?.value, seconds: document.getElementById('time')?.textContent, instance, isolated,
            width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth,
            background: getComputedStyle(document.documentElement).getPropertyValue('--background').trim(),
        } }, '*');
    });
}.toString() + ')();</script>';

window.probeVisualization = (proofCommand = 'inspect', value) => new Promise((resolve, reject) => {
    const id = crypto.randomUUID();
    const timer = setTimeout(() => { window.removeEventListener('message', receive); reject(new Error('Probe timeout')); }, 2000);
    function receive(event) {
        if (event.data?.proofReply !== id) return;
        clearTimeout(timer);
        window.removeEventListener('message', receive);
        resolve(event.data.result);
    }
    window.addEventListener('message', receive);
    document.querySelector('.visualizer-preview-frame').contentWindow.postMessage({ type: 'omlorix:visualization-response', proofCommand, value, id }, location.origin);
});

window.verifyVisualizations = async () => {
    const results = [];
    const check = (condition, name) => { if (!condition) throw new Error(name); results.push(name); };
    const tick = () => new Promise(resolve => setTimeout(resolve, 100));
    const ready = async () => {
        const deadline = Date.now() + 5000;
        while (document.querySelector('.visualizer-preview-surface')?.dataset.state !== 'ready') {
            if (Date.now() > deadline) throw new Error('Visual did not become ready');
            await tick();
        }
    };
    await ready();
    const button = (action) => document.querySelector(`[data-preview-action="${action}"]`);
    let state = await probeVisualization();
    check(state.isolated && state.value === '4', 'automatic local scripts and opaque origin');
    state = await probeVisualization('input', '8');
    check(state.value === '8' && state.seconds === '18.9', 'input updates chart and computed result');
    const instance = state.instance;
    button('source').click();
    check(!document.querySelector('.visualizer-preview-source').hidden, 'source view opens');
    button('source').click();
    button('expand').click();
    await tick();
    state = await probeVisualization();
    check(state.instance === instance && state.value === '8', 'source and expansion preserve the live document');
    check(document.querySelector('.is-expanded')?.getAttribute('aria-modal') === 'true' && document.querySelector('header').inert, 'expanded dialog isolates background');
    document.querySelector('[data-visualizer-close]').click();
    check(!document.querySelector('header').inert && document.activeElement === button('expand'), 'collapse restores focus and background');
    document.getElementById('theme').click();
    await tick();
    state = await probeVisualization();
    check(state.instance === instance && state.background === getComputedStyle(document.documentElement).getPropertyValue('--background').trim(), 'live theme synchronization');
    check(!state.overflow, 'no horizontal overflow');
    button('reset').click();
    await ready();
    state = await probeVisualization();
    check(state.instance !== instance && state.value === '4', 'reset restores initial values');
    return results;
};

window.verifyVisualizationIsolation = async () => {
    const results = [];
    function receive(event) {
        if (event.data?.proofSecurity) results.push(event.data.proofSecurity);
    }
    window.addEventListener('message', receive);
    await proofMount(`<section id="isolation-test">Sandbox check<script>
      (async () => {
        let denied = false;
        try { await fetch('/__proof__/blocked-network'); } catch (_) { denied = true; }
        let opaque = false;
        try { void parent.document.body; } catch (_) { opaque = true; }
        parent.postMessage({type:'omlorix:visualization-status',proofSecurity:{denied,opaque}}, '*');
        setTimeout(() => { location.href = '/__proof__/blocked-navigation'; }, 100);
      })();
    </script></section>`, { title: 'Isolation check' });
    await new Promise(resolve => setTimeout(resolve, 700));
    window.removeEventListener('message', receive);
    const hits = await (await fetch('/__proof__/security-hits')).json();
    if (!results[0]?.denied || !results[0]?.opaque || hits.length) throw new Error('Isolation check failed');
    return { ...results[0], networkRequests: hits.length };
};

window.verifyVisualizationExport = async () => {
    const createUrl = URL.createObjectURL;
    const click = HTMLAnchorElement.prototype.click;
    let captured;
    try {
        URL.createObjectURL = blob => { captured = blob.text(); return createUrl.call(URL, blob); };
        // Exercise the real toolbar exporter without writing to Downloads.
        HTMLAnchorElement.prototype.click = () => {};
        document.querySelector('[data-preview-action="download"]').click();
    } finally {
        URL.createObjectURL = createUrl;
        HTMLAnchorElement.prototype.click = click;
    }
    const html = await captured;
    if (!html?.startsWith('<!doctype html>') || !html.includes(proofPayload.visualization.summary)) throw new Error('Missing standalone document or text alternative');
    await fetch('/__proof__/export', { method: 'POST', body: html });
    return { bytes: new Blob([html]).size, url: '/__proof__/export' };
};
