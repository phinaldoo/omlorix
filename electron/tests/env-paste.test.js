const test = require('node:test');
const assert = require('node:assert/strict');

const {
  applyTextPaste,
  clipboardTextFromEvent,
  replaceSelectedText,
} = require('../renderer/env-paste');

function createControl(value, selectionStart, selectionEnd) {
  return {
    value,
    selectionStart,
    selectionEnd,
    events: [],
    setSelectionRange(start, end) {
      this.selectionStart = start;
      this.selectionEnd = end;
    },
    dispatchEvent(event) {
      this.events.push(event.type);
      return true;
    },
  };
}

function createPasteEvent(text) {
  return {
    defaultPrevented: false,
    clipboardData: {
      getData(type) {
        return type === 'text/plain' ? text : '';
      },
    },
    preventDefault() {
      this.defaultPrevented = true;
    },
  };
}

test('replaceSelectedText inserts clipboard text at the current selection', () => {
  const control = createControl('DATABASE_URL=', 13, 13);

  replaceSelectedText(control, 'postgres://example');

  assert.equal(control.value, 'DATABASE_URL=postgres://example');
  assert.equal(control.selectionStart, 31);
  assert.equal(control.selectionEnd, 31);
});

test('applyTextPaste prevents native paste and dispatches input', () => {
  const control = createControl('abc123', 3, 6);
  const event = createPasteEvent('XYZ');

  assert.equal(applyTextPaste(event, control), true);

  assert.equal(event.defaultPrevented, true);
  assert.equal(control.value, 'abcXYZ');
  assert.deepEqual(control.events, ['input']);
});

test('clipboardTextFromEvent falls back when plain text is unavailable', () => {
  const event = {
    clipboardData: {
      getData(type) {
        return type === 'text' ? 'legacy clipboard text' : '';
      },
    },
  };

  assert.equal(clipboardTextFromEvent(event), 'legacy clipboard text');
});
