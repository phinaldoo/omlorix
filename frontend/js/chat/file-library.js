/** A flat library whose categories follow the accessible files, never folders. */
(function () {
    'use strict';
    const categories = {
        all: ['files_category_all', 'All files', 'file'],
        canvas: ['files_category_canvas', 'Canvas', 'file'],
        pdf: ['files_category_pdf', 'PDFs', 'file'],
        images: ['files_category_images', 'Images', 'image'],
        slides: ['files_category_slides', 'Slides', 'presentation'],
        spreadsheets: ['files_category_spreadsheets', 'Spreadsheets', 'file'],
        documents: ['files_category_documents', 'Documents', 'file'],
        audio: ['files_category_audio', 'Audio', 'file'],
        video: ['files_category_video', 'Video', 'file'],
        archives: ['files_category_archives', 'Archives', 'file'],
        other: ['files_category_other', 'Other files', 'file'],
    };
    const t = (key, fallback) => window.getTranslation?.(key, fallback) || fallback;
    let active = 'all';
    let counts = { all: 0, categories: {} };

    function render() {
        const list = document.getElementById('filesCategoryList');
        if (!list) return;
        const focusCategory = document.activeElement?.dataset?.category;
        list.replaceChildren();
        for (const [id, [key, fallback, icon]] of Object.entries(categories)) {
            const count = id === 'all' ? counts.all : Number(counts.categories?.[id] || 0);
            if (id !== 'all' && !count && active !== id) continue;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'files-sidebar-item' + (active === id ? ' active' : '');
            button.dataset.category = id;
            button.setAttribute('aria-pressed', String(active === id));
            const glyph = document.createElement('span');
            glyph.className = 'files-sidebar-item-icon';
            glyph.setAttribute('aria-hidden', 'true');
            glyph.innerHTML = window.Icons?.[icon] || window.Icons?.file || '';
            const label = document.createElement('span');
            label.className = 'files-sidebar-item-name';
            label.textContent = t(key, fallback);
            const badge = document.createElement('span');
            badge.className = 'files-sidebar-item-count';
            badge.textContent = String(count || 0);
            button.append(glyph, label, badge);
            button.addEventListener('click', () => {
                active = id;
                render();
                window.FilesManager?.refresh();
            });
            list.append(button);
            if (focusCategory === id) button.focus({ preventScroll: true });
        }
        const title = document.getElementById('filesMainHeaderTitle');
        if (title) {
            const [key, fallback] = categories[active];
            title.removeAttribute('data-i18n');
            title.textContent = t(key, fallback);
        }
    }

    async function createCanvas(event) {
        const button = event.currentTarget;
        button.disabled = true;
        try {
            const title = t('canvas_new_title', 'Untitled Canvas');
            const response = await window.authedFetch('/api/v1/files/canvas', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: title + '.md', content: '# ' + title + '\n', content_type: 'markdown' }),
            });
            if (!response.ok) throw new Error('Canvas creation failed');
            const file = await response.json();
            active = 'canvas';
            await window.FilesManager?.refresh();
            await window.canvasMarkdownWidget?.openPreviewForFile(file.file_id, file.file_name, 'markdown');
        } catch (_) {
            window.notifyError?.(t('canvas_create_failed', 'Could not create Canvas. Please try again.'));
        } finally {
            button.disabled = false;
        }
    }

    window.FileLibraryManager = {
        init: render,
        getActiveCategory: () => active,
        updateAfterFilesLoaded(nextCounts) { counts = nextCounts; render(); },
    };
    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('filesCreateCanvas')?.addEventListener('click', createCanvas);
        render();
    });
    document.addEventListener('languageChanged', render);
}());
