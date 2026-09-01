const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const menuStyle = fs.readFileSync(path.join(__dirname, '../../css/login/menu.css'), 'utf8');
const loginStyle = fs.readFileSync(path.join(__dirname, '../../css/login/style.css'), 'utf8');

test('login settings menu stays on the logical viewport edge in RTL layouts', () => {
    assert.match(menuStyle, /\.dropdown-menu\s*\{[^}]*inset-inline-end:\s*0;/u);
    assert.match(menuStyle, /width:\s*min\(220px,\s*calc\(100vw - 32px\)\);/u);
    assert.match(menuStyle, /html\[dir="rtl"\] \.dropdown-menu\s*\{[^}]*transform-origin:\s*top left;/u);
    assert.match(loginStyle, /\.login-layout\.design-centered \.login-header\s*\{[^}]*inset-inline-end:\s*0;/u);
    assert.match(loginStyle, /\.login-layout\.design-glass \.login-header\s*\{[^}]*inset-inline-end:\s*0;/u);
});
