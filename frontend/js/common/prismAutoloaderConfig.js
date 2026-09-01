document.addEventListener('DOMContentLoaded', () => {
    if (window.Prism && Prism.plugins && Prism.plugins.autoloader) {
        Prism.plugins.autoloader.languages_path = '/js/vendor/prism/components/';
        Prism.plugins.autoloader.use_minified = true;
    }
});
