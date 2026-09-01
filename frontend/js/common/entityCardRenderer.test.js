const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'entityCardRenderer.js'), 'utf8');

test('entity cards activate from the full card surface without hijacking controls', () => {
    assert.match(source, /card\.addEventListener\('click', async \(event\) => \{/);
    assert.match(
        source,
        /event\.target\.closest\('\.entity-card-primary-action, \.project-ellipsis, \.select-dropdown'\)/,
    );
    assert.match(source, /primaryAction\?\.addEventListener\('click', async \(event\) => \{/);
});
