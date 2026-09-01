(() => {
    const unhide = () => {
        try {
            document.body.classList.remove('js-hidden');
        } catch (_error) {
            // Ignore transient DOM state errors.
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', unhide, { once: true });
    } else {
        unhide();
    }

    setTimeout(unhide, 2000);
})();
