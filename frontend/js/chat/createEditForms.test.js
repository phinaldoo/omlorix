const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const frontendRoot = path.join(__dirname, '..', '..');
const rendererPath = path.join(frontendRoot, 'js', 'common', 'createEditFormRenderer.js');
const formsPath = path.join(__dirname, 'createEditForms.js');
const indexPath = path.join(frontendRoot, 'index.html');

/** Execute both deferred form scripts against the minimal DOM API they use. */
function renderCreateEditForms() {
    const mounted = {
        automationsContainer: '',
        projectsContainer: '',
    };
    const containers = Object.fromEntries(Object.keys(mounted).map((id) => [id, {
        insertAdjacentHTML(position, html) {
            assert.equal(position, 'beforeend');
            mounted[id] += html;
        },
    }]));
    const document = {
        getElementById(id) {
            return containers[id] || null;
        },
    };
    const window = { document };
    const context = vm.createContext({ window });

    vm.runInContext(fs.readFileSync(rendererPath, 'utf8'), context, { filename: rendererPath });
    vm.runInContext(fs.readFileSync(formsPath, 'utf8'), context, { filename: formsPath });
    return mounted;
}

/** Load the shared renderer with a task-specific fake browser global. */
function loadRenderer(document, windowOverrides = {}) {
    const window = { document, ...windowOverrides };
    const context = vm.createContext({ window });
    vm.runInContext(fs.readFileSync(rendererPath, 'utf8'), context, { filename: rendererPath });
    return window.CreateEditFormRenderer;
}

test('late-rendered translated elements use the active dictionary immediately', () => {
    const renderer = loadRenderer({ getElementById() { return null; } }, {
        getTranslation(key, fallback) {
            return key === 'common_cancel' ? 'Abbrechen' : fallback;
        },
    });

    const markup = renderer.renderActions({
        className: 'projects-create-buttons',
        buttons: [{ id: 'cancelBtn', className: 'om-button border', key: 'common_cancel', fallback: 'Cancel' }],
    });

    assert.match(markup, /data-i18n="common_cancel">Abbrechen<\/button>/);
});

test('Project and Automation forms are mounted dynamically before their behavior scripts', () => {
    const index = fs.readFileSync(indexPath, 'utf8');
    const rendererScript = index.indexOf('/js/common/createEditFormRenderer.js');
    const formsScript = index.indexOf('/js/chat/createEditForms.js');
    const projectsScript = index.indexOf('/js/chat/projects.js');
    const automationsScript = index.indexOf('/js/chat/automations.js');

    assert.ok(rendererScript >= 0);
    assert.ok(rendererScript < formsScript);
    assert.ok(formsScript < projectsScript);
    assert.ok(formsScript < automationsScript);
    assert.doesNotMatch(index, /id="automationsContent(?:Create|Edit)Automation"/);
    assert.doesNotMatch(index, /id="projectsContent(?:Create|Edit)Project"/);
});

test('the shared renderer produces complete accessible create and edit surfaces', () => {
    const mounted = renderCreateEditForms();
    const automations = mounted.automationsContainer;
    const projects = mounted.projectsContainer;

    assert.match(automations, /class="projects-content" id="automationsContentCreateAutomation"/);
    assert.match(automations, /class="projects-content" id="automationsContentEditAutomation"/);
    assert.match(automations, /id="automationConnectionsSelect"\s+role="group" aria-labelledby="automationConnectionsLabelCreate"/);
    assert.match(automations, /id="automationEditConnectionsSelect"\s+role="group" aria-labelledby="automationConnectionsLabelEdit"/);
    assert.match(automations, /id="automationScheduleRules" aria-describedby="automationScheduleError" aria-invalid="false"/);
    assert.match(automations, /id="automationEditScheduleRules" aria-describedby="automationEditScheduleError" aria-invalid="false"/);
    assert.match(automations, /id="automationActiveToggle"[^>]+aria-labelledby="automationActiveTitle" aria-describedby="automationActiveDescription"/);
    assert.match(automations, /id="automationEditActiveToggle"[^>]+aria-labelledby="automationEditActiveTitle" aria-describedby="automationEditActiveDescription"/);
    assert.match(automations, /id="automationIconSvgPanel"[^>]+role="group"[^>]+aria-label="Automation icon type"/);
    assert.match(automations, /class="select-dropdown svg-select-dropdown" id="automationIconDropdown"/);
    assert.match(automations, /class="om-button border"\s+id="automationIconCancelBtn"/);
    assert.match(automations, /class="om-button border submit"\s+id="automationIconSaveBtn"/);
    assert.doesNotMatch(automations, /svg-select-dropdown-button-row-btn/);
    assert.doesNotMatch(automations, /emoji/i);
    assert.match(automations, /id="confirmCreateAutomationBtn"/);
    assert.match(automations, /id="saveAutomationChangesBtn"/);

    assert.match(projects, /class="projects-content" id="projectsContentCreateProject"/);
    assert.match(projects, /class="projects-content" id="projectsContentEditProject"/);
    assert.match(projects, /id="projectSeparateMemoryToggle" aria-describedby="projectSeparateMemoryToggleDescription" aria-labelledby="projectSeparateMemoryToggleLabel"/);
    assert.match(projects, /id="projectEditSeparateMemoryToggle" aria-describedby="projectEditSeparateMemoryToggleDescription" aria-labelledby="projectEditSeparateMemoryToggleLabel"/);
    assert.match(projects, /class="select-dropdown svg-select-dropdown" id="projectIconDropdown"/);
    assert.match(projects, /id="confirmCreateProjectBtn"/);
    assert.match(projects, /id="saveProjectChangesBtn"/);
});

