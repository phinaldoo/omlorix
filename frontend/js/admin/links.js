(function () {
    const ADMIN_EXTERNAL_LINKS = Object.freeze({
        githubRepo: 'https://github.com/phinaldoo/omlorix',
        help: 'https://github.com/phinaldoo/omlorix',
        // Keep every About-page destination useful while the project website is
        // unavailable. These URLs point directly to the matching repository page.
        docs: 'https://github.com/phinaldoo/omlorix#readme',
        feedback: 'https://github.com/phinaldoo/omlorix/issues',
        releases: 'https://github.com/phinaldoo/omlorix/releases',
        contact: 'https://github.com/phinaldoo/omlorix/issues',
        support: 'https://github.com/phinaldoo/omlorix/issues'
    });

    function applyAdminExternalLinks(root = document) {
        root.querySelectorAll('[data-admin-link]').forEach((linkEl) => {
            const linkKey = linkEl.dataset.adminLink;
            const href = ADMIN_EXTERNAL_LINKS[linkKey];

            if (!href) {
                console.warn(`Missing admin external link for key: ${linkKey}`);
                linkEl.removeAttribute('href');
                linkEl.removeAttribute('target');
                linkEl.removeAttribute('rel');
                return;
            }

            linkEl.href = href;
            linkEl.target = '_blank';
            linkEl.rel = 'noopener noreferrer';
        });
    }

    window.ADMIN_EXTERNAL_LINKS = ADMIN_EXTERNAL_LINKS;
    window.applyAdminExternalLinks = applyAdminExternalLinks;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => applyAdminExternalLinks(), { once: true });
    } else {
        applyAdminExternalLinks();
    }
})();
