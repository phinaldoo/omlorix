window.__serverSetupBooted = false;
window.setTimeout(() => {
    if (!window.__serverSetupBooted && window.location.pathname !== '/error') {
        window.location.replace('/error');
    }
}, 4000);
