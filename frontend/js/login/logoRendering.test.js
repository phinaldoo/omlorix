const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const scriptPath = path.join(__dirname, 'script.js');

function readLoginScript() {
    return fs.readFileSync(scriptPath, 'utf8');
}

test('custom login logos are rendered as inert image resources instead of inline SVG DOM', () => {
    const source = readLoginScript();

    assert.doesNotMatch(source, /document\.importNode\s*\(/);
    assert.doesNotMatch(source, /DOMParser/);
    assert.doesNotMatch(source, /svgMarkup/);
    assert.doesNotMatch(source, /sanitizeLogoSvgMarkup/);
    assert.match(source, /URL\.createObjectURL\s*\(\s*blob\s*\)/);
    assert.match(source, /document\.createElement\s*\(\s*['"]img['"]\s*\)/);
});
