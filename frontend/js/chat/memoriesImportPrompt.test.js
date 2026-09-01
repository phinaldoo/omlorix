const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const MEMORIES_PATH = path.join(__dirname, 'memories.js');
const MODALS_PATH = path.join(__dirname, 'deleteWarningModals.js');

class MockElement {
    constructor() {
        this.dataset = {};
        this.attributes = new Map();
        this.textContent = '';
    }

    setAttribute(name, value) {
        this.attributes.set(name, String(value));
    }

    getAttribute(name) {
        return this.attributes.get(name) ?? null;
    }
}

test('memory import prompt starts collapsed and exposes an accessible expansion toggle', () => {
    const modalSource = fs.readFileSync(MODALS_PATH, 'utf8');
    assert.match(modalSource, /id="memoriesImportPromptCard" data-expanded="false"/);
    assert.match(
        modalSource,
        /id="memoriesImportPromptToggle"[^>]*aria-controls="memoriesImportPromptPreview"[^>]*aria-expanded="false"/,
    );

    const elements = new Map([
        ['memoriesImportPromptCard', new MockElement()],
        ['memoriesImportPromptToggle', new MockElement()],
        ['memoriesImportPromptToggleText', new MockElement()],
    ]);
    const translations = {
        workspace_memories_import_prompt_show_less: 'Show less',
        workspace_memories_import_prompt_show_more: 'Show more',
    };
    const context = {
        document: {
            getElementById(id) {
                return elements.get(id) || null;
            },
        },
        window: null,
    };
    context.window = context;
    context.getTranslation = (key, fallback) => translations[key] || fallback;

    vm.runInNewContext(fs.readFileSync(MEMORIES_PATH, 'utf8'), context, {
        filename: MEMORIES_PATH,
    });

    context.MemoriesManager.setImportPromptExpanded(true);
    assert.equal(elements.get('memoriesImportPromptCard').dataset.expanded, 'true');
    assert.equal(elements.get('memoriesImportPromptToggle').getAttribute('aria-expanded'), 'true');
    assert.equal(elements.get('memoriesImportPromptToggleText').textContent, 'Show less');

    context.MemoriesManager.setImportPromptExpanded(false);
    assert.equal(elements.get('memoriesImportPromptCard').dataset.expanded, 'false');
    assert.equal(elements.get('memoriesImportPromptToggle').getAttribute('aria-expanded'), 'false');
    assert.equal(elements.get('memoriesImportPromptToggleText').textContent, 'Show more');
});
