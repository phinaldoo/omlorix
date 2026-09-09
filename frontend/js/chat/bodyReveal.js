(() => {
    const unhide = () => {
        document.body?.classList.remove('js-hidden');
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', unhide, { once: true });
    } else {
        unhide();
    }

    setTimeout(unhide, 2000);
})();