test('generated create and edit controls have unique stable IDs', () => {
    const mounted = renderCreateEditForms();
    const markup = Object.values(mounted).join('');
    const ids = [...markup.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
    const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index);

    assert.equal(ids.length, 97);
    assert.deepEqual([...new Set(duplicates)], []);
});

test('mountPages rejects duplicate page definitions before inserting markup', () => {
    let inserted = '';
    const container = {
        insertAdjacentHTML(_position, html) {
            inserted += html;
        },
    };
    const renderer = loadRenderer({
        getElementById(id) {
            return id === 'formHost' ? container : null;
        },
    });

    assert.throws(() => renderer.mountPages({
        containerId: 'formHost',
        pages: [
            { id: 'duplicatePage', title: 'First' },
            { id: 'duplicatePage', title: 'Second' },
        ],
    }), /duplicate page id "duplicatePage"/);
    assert.equal(inserted, '');
});

test('shared single selects expose listbox semantics and keyboard selection', () => {
    /** Create the small EventTarget and attribute contract used by the binder. */
    function makeElement() {
        const classes = new Set();
        const listeners = new Map();
        return {
            attributes: new Map(),
            classList: {
                contains(name) { return classes.has(name); },
                toggle(name, force) {
                    if (force) classes.add(name);
                    else classes.delete(name);
                },
            },
            focused: false,
            tabIndex: -1,
            addEventListener(type, listener) { listeners.set(type, listener); },
            dispatch(type, event) { listeners.get(type)?.(event); },
            focus() { this.focused = true; },
            getAttribute(name) { return this.attributes.get(name) || null; },
            setAttribute(name, value) { this.attributes.set(name, String(value)); },
        };
    }

    const trigger = makeElement();
    const dropdown = makeElement();
    const firstOption = makeElement();
    const selectedOption = makeElement();
    selectedOption.setAttribute('aria-selected', 'true');
    let selectedClicks = 0;
    firstOption.click = () => { selectedClicks += 1; };
    dropdown.querySelectorAll = () => [firstOption, selectedOption];
    const container = {
        querySelector(selector) {
            if (selector === '#selectTrigger') return trigger;
            if (selector === '#selectDropdown') return dropdown;
            return null;
        },
    };
    const renderer = loadRenderer({ getElementById() { return null; } }, {
        requestAnimationFrame(callback) { callback(); },
    });
    const markup = renderer.renderSingleSelect({
        kind: 'skill',
        triggerId: 'selectTrigger',
        dropdownId: 'selectDropdown',
        label: 'Choose skill',
        bodyHtml: '<button type="button" role="option">Skill</button>',
    });

    assert.match(markup, /aria-controls="selectDropdown"/);
    assert.match(markup, /id="selectDropdown" role="listbox" aria-labelledby="selectTrigger"/);

    renderer.bindSingleSelect({ container, triggerId: 'selectTrigger', dropdownId: 'selectDropdown' });
    const keyboardEvent = (key, target) => ({
        key,
        target,
        preventDefault() {},
        stopPropagation() {},
    });

    trigger.dispatch('keydown', keyboardEvent('ArrowDown', trigger));
    assert.equal(trigger.getAttribute('aria-expanded'), 'true');
    assert.equal(selectedOption.focused, true);

    dropdown.dispatch('keydown', keyboardEvent('ArrowDown', selectedOption));
    assert.equal(firstOption.focused, true);
    dropdown.dispatch('keydown', keyboardEvent('Enter', firstOption));
    assert.equal(selectedClicks, 1);

    dropdown.dispatch('keydown', keyboardEvent('Escape', firstOption));
    assert.equal(trigger.getAttribute('aria-expanded'), 'false');
    assert.equal(trigger.focused, true);
});
