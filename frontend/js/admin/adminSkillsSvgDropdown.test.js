const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const frontendRoot = path.join(__dirname, '..', '..');

test('admin skill SVG selectors reuse the shared picker, dropdown, and buttons', () => {
    const markup = fs.readFileSync(path.join(frontendRoot, 'admin.html'), 'utf8');
    const behavior = fs.readFileSync(path.join(__dirname, 'adminSkills.js'), 'utf8');
    const styles = fs.readFileSync(path.join(frontendRoot, 'css', 'common', 'svgSelect.css'), 'utf8');

    assert.ok(markup.indexOf('/css/common/elementsNew.css') < markup.indexOf('/css/common/svgSelect.css'));
    assert.ok(markup.indexOf('/js/common/workspaceIcons.js') < markup.indexOf('/js/admin/adminSkills.js'));

    for (const mode of ['Create', 'Edit']) {
        assert.match(
            markup,
            new RegExp(`class="select-dropdown svg-select-dropdown" id="adminSkill${mode}IconDropdown"`),
        );
    }
    assert.match(markup, /class="om-button border" id="adminSkillCreateIconCancel"/);
    assert.match(markup, /class="om-button border submit" id="adminSkillCreateIconSave"/);
    assert.doesNotMatch(markup, /svg-select-dropdown-button-row/);
    assert.doesNotMatch(styles, /svg-select-dropdown-button-row/);
    assert.equal((behavior.match(/createWorkspaceIconPicker\(/g) || []).length, 1);
    assert.doesNotMatch(behavior, /function (?:initIconGrid|initColorRow|selectSvg|selectColor|updateSelections|positionSkillSvgDropdown)/);
    assert.doesNotMatch(behavior, /createDropdownController|positionDropdownAtTrigger/);
});
