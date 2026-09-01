const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeEventTarget {
    constructor() {
        this.listeners = {};
        this.style = {
            setProperty(name, value) {
                this[name] = String(value);
            },
        };
        this.children = [];
        this.attributes = {};
        this.className = '';
        this.innerHTML = '';
        this.textContent = '';
        this.hidden = false;
        this.disabled = false;
        const classes = new Set();
        this.classList = {
            add: (...names) => names.forEach((name) => classes.add(name)),
            remove: (...names) => names.forEach((name) => classes.delete(name)),
            contains: (name) => classes.has(name),
            toggle: (name, force) => {
                const shouldAdd = force === undefined ? !classes.has(name) : Boolean(force);
                if (shouldAdd) classes.add(name);
                else classes.delete(name);
                return shouldAdd;
            },
        };
    }

    addEventListener(eventName, handler) {
        if (!this.listeners[eventName]) {
            this.listeners[eventName] = [];
        }
        this.listeners[eventName].push(handler);
    }

    removeEventListener(eventName, handler) {
        const handlers = this.listeners[eventName];
        if (!handlers) return;
        this.listeners[eventName] = handlers.filter((registeredHandler) => registeredHandler !== handler);
    }

    dispatchEvent(event) {
        const handlers = this.listeners[event.type] || [];
        handlers.forEach((handler) => handler(event));
    }

    listenerCount(eventName) {
        return (this.listeners[eventName] || []).length;
    }

    appendChild(child) {
        this.children.push(child);
        return child;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }
}

function extractObjectDeclaration(source, declarationPrefix) {
    const start = source.indexOf(declarationPrefix);
    assert.notEqual(start, -1, `expected declaration starting with ${declarationPrefix}`);

    const objectStart = source.indexOf('{', start);
    assert.notEqual(objectStart, -1, 'expected object body start');

    let depth = 0;
    for (let index = objectStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') {
            depth += 1;
        } else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                const semicolonIndex = source.indexOf(';', index);
                assert.notEqual(semicolonIndex, -1, 'expected declaration terminator');
                return source.slice(start, semicolonIndex + 1);
            }
        }
    }

    throw new Error(`Could not extract declaration for ${declarationPrefix}`);
}

function loadFilesPreview() {
    const source = fs.readFileSync(path.join(__dirname, 'files.js'), 'utf8');
    const declaration = extractObjectDeclaration(source, 'const FilesPreview = {');

    const documentTarget = new FakeEventTarget();
    documentTarget.body = { style: {} };
    documentTarget.createElement = (tagName = 'div') => {
        const element = new FakeEventTarget();
        element.tagName = String(tagName).toUpperCase();

        if (element.tagName === 'AUDIO') {
            element.currentTime = 0;
            element.duration = Number.NaN;
            element.ended = false;
            element.paused = true;
            element.play = async () => {
                element.paused = false;
                element.dispatchEvent({ type: 'play' });
            };
            element.pause = () => {
                element.paused = true;
                element.dispatchEvent({ type: 'pause' });
            };
        }

        return element;
    };
    documentTarget.createTextNode = (textContent) => ({ textContent: String(textContent) });
    const windowTarget = new FakeEventTarget();
    windowTarget.innerWidth = 480;

    const dragHandle = new FakeEventTarget();
    const resizeHandle = new FakeEventTarget();
    const closeButton = new FakeEventTarget();
    const downloadButton = new FakeEventTarget();
    const backdrop = new FakeEventTarget();
    const sidebar = new FakeEventTarget();
    const body = new FakeEventTarget();
    const title = new FakeEventTarget();

    const downloadCalls = [];
    const revokedUrls = [];
    const context = {
        DOM: {
            filesPreviewClose: closeButton,
            filesPreviewDownload: downloadButton,
            filesPreviewBackdrop: backdrop,
            filesPreviewResizeHandle: resizeHandle,
            filesPreviewDragHandle: dragHandle,
            filesPreviewSidebar: sidebar,
            filesPreviewBody: body,
            filesPreviewTitle: title,
        },
        FileOperations: {
            downloadFile(fileId) {
                downloadCalls.push(fileId);
                return Promise.resolve();
            },
        },
        Icons: {
            file: '<svg></svg>',
            download: '<svg class="download"></svg>',
            play: '<svg class="play"></svg>',
            pause: '<svg class="pause"></svg>',
            createSvgElement() {
                return {
                    outerHTML: '<svg class="files-preview-unsupported-icon"></svg>',
                };
            },
        },
        URL: {
            revokeObjectURL(objectUrl) {
                revokedUrls.push(objectUrl);
            },
        },
        Utils: {
            escapeHtml(value) {
                return String(value ?? '')
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
            },
            formatFileSize() {
                return '';
            },
        },
        document: documentTarget,
        filesFormatT(_key, fallback, values = {}) {
            return String(fallback).replace(/\{(\w+)\}/g, (_, token) => values[token] || '');
        },
        filesT(_key, fallback) {
            return fallback;
        },
        notifyError() {},
        console,
        setTimeout,
        clearTimeout,
        window: windowTarget,
    };

    vm.runInNewContext(
        `${declaration}\nthis.FilesPreview = FilesPreview;`,
        context,
        { filename: 'files.js' },
    );

    return {
        FilesPreview: context.FilesPreview,
        body,
        downloadCalls,
        dragHandle,
        documentTarget,
        sidebar,
        revokedUrls,
        windowTarget,
    };
}

