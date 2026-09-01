const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const frontendRoot = path.join(__dirname, '..', '..');
const indexSource = readFrontendSource(path.join(frontendRoot, 'index.html'), 'utf8');
const overlayStyle = readFrontendSource(path.join(frontendRoot, 'css', 'chat', 'dragNDrop.css'), 'utf8');
const animationStyle = readFrontendSource(path.join(frontendRoot, 'css', 'common', 'animations.css'), 'utf8');
const chatBoxSource = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
const filesSource = readFrontendSource(path.join(__dirname, 'files.js'), 'utf8');

/** Return the source for one named function declaration. */
function functionSource(source, name, nextName) {
    const start = source.indexOf(`function ${name}(`);
    const end = nextName ? source.indexOf(`function ${nextName}(`, start + 1) : source.length;
    assert.notEqual(start, -1, `expected function ${name}`);
    assert.notEqual(end, -1, `expected function ${nextName}`);
    return source.slice(start, end);
}

test('file drop overlay uses the simplified accessible demo structure', () => {
    assert.match(indexSource, /id="chatDropOverlay"[^>]*role="status"[^>]*aria-live="polite"/);
    assert.match(indexSource, /class="drop-overlay-dropzone"/);
    assert.match(indexSource, /id="dropOverlayIcon"[^>]*aria-hidden="true"/);
    assert.match(indexSource, /data-i18n="dropOverlay\.title"/);
    assert.match(indexSource, /data-i18n="dropOverlay\.subtitle"/);
    assert.doesNotMatch(indexSource, /drop-overlay-content/);

    assert.match(overlayStyle, /width:\s*min\(560px, 86vw\)/);
    assert.match(overlayStyle, /border:\s*2px dashed var\(--border-color\)/);
    assert.match(overlayStyle, /background:\s*var\(--surface-elevated\)/);
    assert.doesNotMatch(overlayStyle, /radial-gradient|linear-gradient|::before|::after/);
    assert.match(animationStyle, /@keyframes drop-overlay-pulse/);
    assert.match(overlayStyle, /@media \(prefers-reduced-motion: reduce\)[\s\S]*animation:\s*none/);
});

test('chat drop flow routes files once and respects upload permissions', async () => {
    const dropSource = functionSource(chatBoxSource, 'handleChatDrop', 'bindChatDropZone');
    assert.match(dropSource, /extractDroppedFiles\(dataTransfer\)/);
    assert.match(dropSource, /canUploadChatFiles\(\)/);
    assert.match(dropSource, /window\.handleChatFileSelection\(files\)/);
    assert.match(chatBoxSource, /bindChatDropZone\(window\)/);
    assert.doesNotMatch(chatBoxSource, /bindChatDropZone\(chatDropOverlay\)|bindChatDropZone\(chatBoxArea\)/);
    assert.match(chatBoxSource, /window\.addEventListener\('blur', resetChatDropState\)/);

    // Execute the production drop handler with browser-like event objects so
    // routing is verified beyond the static source contract.
    const routingSource = [
        functionSource(chatBoxSource, 'isFileDragEvent', 'isInternalAppDragEvent'),
        functionSource(chatBoxSource, 'isInternalAppDragEvent', 'isLikelyExternalFileDragEvent'),
        functionSource(chatBoxSource, 'isLikelyExternalFileDragEvent', 'extractDroppedFiles'),
        functionSource(chatBoxSource, 'extractDroppedFiles', 'handleChatDragEnter'),
        dropSource,
    ].join('\n');
    const selected = [];
    const workspaceUploads = [];
    const notifications = [];
    const state = { filesView: false, canUpload: true, resetCount: 0 };
    const context = {
        CHAT_REFERENCE_DRAG_MIME: 'application/x-chat-reference',
        canUploadChatFiles: () => state.canUpload,
        console,
        getChatI18nString: (_key, fallback) => fallback,
        isFilesViewActive: () => state.filesView,
        isSkillImportDropActive: () => false,
        notifyError: (message) => notifications.push(message),
        resetChatDropState: () => { state.resetCount += 1; },
        window: {
            FilesManager: { uploadFiles: (files) => workspaceUploads.push(files) },
            handleChatFileSelection: (files) => selected.push(files),
        },
    };
    vm.runInNewContext(`${routingSource}\nthis.runDrop = handleChatDrop;`, context);

    const createDropEvent = (file) => ({
        cancelable: true,
        dataTransfer: { files: [file], items: [], types: ['Files'] },
        preventDefault() { this.defaultPrevented = true; },
        stopImmediatePropagation() {},
        stopPropagation() {},
    });
    const chatFile = { name: 'chat.txt', size: 4, type: 'text/plain' };
    const chatEvent = createDropEvent(chatFile);
    context.runDrop(chatEvent);
    assert.equal(chatEvent.defaultPrevented, true);
    assert.equal(selected.length, 1);
    assert.equal(selected[0].length, 1);
    assert.equal(selected[0][0], chatFile);
    assert.equal(workspaceUploads.length, 0);

    state.filesView = true;
    const workspaceFile = { name: 'workspace.txt', size: 9, type: 'text/plain' };
    context.runDrop(createDropEvent(workspaceFile));
    assert.equal(workspaceUploads.length, 1);
    assert.equal(workspaceUploads[0].length, 1);
    assert.equal(workspaceUploads[0][0], workspaceFile);

    state.filesView = false;
    state.canUpload = false;
    context.runDrop(createDropEvent({ name: 'blocked.txt', size: 7, type: 'text/plain' }));
    assert.equal(selected.length, 1, 'disabled uploads must not reach the chat uploader');
    assert.deepEqual(notifications, ['File uploads are disabled for your account.']);
});

test('Workspace Files keeps ownership of its contextual drop upload', () => {
    assert.match(filesSource, /FILES_DROP_OVERLAY_OWNER_FILES = 'workspace-files'/);
    assert.match(filesSource, /filesT\('files_drop_upload_title'/);
    assert.match(filesSource, /FileOperations\.uploadFiles\(files\)/);
    assert.match(filesSource, /title: filesT\('dropOverlay\.title'/);
    assert.match(filesSource, /subtitle: filesT\('dropOverlay\.subtitle'/);
});
