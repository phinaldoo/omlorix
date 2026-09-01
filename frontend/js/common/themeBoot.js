(function () {
    let savedMode = 'system';
    let savedTheme = 'mono';

    try {
        savedMode = localStorage.getItem('mode') || 'system';
        savedTheme = localStorage.getItem('theme') || 'mono';
    } catch (_error) {
        // Ignore storage access errors.
    }

    function applyMode(mode) {
        let finalMode = mode;
        if (mode === 'system') {
            finalMode = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
        }
        document.documentElement.setAttribute('data-mode', finalMode);
        document.documentElement.setAttribute('data-theme', savedTheme);
    }

    applyMode(savedMode);

    if (savedMode === 'system') {
        const mq = window.matchMedia('(prefers-color-scheme: dark)');
        mq.addEventListener('change', function () {
            applyMode('system');
        });
    }
})();