function findDescendant(node, predicate) {
    for (const child of node?.children || []) {
        if (predicate(child)) return child;
        const nestedMatch = findDescendant(child, predicate);
        if (nestedMatch) return nestedMatch;
    }
    return null;
}

test('file preview binds mobile drag handle events only once per open lifecycle', () => {
    const { FilesPreview, dragHandle, documentTarget } = loadFilesPreview();
    let closeCalls = 0;
    FilesPreview.close = () => {
        closeCalls += 1;
    };

    FilesPreview.bindEvents();
    FilesPreview.bindEvents();

    assert.equal(FilesPreview.eventsBound, true);
    assert.equal(documentTarget.listenerCount('keydown'), 1);
    assert.equal(dragHandle.listenerCount('click'), 1);

    dragHandle.dispatchEvent({ type: 'click' });
    assert.equal(closeCalls, 1);

    FilesPreview.unbindEvents();

    assert.equal(FilesPreview.eventsBound, false);
    assert.equal(documentTarget.listenerCount('keydown'), 0);
    assert.equal(dragHandle.listenerCount('click'), 0);

    dragHandle.dispatchEvent({ type: 'click' });
    assert.equal(closeCalls, 1);
});

test('fullscreen image preview closes only when the blurred surface is clicked', () => {
    const { FilesPreview, sidebar } = loadFilesPreview();
    let closeCalls = 0;
    FilesPreview.close = () => {
        closeCalls += 1;
    };
    FilesPreview.isOpen = true;
    FilesPreview.activeLayoutMode = 'image';
    FilesPreview.bindEvents();

    sidebar.dispatchEvent({
        type: 'click',
        target: {
            closest() {
                return null;
            },
        },
    });
    assert.equal(closeCalls, 1, 'clicking the blurred surface should close the preview');

    sidebar.dispatchEvent({
        type: 'click',
        target: {
            closest(selector) {
                assert.equal(selector, '.files-preview-image, .main-container-header-buttons');
                return { className: 'files-preview-image' };
            },
        },
    });
    assert.equal(closeCalls, 1, 'clicking the preview image should keep it open');

    FilesPreview.activeLayoutMode = 'panel';
    sidebar.dispatchEvent({
        type: 'click',
        target: {
            closest() {
                return null;
            },
        },
    });
    assert.equal(closeCalls, 1, 'the regular sidebar should not gain outside-click behavior');

    FilesPreview.unbindEvents();
    assert.equal(sidebar.listenerCount('click'), 0);
});


