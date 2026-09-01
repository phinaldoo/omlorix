const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const chatSkillsSource = fs.readFileSync(path.join(__dirname, 'skills.js'), 'utf8');
const adminSkillsSource = fs.readFileSync(path.join(__dirname, '..', 'admin', 'adminSkills.js'), 'utf8');

test('URL skill imports are not presented as verified marketplace content', () => {
    for (const source of [chatSkillsSource, adminSkillsSource]) {
        assert.doesNotMatch(source, /omlorix-marketplace/);
        assert.doesNotMatch(source, /verifyImportSignature/);
        assert.doesNotMatch(source, /From Omlorix Marketplace/);
        assert.match(source, /Unverified skill import/);
        assert.match(source, /source: 'url_import'/);
    }

    assert.match(chatSkillsSource, /imported_from: 'url_import'/);
    assert.match(chatSkillsSource, /reason: 'imported-from-url'/);
});

test('URL skill import timestamps reject far-future links', () => {
    for (const source of [chatSkillsSource, adminSkillsSource]) {
        assert.match(source, /const maxFutureSkew = 5 \* 60 \* 1000/);
        assert.match(source, /importTime - now > maxFutureSkew/);
    }
});
