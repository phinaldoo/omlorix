const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'chatBox/mentions.js'), 'utf8');
const measureSource = source.slice(
    source.indexOf('function syncMentionMenuHeight()'),
    source.indexOf('/** Build the intro'),
);

test('mention height fits above the composer, including a reduced visual viewport', () => {
    for (const [top, viewport, content, expected] of [
        [220, null, 1600, 200],
        [75, null, 1600, 55],
        [600, null, 1600, 430],
        [220, null, 60, 92],
        [300, { offsetTop: 150, height: 250 }, 1600, 130],
    ]) {
        let frame;
        const dropdown = {
            style: {},
            parentElement: { getBoundingClientRect: () => ({ top }) },
        };
        const body = { style: { height: '' } };
        Object.defineProperty(body, 'scrollHeight', {
            get: () => body.style.height === 'auto' ? content : Math.max(430, content),
        });
        const context = {
            skillMentionDropdown: dropdown,
            skillMentionBody: body,
            skillMentionState: { isOpen: true },
            mentionMenuHeightFrame: null,
            requestAnimationFrame: (callback) => { frame = callback; return 1; },
            window: {
                innerHeight: 900,
                visualViewport: viewport,
                getComputedStyle: () => ({ borderTopWidth: '1px', borderBottomWidth: '1px' }),
            },
        };
        vm.runInNewContext(`${measureSource}\nsyncMentionMenuHeight();`, context);
        frame();
        assert.equal(dropdown.style.height, `${expected}px`);
        assert.ok(parseFloat(dropdown.style.height) <= parseFloat(dropdown.style.maxHeight));
        assert.equal(body.style.height, '');
    }
});
