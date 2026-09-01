const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const HELPER_PATH = path.join(__dirname, 'deleteWarningModal.js');
const CHAT_MODAL_REGISTRY_PATH = path.join(__dirname, '..', 'chat', 'deleteWarningModals.js');
const MEMORIES_PATH = path.join(__dirname, '..', 'chat', 'memories.js');
const TWO_FA_PATH = path.join(__dirname, 'twofa.js');

function loadBackdropBinder() {
    const context = { window: {} };
    vm.runInNewContext(fs.readFileSync(HELPER_PATH, 'utf8'), context, {
        filename: HELPER_PATH,
    });
    return context.window.DeleteWarningModal.bindBackdropDismissal;
}

function loadModalCreator() {
    let translatedRoot = null;
    let translatedHtml = '';
    const overlay = {
        setAttribute() {},
        querySelectorAll() {
            return [];
        },
    };
    const context = {
        document: {
            createElement() {
                return overlay;
            },
        },
        window: {
            translateI18nElements(root) {
                translatedRoot = root;
                translatedHtml = root.innerHTML;
            },
        },
    };
    vm.runInNewContext(fs.readFileSync(HELPER_PATH, 'utf8'), context, {
        filename: HELPER_PATH,
    });
    return {
        create: context.window.DeleteWarningModal.create,
        getTranslatedHtml: () => translatedHtml,
        getTranslatedRoot: () => translatedRoot,
    };
}

function createOverlay() {
    const listeners = new Map();
    const overlay = {
        addEventListener(type, handler) {
            listeners.set(type, handler);
        },
    };
    return {
        overlay,
        click(target = overlay) {
            listeners.get('click')?.({ target });
        },
    };
}

test('shared modal backdrop dismissal ignores clicks inside the dialog', () => {
    const bindBackdropDismissal = loadBackdropBinder();
    const harness = createOverlay();
    let dismissals = 0;
    const dismissalControl = {
        disabled: false,
        click() {
            dismissals += 1;
        },
    };

    bindBackdropDismissal(harness.overlay, dismissalControl);
    harness.click({});
    assert.equal(dismissals, 0);

    harness.click();
    assert.equal(dismissals, 1);
});

test('shared modal backdrop dismissal respects disabled cancel controls', () => {
    const bindBackdropDismissal = loadBackdropBinder();
    const harness = createOverlay();
    let dismissals = 0;
    const dismissalControl = {
        disabled: true,
        click() {
            dismissals += 1;
        },
    };

    bindBackdropDismissal(harness.overlay, dismissalControl);
    harness.click();
    assert.equal(dismissals, 0);
});

test('shared modals translate their subtree when mounted after i18n is ready', () => {
    const harness = loadModalCreator();
    const overlay = harness.create({
        id: 'lateModal',
        contentHtml: '<h2 data-i18n="modal_import_groups_title">Import Groups</h2>',
        actions: [{ id: 'lateModalCancel', role: 'cancel', i18n: 'btn_cancel', text: 'Cancel' }],
    });

    assert.equal(harness.getTranslatedRoot(), overlay);
    assert.match(harness.getTranslatedHtml(), /data-i18n="modal_import_groups_title"/);
    assert.match(harness.getTranslatedHtml(), /data-i18n="btn_cancel"/);
});

test('group import dialog exposes translated copy and an associated description', () => {
    const registry = fs.readFileSync(
        path.join(__dirname, '..', 'admin', 'deleteWarningModals.js'),
        'utf8',
    );
    const groupModalStart = registry.indexOf("id: 'groupImportOverlay'");
    const groupModalEnd = registry.indexOf("id: 'importProvidersOverlay'", groupModalStart);
    const groupModal = registry.slice(groupModalStart, groupModalEnd);

    assert.match(groupModal, /subtitleId: 'groupImportSubtitle'/);
    for (const key of [
        'modal_import_groups_title',
        'modal_import_groups_subtitle',
        'modal_close_aria',
        'modal_choose_file',
        'modal_select_all',
        'modal_import_selected',
    ]) {
        assert.ok(groupModal.includes(key), `group modal must use ${key}`);
    }
    assert.match(registry, /const cancel = \(id, i18n = 'btn_cancel'/);
});

test('account settings modals opt into shared backdrop dismissal', () => {
    const registry = fs.readFileSync(CHAT_MODAL_REGISTRY_PATH, 'utf8');
    const twoFa = fs.readFileSync(TWO_FA_PATH, 'utf8');

    assert.match(
        registry,
        /id: 'changePasswordOverlay',\s+backdropDismissControlId: 'changePasswordCancelButton'/,
    );
    assert.match(
        registry,
        /id: 'deleteAccountOverlay',\s+backdropDismissControlId: 'deleteAccountCancelButton'/,
    );
    assert.match(
        twoFa,
        /DeleteWarningModal\.bindBackdropDismissal\(tfaOverlay, hide2FASetup\)/,
    );
});

test('memory editor reuses the shared modal form instead of nesting a feature card', () => {
    const registry = fs.readFileSync(CHAT_MODAL_REGISTRY_PATH, 'utf8');

    assert.match(
        registry,
        /id: 'memoriesEditorOverlay',[\s\S]*class="delete-warning-card-form memories-editor-form"/,
    );
    assert.match(registry, /id="memoriesContentInput" class="form-input memories-editor-textarea"/);
    assert.doesNotMatch(registry, /memoriesCategorySelect/);
    assert.doesNotMatch(registry, /class="memories-card memories-form-card"/);
});

test('memory cards own edit and delete actions while the editor keeps only save', () => {
    const registry = fs.readFileSync(CHAT_MODAL_REGISTRY_PATH, 'utf8');
    const memories = fs.readFileSync(MEMORIES_PATH, 'utf8');
    const editorStart = registry.indexOf("id: 'memoriesEditorOverlay'");
    const editorEnd = registry.indexOf("id: 'notesDeleteOverlay'", editorStart);
    const editorDefinition = registry.slice(editorStart, editorEnd);

    assert.doesNotMatch(editorDefinition, /memoriesResetBtn|memoriesDeleteBtn/);
    assert.match(editorDefinition, /submit\('memoriesSaveBtn'/);
    assert.match(memories, /<article class="memory-item[\s\S]*data-memory-action="edit"/);
    assert.match(
        memories,
        /memory-item-content[\s\S]*memory-item-footer[\s\S]*memory-item-actions/,
    );
    assert.match(memories, /data-memory-action="delete"/);
    assert.match(
        memories,
        /const memoryId = button\?\.closest\('\[data-memory-id\]'\)[\s\S]*await window\.showDeleteConfirm\([\s\S]*if \(confirmed\) await this\.deleteMemoryFromCard\(memoryId, button\)/,
    );
});
