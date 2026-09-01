const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const frontendRoot = path.join(__dirname, '..', '..');
const rendererPath = path.join(frontendRoot, 'js', 'common', 'createEditFormRenderer.js');
const formsPath = path.join(__dirname, 'workspaceCreateEditForms.js');
const indexPath = path.join(frontendRoot, 'index.html');

/** Execute the dynamic Workspace form definitions against their minimal DOM contract. */
function renderWorkspaceForms() {
    const mounted = {
        workspaceSectionSkills: '',
        workspaceSectionPrompts: '',
        workspaceSectionConnections: '',
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

test('Workspace create and edit pages mount before their behavior modules', () => {
    const index = fs.readFileSync(indexPath, 'utf8');
    const formsScript = index.indexOf('/js/chat/workspaceCreateEditForms.js');

    assert.ok(index.indexOf('/js/common/createEditFormRenderer.js') < formsScript);
    for (const behaviorScript of [
        '/js/chat/skills.js',
        '/js/chat/workspace.js',
    ]) {
        assert.ok(formsScript < index.indexOf(behaviorScript), `${behaviorScript} must load after the forms`);
    }
    assert.doesNotMatch(index, /id="(?:skillsContentCreate|skillsContentEdit|promptLibraryEditorContent)"/);
});

test('the shared renderer produces all Workspace editor surfaces end to end', () => {
    const mounted = renderWorkspaceForms();
    const skills = mounted.workspaceSectionSkills;
    const prompts = mounted.workspaceSectionPrompts;

    assert.match(skills, /class="projects-content" id="skillsContentCreate"/);
    assert.match(skills, /class="projects-content" id="skillsContentEdit"/);
    assert.match(skills, /id="skillIconPicker"/);
    assert.match(skills, /id="skillEditAssetsBtn"/);
    assert.match(skills, /id="skillMetadataInput"[^>]+aria-describedby="skillMetadataHint skillMetadataFilesHint skillMetadataError"[^>]+aria-invalid="false"/);
    assert.match(skills, /id="skillMetadataError"[^>]+hidden[^>]+aria-hidden="true"/);
    assert.match(skills, /id="skillEditMetadataInput"[^>]+aria-describedby="skillEditMetadataHint skillEditMetadataError"[^>]+aria-invalid="false"/);
    assert.match(skills, /id="skillEditMetadataError"[^>]+hidden[^>]+aria-hidden="true"/);
    assert.match(prompts, /id="promptLibraryEditorContent"/);
    assert.match(prompts, /id="promptEditorTitleInput"[^>]+required/);
});

test('all generated Workspace editor IDs remain unique', () => {
    const markup = Object.values(renderWorkspaceForms()).join('');
    const ids = [...markup.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
    const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index);

    assert.ok(ids.length > 30, 'expected the complete Skills and Prompt surfaces');
    assert.deepEqual([...new Set(duplicates)], []);
});