test('unsupported preview escapes filename-derived extension before rendering HTML', () => {
    const { FilesPreview } = loadFilesPreview();
    const preview = FilesPreview.createUnsupportedPreview({
        meta: {
            original_filename: 'poc.<img src=x onerror="alert(1)">',
        },
        file_type: 'application/x-unsupported',
    });
    const textMarkup = preview.children[1].innerHTML;

    assert.match(textMarkup, /&lt;IMG SRC=X ONERROR=&quot;ALERT\(1\)&quot;&gt;/);
    assert.doesNotMatch(textMarkup, /<img\s/i);
    assert.doesNotMatch(textMarkup, /onerror\s*=\s*["']/i);
});

test('unsupported preview download button works without an inline CSP handler', async () => {
    const { FilesPreview, downloadCalls } = loadFilesPreview();
    FilesPreview.activeFileId = 'file-unsupported-42';

    const preview = FilesPreview.createUnsupportedPreview({
        file_id: 'file-unsupported-42',
        file_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        meta: { original_filename: 'report.xlsx' },
    });
    const button = preview.children[2];

    assert.equal(button.listenerCount('click'), 1);
    assert.doesNotMatch(button.innerHTML, /onclick\s*=/i);
    button.dispatchEvent({ type: 'click' });
    await Promise.resolve();

    assert.deepEqual(downloadCalls, ['file-unsupported-42']);
});

test('audio preview synchronizes playback, seeking, duration, and error states', async () => {
    const { FilesPreview, body, revokedUrls } = loadFilesPreview();
    const preview = FilesPreview.createAudioPreviewElement('blob:audio-preview', {
        file_type: 'audio/wav',
        file_size: 57385,
        meta: { original_filename: 'recording-meeting-notes.wav' },
    }, 'audio/wav');

    const playButton = findDescendant(preview, (element) => element.className === 'files-preview-audio-play-button');
    const seek = findDescendant(preview, (element) => element.className === 'files-preview-audio-seek');
    const audio = findDescendant(preview, (element) => element.tagName === 'AUDIO');
    const source = findDescendant(preview, (element) => element.tagName === 'SOURCE');
    const currentTime = findDescendant(preview, (element) => element.className === 'files-preview-audio-time');
    const errorMessage = findDescendant(preview, (element) => element.className === 'files-preview-audio-error');

    assert.match(preview.className, /files-preview-audio-container--hybrid/);
    assert.equal(playButton.attributes['aria-label'], 'Play');
    assert.equal(seek.attributes['aria-label'], 'Playback position');
    assert.equal(seek.disabled, true);
    assert.equal(source.type, 'audio/wav');

    audio.duration = 14;
    audio.dispatchEvent({ type: 'loadedmetadata' });
    assert.equal(seek.max, '14');
    assert.equal(seek.disabled, false);

    playButton.dispatchEvent({ type: 'click' });
    await Promise.resolve();
    assert.equal(audio.paused, false);
    assert.equal(playButton.attributes['aria-label'], 'Pause');
    assert.match(playButton.innerHTML, /class="pause"/);

    audio.currentTime = 4.2;
    audio.dispatchEvent({ type: 'timeupdate' });
    assert.equal(currentTime.textContent, '0:04');
    assert.equal(seek.value, '4.2');
    assert.equal(seek.style['--audio-progress'], '30%');

    seek.value = '7';
    seek.dispatchEvent({ type: 'input' });
    assert.equal(audio.currentTime, 7);
    assert.equal(seek.attributes['aria-valuetext'], '0:07');

    playButton.dispatchEvent({ type: 'click' });
    await Promise.resolve();
    assert.equal(audio.paused, true);
    assert.equal(playButton.attributes['aria-label'], 'Play');

    playButton.dispatchEvent({ type: 'click' });
    await Promise.resolve();
    body.querySelectorAll = (selector) => {
        assert.equal(selector, 'audio, video');
        return [audio];
    };
    FilesPreview.activeObjectUrl = 'blob:audio-preview';
    FilesPreview.cleanupObjectUrl();
    assert.equal(audio.paused, true);
    assert.deepEqual(revokedUrls, ['blob:audio-preview']);

    audio.dispatchEvent({ type: 'error' });
    assert.equal(playButton.disabled, true);
    assert.equal(seek.disabled, true);
    assert.equal(errorMessage.hidden, false);

    audio.duration = 21;
    audio.currentTime = 3;
    audio.dispatchEvent({ type: 'durationchange' });
    audio.dispatchEvent({ type: 'timeupdate' });
    assert.equal(seek.disabled, true, 'later media events must not undo the error lockout');
});

test('audio preview remains usable when play is interrupted by a browser race', async () => {
    const { FilesPreview } = loadFilesPreview();
    const preview = FilesPreview.createAudioPreviewElement('blob:audio-preview', {
        file_type: 'audio/wav',
        meta: { original_filename: 'recording.wav' },
    }, 'audio/wav');
    const playButton = findDescendant(preview, (element) => element.className === 'files-preview-audio-play-button');
    const seek = findDescendant(preview, (element) => element.className === 'files-preview-audio-seek');
    const audio = findDescendant(preview, (element) => element.tagName === 'AUDIO');
    const errorMessage = findDescendant(preview, (element) => element.className === 'files-preview-audio-error');

    audio.duration = 10;
    audio.dispatchEvent({ type: 'loadedmetadata' });
    audio.play = async () => {
        const error = new Error('The play request was interrupted');
        error.name = 'AbortError';
        throw error;
    };

    playButton.dispatchEvent({ type: 'click' });
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(playButton.disabled, false);
    assert.equal(seek.disabled, false);
    assert.equal(errorMessage.hidden, true);
});

test('workspace Markdown files are identified by MIME type or original filename', () => {
    const { FilesPreview } = loadFilesPreview();

    assert.equal(FilesPreview.isMarkdownPreviewFile({ file_type: 'text/markdown' }), true);
    assert.equal(FilesPreview.isMarkdownPreviewFile({ file_type: 'text/x-markdown' }), true);
    assert.equal(FilesPreview.isMarkdownPreviewFile({
        file_type: 'application/octet-stream',
        meta: { original_filename: 'README.MD' },
    }), true);
    assert.equal(FilesPreview.isMarkdownPreviewFile({
        file_type: 'text/plain',
        meta: { original_filename: 'design.markdown' },
    }), true);
    assert.equal(FilesPreview.isMarkdownPreviewFile({
        file_type: 'text/plain',
        meta: { original_filename: 'notes.txt' },
    }), false);
});

test('every supported HTML-family file is identified for the shared Canvas preview', () => {
    const { FilesPreview } = loadFilesPreview();

    for (const mimeType of [
        'text/html; charset=utf-8',
        'application/html',
        'application/xhtml+xml',
        'application/x-html',
        'text/xhtml',
    ]) {
        assert.equal(
            FilesPreview.isHtmlPreviewFile({ file_type: mimeType }),
            true,
            `expected ${mimeType} to use Canvas`,
        );
    }

    for (const extension of ['html', 'htm', 'xhtml', 'xht', 'xhtm', 'shtml', 'shtm']) {
        const file = {
            file_type: 'application/octet-stream',
            meta: { original_filename: `document.${extension.toUpperCase()}` },
        };
        assert.equal(FilesPreview.isHtmlPreviewFile(file), true, `expected .${extension} to use Canvas`);
        assert.equal(FilesPreview.isCanvasPreviewFile(file), true, `expected .${extension} to be Canvas-compatible`);
    }
});

test('PDF files are identified by MIME type or a generic binary filename', () => {
    const { FilesPreview } = loadFilesPreview();

    assert.equal(FilesPreview.isPdfPreviewFile({ file_type: 'application/pdf' }), true);
    assert.equal(FilesPreview.isPdfPreviewFile({
        file_type: 'application/octet-stream',
        meta: { original_filename: 'Quarterly Report.PDF' },
    }), true);
    assert.equal(FilesPreview.isCanvasPreviewFile({
        file_type: 'application/octet-stream',
        meta: { original_filename: 'Quarterly Report.PDF' },
    }), true);
});

test('LaTeX source files open in the shared editable Canvas preview', () => {
    const { FilesPreview } = loadFilesPreview();

    for (const mimeType of ['text/x-tex', 'text/x-latex', 'application/x-latex']) {
        assert.equal(FilesPreview.isLatexPreviewFile({ file_type: mimeType }), true);
    }
    const genericTex = {
        file_type: 'application/octet-stream',
        meta: { original_filename: 'paper.TEX' },
    };
    assert.equal(FilesPreview.isLatexPreviewFile(genericTex), true);
    assert.equal(FilesPreview.isCanvasPreviewFile(genericTex), true);
});

test('SVG filenames recover the image MIME type from generic storage metadata', () => {
    const { FilesPreview } = loadFilesPreview();

    assert.equal(FilesPreview.resolveInitialMimeType({
        file_type: 'application/octet-stream',
        meta: { original_filename: 'generated-diagram.SVG' },
    }), 'image/svg+xml');
    assert.equal(FilesPreview.getPreferredLayoutMode({
        file_type: 'application/octet-stream',
        meta: { original_filename: 'generated-diagram.SVG' },
    }), 'image');
});

test('workspace Markdown files open through the shared Canvas preview', async () => {
    const { FilesPreview, windowTarget } = loadFilesPreview();
    const calls = [];
    windowTarget.canvasMarkdownWidget = {
        async openPreviewForFile(...args) {
            calls.push(args);
        },
    };

    await FilesPreview.open({
        file_id: 'file-42',
        file_type: 'application/octet-stream',
        meta: { original_filename: 'project-plan.md' },
    });

    assert.deepEqual(calls, [['file-42', 'project-plan.md', 'markdown']]);
    assert.equal(FilesPreview.isOpen, false);
});

test('HTML files from chat or Workspace open in editable Canvas preview mode', async () => {
    const { FilesPreview, windowTarget } = loadFilesPreview();
    const calls = [];
    windowTarget.canvasMarkdownWidget = {
        isPreviewOpenForFile() {
            return false;
        },
        async openPreviewForFile(...args) {
            calls.push(args);
        },
    };

    await FilesPreview.open({
        file_id: 'html-42',
        file_type: 'application/octet-stream',
        meta: { original_filename: 'Status Page.xhtml' },
    });

    assert.deepEqual(calls, [['html-42', 'Status Page.xhtml', 'html']]);
    assert.equal(FilesPreview.isOpen, false);
});

test('PDF files from chat or Workspace open through the shared Canvas sidebar', async () => {
    const { FilesPreview, windowTarget } = loadFilesPreview();
    const calls = [];
    windowTarget.canvasMarkdownWidget = {
        isPreviewOpenForFile() {
            return false;
        },
        async openPreviewForFile(...args) {
            calls.push(args);
        },
    };

    await FilesPreview.open({
        file_id: 'pdf-42',
        file_type: 'application/octet-stream',
        meta: { original_filename: 'Quarterly Report.pdf' },
    });

    assert.deepEqual(calls, [['pdf-42', 'Quarterly Report.pdf', 'pdf']]);
    assert.equal(FilesPreview.isOpen, false);
});

test('opening an already active PDF closes the Canvas sidebar', async () => {
    const { FilesPreview, windowTarget } = loadFilesPreview();
    const calls = [];
    windowTarget.canvasMarkdownWidget = {
        isPreviewOpenForFile(fileId) {
            return fileId === 'pdf-active';
        },
        hidePreviewPanel() {
            calls.push('close-canvas');
        },
        async openPreviewForFile() {
            calls.push('open-canvas');
        },
    };

    await FilesPreview.open({
        file_id: 'pdf-active',
        file_type: 'application/pdf',
        meta: { original_filename: 'active.pdf' },
    });

    assert.deepEqual(calls, ['close-canvas']);
});

test('opening Canvas from an active generic preview closes the old file surface first', async () => {
    const { FilesPreview, windowTarget } = loadFilesPreview();
    const calls = [];
    FilesPreview.isOpen = true;
    FilesPreview.close = () => {
        calls.push('close-generic');
        FilesPreview.isOpen = false;
    };
    windowTarget.canvasMarkdownWidget = {
        async openPreviewForFile() {
            calls.push('open-canvas');
        },
    };

    const opened = await FilesPreview.openCanvasPreview(
        { file_type: 'text/markdown' },
        'file-7',
        'notes.md',
    );

    assert.equal(opened, true);
    assert.deepEqual(calls, ['close-generic', 'open-canvas']);
});

test('files workspace does not auto-fetch pages while hidden on index load', () => {
    const source = fs.readFileSync(path.join(__dirname, 'files.js'), 'utf8');

    assert.match(source, /document\.addEventListener\('DOMContentLoaded', \(\) => \{\s*EventHandlers\.setupListeners\(\);\s*FileDragDrop\.init\(\);\s*\}\);/);
    assert.doesNotMatch(source, /filesContainer\?\.style\.display !== 'none'[\s\S]*FilesManager\.initialize\(\)/);
});

test('files infinite scroll only loads more when the files workspace is visible', () => {
    const source = fs.readFileSync(path.join(__dirname, 'files.js'), 'utf8');
    const maybeLoadMoreStart = source.indexOf('async maybeLoadMore()');
    assert.notEqual(maybeLoadMoreStart, -1, 'expected FilesManager.maybeLoadMore');
    const maybeLoadMoreEnd = source.indexOf('\n},\n\ngetCachedFiles', maybeLoadMoreStart);
    assert.notEqual(maybeLoadMoreEnd, -1, 'expected end of FilesManager.maybeLoadMore');
    const maybeLoadMoreSource = source.slice(maybeLoadMoreStart, maybeLoadMoreEnd);

    assert.match(maybeLoadMoreSource, /!isFilesViewVisible\(\)/);
    assert.match(maybeLoadMoreSource, /scroller\.clientHeight <= 0/);
});
