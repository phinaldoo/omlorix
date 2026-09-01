const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractFunction(source, functionName) {
  const start = source.indexOf(`function ${functionName}(`);
  assert.notEqual(start, -1, `expected ${functionName} in pullToRefresh.js`);

  let signatureDepth = 0;
  let bodyStart = -1;
  for (let index = start; index < source.length; index += 1) {
    const char = source[index];
    if (char === '(') {
      signatureDepth += 1;
    } else if (char === ')') {
      signatureDepth -= 1;
    } else if (char === '{' && signatureDepth === 0) {
      bodyStart = index;
      break;
    }
  }
  assert.notEqual(bodyStart, -1, `expected ${functionName} body`);

  let depth = 0;
  for (let index = bodyStart; index < source.length; index += 1) {
    const char = source[index];
    if (char === '{') {
      depth += 1;
    } else if (char === '}') {
      depth -= 1;
      if (depth === 0) {
        return source.slice(start, index + 1);
      }
    }
  }

  throw new Error(`Could not extract ${functionName}`);
}

class FakeElement {
  constructor({ ignored = false } = {}) {
    this.ignored = ignored;
  }

  closest(selector) {
    return selector === '[data-pull-refresh-ignore]' && this.ignored ? this : null;
  }
}

function loadPullToRefreshHelpers() {
  const source = fs.readFileSync(path.join(__dirname, 'pullToRefresh.js'), 'utf8');
  const chatBoxInput = { value: '' };
  const writeCalls = [];

  const context = {
    document: {
      getElementById(id) {
        if (id === 'chatBoxInput') return chatBoxInput;
        return null;
      },
    },
    window: {
      writeChatInputDraft: (value) => writeCalls.push(value),
    },
  };

  vm.runInNewContext(
    [
      extractFunction(source, 'persistDraftIfNeeded'),
      'this.persistDraftIfNeeded = persistDraftIfNeeded;',
    ].join('\n\n'),
    context,
    { filename: 'pullToRefresh.js' },
  );

  return {
    chatBoxInput,
    persistDraftIfNeeded: context.persistDraftIfNeeded,
    writeCalls,
  };
}

test('pull-to-refresh uses the shared chat draft writer', () => {
  const { chatBoxInput, persistDraftIfNeeded, writeCalls } = loadPullToRefreshHelpers();

  chatBoxInput.value = 'Writer draft';
  persistDraftIfNeeded();

  assert.deepEqual(writeCalls, ['Writer draft']);
});

test('pull-to-refresh ignores gestures from the mobile model selector and while it is open', () => {
  const source = fs.readFileSync(path.join(__dirname, 'pullToRefresh.js'), 'utf8');
  const context = { Element: FakeElement };

  vm.runInNewContext(
    [
      extractFunction(source, 'isRefreshGestureBlocked'),
      'this.isRefreshGestureBlocked = isRefreshGestureBlocked;',
    ].join('\n\n'),
    context,
    { filename: 'pullToRefresh.js' },
  );

  const closedHeader = { classList: { contains: () => false } };
  const openHeader = { classList: { contains: (name) => name === 'model-select-open' } };

  assert.equal(context.isRefreshGestureBlocked(new FakeElement({ ignored: true }), closedHeader), true);
  assert.equal(context.isRefreshGestureBlocked(new FakeElement(), openHeader), true);
  assert.equal(context.isRefreshGestureBlocked(new FakeElement(), closedHeader), false);
});
