(function () {
    'use strict';

    // Canvas and other transcript previews share the same card and action.
    // Callers own their metadata, labels, and preview lifecycle.
    function create({ icon = '', title = '', subtitle = '', openLabel }) {
        const card = document.createElement('div');
        card.className = 'canvas-markdown-result-widget';
        card.innerHTML =
            '<div class="canvas-markdown-result-header">' +
            '  <div class="canvas-markdown-result-icon" aria-hidden="true"></div>' +
            '  <div class="canvas-markdown-result-meta">' +
            '    <div class="canvas-markdown-result-title"></div>' +
            '    <div class="canvas-markdown-result-sub"></div>' +
            '  </div>' +
            '</div>' +
            '<button class="canvas-markdown-result-open-btn" type="button">' +
            '  <span aria-hidden="true">' + Icons.eye + '</span>' +
            '  <span class="canvas-markdown-result-open-label"></span>' +
            '</button>';
        card.querySelector('.canvas-markdown-result-icon').innerHTML = icon;
        card.querySelector('.canvas-markdown-result-title').textContent = title;
        card.querySelector('.canvas-markdown-result-sub').textContent = subtitle;
        card.querySelector('.canvas-markdown-result-open-label').textContent = openLabel;
        return card;
    }

    window.ChatResultCard = { create };
})();
