const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName} in projectsChat.js`);

    const bodyStart = source.indexOf('{', start);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') {
            depth += 1;
        } else if (source[index] === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`could not extract ${functionName}`);
}

class FakeActionButton {
    constructor() {
        this.listeners = new Map();
    }

    addEventListener(type, listener) {
        this.listeners.set(type, listener);
    }

    closest(selector) {
        return selector === '.project-sidebar-row-action' ? this : null;
    }

    click() {
        const event = { target: this };
        this.listeners.get('click')?.(event);
        this.parentRow?.click(this);
    }
}

class FakeFileRow {
    constructor(actionButton) {
        this.actionButton = actionButton;
        actionButton.parentRow = this;
        this.listeners = new Map();
        this.style = {};
    }

    addEventListener(type, listener) {
        this.listeners.set(type, listener);
    }

    querySelector(selector) {
        return selector === '.project-sidebar-row-action' ? this.actionButton : null;
    }

    click(target = { closest: () => null }) {
        this.listeners.get('click')?.({ target });
    }
}

test('project file action is named, natively keyboard-operable, and opens the shared preview', () => {
    const source = fs.readFileSync(path.join(__dirname, 'projectsChat.js'), 'utf8');
    const css = fs.readFileSync(path.join(__dirname, '..', '..', 'css', 'chat', 'projectSidebar.css'), 'utf8');
    const actionButton = new FakeActionButton();
    const row = new FakeFileRow(actionButton);
    const container = {
        innerHTML: '',
        querySelectorAll(selector) {
            return selector === '.project-sidebar-row--file' ? [row] : [];
        },
    };
    const openCalls = [];
    const translationCalls = [];
    let closeCalls = 0;
    const FilesPreview = {
        isOpen: false,
        activeFileId: null,
        open(file) {
            openCalls.push(file);
            return Promise.resolve();
        },
        close() {
            closeCalls += 1;
        },
    };
    const context = {
        FilesPreview,
        Icons: { info: '<svg></svg>', chevron: '<svg></svg>' },
        console,
        getSidebarElement: () => container,
        htmlEscape: (value) => String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;'),
        projectSidebarFormatT: (key, fallback, vars) => {
            translationCalls.push({ key, fallback, vars });
            return `Localized file preview: ${vars.filename}`;
        },
        projectSidebarSelectors: { filesContainer: '.project-sidebar-files' },
        projectSidebarT: (_key, fallback) => fallback,
        renderEmptyState: () => {},
    };

    vm.runInNewContext(
        [
            extractFunction(source, 'toggleProjectFilePreview'),
            extractFunction(source, 'renderFilesList'),
            'this.renderFilesList = renderFilesList;',
        ].join('\n\n'),
        context,
        { filename: 'projectsChat.js' },
    );

    const file = {
        file_id: 'file-1',
        meta: { original_filename: 'Report & notes.pdf' },
        file_category: 'project_context',
    };
    context.renderFilesList([file]);

    assert.match(
        container.innerHTML,
        /<button type="button" class="project-sidebar-row-action"[^>]*aria-label="Localized file preview: Report &amp; notes\.pdf"/,
    );
    assert.deepEqual(translationCalls.map((call) => ({ ...call, vars: { ...call.vars } })), [{
        key: 'files_preview_open_file_aria',
        fallback: 'Open file preview: {filename}',
        vars: { filename: 'Report & notes.pdf' },
    }]);
    assert.ok(actionButton.listeners.has('click'), 'the semantic button must own a click handler');
    assert.match(css, /\.project-sidebar-row-action:focus\s*\{[^}]*opacity:\s*1/s);
    assert.match(css, /\.project-sidebar-row-action:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--primary-color\)/s);

    actionButton.click();
    assert.deepEqual(openCalls, [file]);

    FilesPreview.isOpen = true;
    FilesPreview.activeFileId = file.file_id;
    actionButton.click();
    assert.equal(closeCalls, 1);
    assert.equal(openCalls.length, 1);

    FilesPreview.isOpen = false;
    row.click();
    assert.deepEqual(openCalls, [file, file]);
});
