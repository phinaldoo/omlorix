const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const viewport = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, 'mermaid-preview.js'), 'utf8'), viewport);

test('zoom keeps the diagram point under the pointer fixed, including scale limits', () => {
    const state = { scale: 0.5, fitScale: 0.5, x: 80, y: 30, fitted: true };
    const diagramX = (250 - state.x) / state.scale;
    const diagramY = (150 - state.y) / state.scale;
    for (const scale of [1, 2.5, 100, 0.001, 0.5]) {
        viewport.zoomMermaidViewport(state, scale, 250, 150);
        assert.ok(Math.abs(state.x + diagramX * state.scale - 250) < 1e-8);
        assert.ok(Math.abs(state.y + diagramY * state.scale - 150) < 1e-8);
        assert.ok(state.scale >= 0.1 && state.scale <= 8);
        assert.equal(state.fitted, false);
    }
});

test('fit includes large and tall diagrams below the old 25% limit and resets pan', () => {
    for (const [width, height] of [[12000, 800], [400, 15000], [200, 100]]) {
        const state = { width: 320, height: 280, diagramWidth: width, diagramHeight: height, x: -900, y: 300, scale: 8 };
        state.fitScale = viewport.getMermaidViewportFit(state.width, state.height, width, height);
        viewport.fitMermaidViewport(state);
        assert.ok(width * state.scale <= state.width - 48);
        assert.ok(height * state.scale <= state.height - 80);
        assert.ok(state.x >= 24 && state.y >= 24);
        assert.equal(state.fitted, true);
        viewport.zoomMermaidViewport(state, state.fitScale, 0, 0);
        assert.equal(state.scale, state.fitScale);
    }
});
