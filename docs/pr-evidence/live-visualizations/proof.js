(async () => {
    const dictionary = await (await fetch('/i18n/en/index.json')).json();
    window.getTranslation = (key, fallback) => dictionary[key] || fallback;
    const payload = await (await fetch('/__proof__/payload')).json();
    window.proofPayload = payload;
    const fixture = location.search.includes('verify') ? installVisualizationProbe(payload.html) : payload.html;
    window.proofMount = (html = fixture, options = {}) => window.OmlorixVisualizer.mount(
        document.getElementById('visual'), html, { ...payload.visualization, ...options },
    );
    await window.proofMount();
    document.getElementById('theme').addEventListener('click', () => {
        document.documentElement.dataset.mode = document.documentElement.dataset.mode === 'dark' ? 'light' : 'dark';
    });
})();
