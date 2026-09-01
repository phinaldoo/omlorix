const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractSnippet(source, startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    assert.notEqual(start, -1, `expected snippet starting with ${startMarker}`);
    const end = source.indexOf(endMarker, start);
    assert.notEqual(end, -1, `expected snippet ending before ${endMarker}`);
    return source.slice(start, end);
}

function loadHelpers({ formatTranslationImpl, getTranslationImpl } = {}) {
    const source = fs.readFileSync(path.join(__dirname, 'fileFolders.js'), 'utf8');
    const context = {
        window: {
            formatTranslation: formatTranslationImpl,
            getTranslation: getTranslationImpl || ((_key, fallback) => fallback),
        },
    };

    vm.runInNewContext(
        [
            extractSnippet(source, 'function fileFoldersT(', '// ============================================================================\n// State'),
            'this.helpers = { getFolderInvitationSuccessMessage, getFolderCloneSuccessMessage, getFolderAcceptSuccessMessage };',
        ].join('\n\n'),
        context,
        { filename: 'fileFolders.js' },
    );

    return context.helpers;
}

function loadFolderSharingImplementations({ translate = (_key, fallback) => fallback } = {}) {
    const source = fs.readFileSync(path.join(__dirname, 'fileFolders.js'), 'utf8');
    const appendedElements = [];
    const openedMenus = [];
    const domAccesses = [];
    const elements = new Map();

    const createElement = () => ({
        style: {},
        dataset: {},
        className: '',
        innerHTML: '',
        hidden: false,
        value: '',
        checked: false,
        disabled: false,
        textContent: '',
        offsetHeight: 0,
        offsetWidth: 0,
        classList: {
            add() {},
            remove() {},
            toggle() {},
        },
        addEventListener() {},
        remove() {},
        contains() { return false; },
        matches() { return false; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        setAttribute() {},
        removeAttribute() {},
        getBoundingClientRect() {
            return { top: 0, right: 0, bottom: 0 };
        },
    });

    const document = {
        body: {
            appendChild(element) {
                appendedElements.push(element);
            },
        },
        documentElement: { clientWidth: 1280, clientHeight: 720 },
        createElement,
        getElementById(id) {
            domAccesses.push(id);
            if (!elements.has(id)) elements.set(id, createElement());
            return elements.get(id);
        },
        querySelectorAll() { return []; },
        addEventListener() {},
        removeEventListener() {},
    };
    const state = {
        folders: [],
        selectedUserIds: [],
    };
    const context = {
        document,
        state,
        window: {
            innerWidth: 1280,
            innerHeight: 720,
            addEventListener() {},
            removeEventListener() {},
            openDropdownMenu(options) {
                openedMenus.push(options);
                return { close() {} };
            },
        },
        translate,
        requestAnimationFrame: callback => callback(),
        setTimeout() {},
    };

    vm.runInNewContext(
        [
            'const FileFoldersState = this.state;',
            'const FolderDOM = {};',
            'const FolderAPI = {};',
            'const FolderModal = {};',
            'const FolderIconPicker = {};',
            'const FolderRenderer = { escapeHtml: value => String(value ?? "") };',
            'const Icons = { close: "close", edit: "edit", share: "share", trash: "trash", logout: "logout" };',
            'function fileFoldersT(key, fallback) { return translate(key, fallback); }',
            extractSnippet(
                source,
                'const ContextMenu = {',
                '// ============================================================================\n// Modal',
            ),
            extractSnippet(
                source,
                'const FileFoldersManager = {',
                '// ============================================================================\n// Share Modal',
            ),
            extractSnippet(
                source,
                'const FolderShareModal = {',
                '// ============================================================================\n// Accept Shared Folder Modal',
            ),
            'this.folderSharing = { ContextMenu, FileFoldersManager, FolderShareModal };',
        ].join('\n\n'),
        context,
        { filename: 'fileFolders.js' },
    );

    return {
        ...context.folderSharing,
        appendedElements,
        openedMenus,
        domAccesses,
        elements,
        state,
    };
}

test('folder invitation success message always uses translated copy', () => {
    const { getFolderInvitationSuccessMessage } = loadHelpers({
        formatTranslationImpl: (key, fallback, vars = {}) => {
        assert.equal(key, 'files_folder_share_invited_count');
        return String(fallback).replace(/\{(\w+)\}/g, (_, token) => String(vars[token] ?? ''));
        },
    });

    assert.equal(
        getFolderInvitationSuccessMessage({
            invited_count: 3,
            message: 'Successfully invited 3 user(s) to the folder.',
        }),
        'Invited 3 user(s)',
    );
});

test('folder clone success message ignores backend english copy', () => {
    const { getFolderCloneSuccessMessage } = loadHelpers({
        getTranslationImpl: (_key, fallback) => `translated:${fallback}`,
    });

    assert.equal(
        getFolderCloneSuccessMessage({
            message: 'Folder cloned successfully!',
        }),
        'translated:Folder cloned successfully!',
    );
});

test('folder accept success message ignores backend english copy', () => {
    const { getFolderAcceptSuccessMessage } = loadHelpers({
        getTranslationImpl: (_key, fallback) => `translated:${fallback}`,
    });

    assert.equal(
        getFolderAcceptSuccessMessage({
            message: 'Folder added to your workspace',
        }),
        'translated:Folder added to your workspace!',
    );
});

test('folder create modal icon picker delegates to the shared icon picker', () => {
    const source = fs.readFileSync(path.join(__dirname, 'fileFolders.js'), 'utf8');
    const modalSnippet = extractSnippet(source, 'const FolderModal = {', '// ============================================================================\n// Main Manager');
    const renderIconPickerSnippet = extractSnippet(modalSnippet, '    renderIconPicker() {', '    updateIconPickerPreview() {');

    assert.equal(
        renderIconPickerSnippet.includes('this.escapeHtml('),
        false,
        'renderIconPicker must not call an undefined FolderModal.escapeHtml method',
    );
    assert.equal(
        renderIconPickerSnippet.includes('FolderIconPicker?.render?.()'),
        true,
        'renderIconPicker should use the shared workspace icon picker renderer',
    );
});

test('folder share modal preserves its layout and positions select chevrons', () => {
    const source = fs.readFileSync(path.join(__dirname, 'fileFolders.js'), 'utf8');
    const shareSnippet = extractSnippet(
        source,
        'const FolderShareModal = {',
        '// ============================================================================\n// Accept Shared Folder Modal',
    );

    assert.equal(
        shareSnippet.match(/class="notes-share-select-arrow"/g)?.length,
        2,
        'both folder share selects need the positioned chevron wrapper',
    );
    assert.equal(shareSnippet.includes("linkMode.style.display = 'block'"), false);
    assert.equal(shareSnippet.includes("inviteMode.style.display = 'block'"), false);
    assert.equal(shareSnippet.includes('linkMode.hidden = !showLinkMode'), true);
    assert.equal(shareSnippet.includes('inviteMode.hidden = showLinkMode'), true);
    assert.equal(shareSnippet.includes('id="folderInviteBtn" disabled'), true);
    assert.equal(shareSnippet.includes('inviteBtn.disabled = true'), true);
});

test('folder invitee remove controls have localized user-specific accessible names', () => {
    const { FolderShareModal, elements, state } = loadFolderSharingImplementations({
        translate: (key, fallback) => key === 'chat_share_invite_remove_user_aria'
            ? 'Localized remove user'
            : fallback,
    });
    state.publicUsers = [{ id: 'user-1', display_name: 'Ada Lovelace' }];
    state.selectedUserIds = ['user-1'];

    FolderShareModal.updateSelectedUsersUI();

    const selectedList = elements.get('folderSelectedUsersList');
    assert.match(selectedList.innerHTML, /aria-label="Localized remove user: Ada Lovelace"/);
});

test('private system folders cannot open folder sharing controls', async () => {
    const {
        ContextMenu,
        FileFoldersManager,
        FolderShareModal,
        openedMenus,
        domAccesses,
        state,
    } = loadFolderSharingImplementations();
    const systemFolder = { id: 'system-folder', name: 'Canvas', system_kind: 'canvas' };
    const regularFolder = { id: 'regular-folder', name: 'Regular folder', system_kind: null };
    const trigger = {
        matches: () => true,
        setAttribute() {},
    };
    const menuEvent = {
        currentTarget: trigger,
        target: { closest: () => null },
    };

    ContextMenu.show(menuEvent, systemFolder);
    const systemMenu = openedMenus[openedMenus.length - 1];
    assert.equal(systemMenu.items.some(item => item.label === 'Share'), false);

    ContextMenu.show(menuEvent, regularFolder);
    const regularMenu = openedMenus[openedMenus.length - 1];
    assert.equal(regularMenu.items.some(item => item.label === 'Share'), true);

    const managerModalCalls = [];
    const realShowShareModal = FolderShareModal.showShareModal;
    FolderShareModal.showShareModal = async folderId => managerModalCalls.push(folderId);
    await FileFoldersManager.shareFolder(systemFolder);
    await FileFoldersManager.shareFolder(regularFolder);
    assert.equal(managerModalCalls.length, 1);
    assert.equal(managerModalCalls[0], regularFolder.id);
    FolderShareModal.showShareModal = realShowShareModal;

    const loadedFolderIds = [];
    FolderShareModal.setShareMode = () => {};
    FolderShareModal.clearInviteSelectionError = () => {};
    FolderShareModal.onShareTypeChange = () => {};
    FolderShareModal.onInviteTypeChange = () => {};
    FolderShareModal.loadShareStatus = async folderId => loadedFolderIds.push(folderId);

    state.folders = [systemFolder, regularFolder];
    const accessesBeforeSystemFolder = domAccesses.length;
    await FolderShareModal.showShareModal(systemFolder.id);
    assert.equal(domAccesses.length, accessesBeforeSystemFolder);
    assert.equal(loadedFolderIds.length, 0);

    await FolderShareModal.showShareModal(regularFolder.id);
    assert.equal(state.sharingFolderId, regularFolder.id);
    assert.equal(loadedFolderIds.length, 1);
    assert.equal(loadedFolderIds[0], regularFolder.id);
});

test('folder More options stays hit-testable and cannot activate its folder row', () => {
    const source = fs.readFileSync(path.join(__dirname, 'fileFolders.js'), 'utf8');
    const css = fs.readFileSync(path.join(__dirname, '..', '..', 'css', 'chat', 'files.css'), 'utf8');
    const defaultActionRule = css.match(/\.files-sidebar-item-action-btn\s*\{([^}]*)\}/);
    const listenerSnippet = extractSnippet(source, '    attachFolderListeners(folders) {', '    updateCounts() {');

    assert.ok(defaultActionRule, 'expected the folder action button CSS rule');
    assert.match(defaultActionRule[1], /pointer-events:\s*auto\s*;/);
    assert.doesNotMatch(defaultActionRule[1], /pointer-events:\s*none\s*;/);
    assert.match(listenerSnippet, /if \(e\.target\.closest\('\[data-folder-ctx\]'\)\) return;/);
    assert.match(
        listenerSnippet,
        /ctxBtn\.addEventListener\('click', \(e\) => \{\s*e\.preventDefault\(\);\s*e\.stopPropagation\(\);\s*ContextMenu\.show\(e, folder\);/,
    );
});

test('folder share action icons remain visible and tappable on mobile', () => {
    const css = fs.readFileSync(path.join(__dirname, '..', '..', 'css', 'chat', 'files.css'), 'utf8');
    const source = fs.readFileSync(path.join(__dirname, 'fileFolders.js'), 'utf8');

    assert.equal(css.includes('#folderShareOverlay .notes-share-active-copy svg'), true);
    assert.equal(css.includes('#folderShareOverlay .notes-share-active-delete svg'), true);
    assert.equal(css.includes('min-width: 20px;'), true);
    assert.equal(css.includes('visibility: visible;'), true);
    assert.equal(css.includes('min-width: 44px;'), true);
    assert.equal(source.includes('btn.innerHTML = Icons.copy;'), true);
    assert.equal(source.includes('class="om-button border cancel notes-share-active-copy"'), false);
    assert.equal(source.includes('class="om-button border danger-nofill notes-share-active-delete"'), false);
});

test('workspace sidebar add controls use the shared om-button styles', () => {
    const markup = fs.readFileSync(path.join(__dirname, '..', '..', 'index.html'), 'utf8');
    const workspaceStyles = fs.readFileSync(path.join(__dirname, '..', '..', 'css', 'chat', 'workspace-core.css'), 'utf8');

    assert.match(markup, /class="om-button" id="filesFolderAddBtn"/);
    assert.match(markup, /class="om-button" id="todosSidebarAddBtn"/);
    assert.match(markup, /class="om-button" id="notesSidebarAddBtn"/);
    assert.doesNotMatch(markup, /(?:files|todos|notes)-sidebar-add-btn/);
    assert.doesNotMatch(workspaceStyles, /(?:files|todos|notes)-sidebar-add-btn/);
});
