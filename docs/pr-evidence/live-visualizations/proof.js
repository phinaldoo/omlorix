(async () => {
    const dictionary = await (await fetch('/i18n/en/index.json')).json();
    window.getTranslation = (key, fallback) => dictionary[key] || fallback;
    const params = new URLSearchParams(location.search);
    const example = params.get('example') || 'parallelism';
    const payload = await (await fetch('/__proof__/payload?example=' + encodeURIComponent(example))).json();
    window.proofPayload = payload;
    window.showWarningConfirm = async options => { window.proofConfirmation = options; return window.proofApprove === true; };
    window.sendMessage = async prompt => { window.proofSentPrompt = prompt; };
    for (const [key, selector] of Object.entries({ question: '.proof-question', intro: '.proof-intro', after: '.proof-after' })) {
        if (payload.proof[key]) document.querySelector(selector).textContent = payload.proof[key];
    }
    const probe = example === 'parallelism' ? installVisualizationProbe : installExampleProbe;
    const fixture = params.has('verify') ? probe(payload.html) : payload.html;
    window.proofMount = (html = fixture, options = {}) => window.OmlorixVisualizer.mount(
        document.getElementById('visual'), html, { ...payload.visualization, messageId: 'proof-' + example, toolCallId: example, ...options },
    );
    await window.proofMount();
    document.getElementById('theme').addEventListener('click', () => {
        document.documentElement.dataset.mode = document.documentElement.dataset.mode === 'dark' ? 'light' : 'dark';
    });
})();
