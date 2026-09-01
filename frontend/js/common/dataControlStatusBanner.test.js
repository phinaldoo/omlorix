const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeElement {
    constructor() {
        this.attributes = {};
        this.classNames = new Set();
        this.hidden = false;
        this.style = {};
        this.textContent = '';
        this.classList = {
            add: (name) => this.classNames.add(name),
            remove: (name) => this.classNames.delete(name),
            contains: (name) => this.classNames.has(name),
            toggle: (name, force) => {
                if (force) this.classNames.add(name);
                else this.classNames.delete(name);
            },
        };
    }

    removeAttribute(name) {
        delete this.attributes[name];
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }
}

/** Load the shared controller against the same element contract as index.html. */
function createHarness() {
    const elements = {
        dataControlStatusBanner: new FakeElement(),
        dataControlStatusBannerMessage: new FakeElement(),
        dataControlStatusBannerProgress: new FakeElement(),
        dataControlStatusBannerBar: new FakeElement(),
        spinner: new FakeElement(),
    };
    elements.dataControlStatusBanner.hidden = true;

    const context = {
        document: {
            getElementById: (id) => elements[id] || null,
            querySelector: (selector) => (
                selector === '#dataControlStatusBanner .dc-status-banner__spinner'
                    ? elements.spinner
                    : null
            ),
        },
        window: {},
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'dataControlStatusBanner.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'dataControlStatusBanner.js' });
    return { controller: context.window.dataControlStatusBanner, elements };
}

test('shared banner supports determinate and indeterminate operation progress', () => {
    const { controller, elements } = createHarness();

    controller.show('Uploading example.pdf...', {
        owner: 'workspace-file-upload',
        busy: true,
        percent: 42.4,
    });
    assert.equal(elements.dataControlStatusBanner.hidden, false);
    assert.equal(elements.dataControlStatusBanner.attributes['aria-busy'], 'true');
    assert.equal(elements.dataControlStatusBannerMessage.textContent, 'Uploading example.pdf...');
    assert.equal(elements.dataControlStatusBannerBar.style.width, '42.4%');
    assert.equal(elements.dataControlStatusBannerProgress.attributes['aria-valuenow'], '42');
    assert.equal(elements.spinner.hidden, false);

    controller.show('Importing...', {
        owner: 'data-controls',
        busy: true,
        indeterminate: true,
    });
    assert.equal(elements.dataControlStatusBanner.classNames.has('dc-status-banner--indeterminate'), true);
    assert.equal(elements.dataControlStatusBannerProgress.attributes['aria-valuenow'], undefined);

    assert.equal(controller.hide('workspace-file-upload'), false, 'stale owner must not hide newer work');
    assert.equal(elements.dataControlStatusBanner.hidden, false);
    assert.equal(controller.hide('data-controls'), true);
    assert.equal(elements.dataControlStatusBanner.hidden, true);
});

test('workspace uploads use the shared banner and the custom banner is removed', () => {
    const frontendRoot = path.resolve(__dirname, '../..');
    const filesSource = fs.readFileSync(path.join(frontendRoot, 'js/chat/files.js'), 'utf8');
    const filesStyles = fs.readFileSync(path.join(frontendRoot, 'css/chat/files.css'), 'utf8');
    const indexSource = fs.readFileSync(path.join(frontendRoot, 'index.html'), 'utf8');

    assert.match(filesSource, /dataControlStatusBanner\?\.show/);
    assert.match(filesSource, /dataControlStatusBanner\?\.hide/);
    assert.doesNotMatch(filesSource, /getElementById\(['"]uploadProgress['"]\)/);
    assert.doesNotMatch(filesStyles, /\.upload-progress(?:\s|\{|\.)/);
    assert.doesNotMatch(indexSource, /id="uploadProgress"/);
    assert.equal((indexSource.match(/id="dataControlStatusBanner"/g) || []).length, 1);
});
