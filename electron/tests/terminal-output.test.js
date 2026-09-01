const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const terminalOutput = require('../renderer/terminal-output');
const { terminalComposeArgs } = require('../server-manager');

/** Make the minimal DOM-like surface required by the text renderer. */
function outputElement() {
  return { textContent: '', scrollTop: 0, scrollHeight: 100 };
}

test('carriage-return download progress replaces one line across process chunks', () => {
  const element = outputElement();

  terminalOutput.append(element, 'Downloading 10%\r');
  terminalOutput.append(element, 'Downloading 55%\r');
  terminalOutput.append(element, 'Downloading 100%\nDone');

  assert.equal(element.textContent, 'Downloading 100%\nDone');
});

test('CRLF remains a normal newline and stream-only whitespace is not duplicated', () => {
  const element = outputElement();

  terminalOutput.append(element, 'first\r');
  terminalOutput.append(element, '\n');
  terminalOutput.append(element, 'second');

  assert.equal(element.textContent, 'first\nsecond');
});

test('split ANSI sequences redraw Docker-style multi-line progress in place', () => {
  const element = outputElement();

  terminalOutput.append(element, '[+] Running 0/2\n - api Pulling\n - web Pulling\n');
  terminalOutput.append(element, '\x1b[3A\x1b[2K[+] Running 1/2\n');
  terminalOutput.append(element, '\x1b[2K - api Pulled\n\x1b[');
  terminalOutput.append(element, '2K - web Pulling\n');

  assert.equal(element.textContent, '[+] Running 1/2\n - api Pulled\n - web Pulling\n');
  assert.doesNotMatch(element.textContent, /\x1b/);
});

test('launcher messages start after an unfinished streamed progress row', () => {
  const element = outputElement();

  terminalOutput.append(element, 'Pulling 75%\r');
  terminalOutput.append(element, 'Pulling 100%');
  terminalOutput.append(element, '> Start finished', { separate: true });

  assert.equal(element.textContent, 'Pulling 100%\n> Start finished');
});

test('clear drops visible output and pending parser state', () => {
  const element = outputElement();

  terminalOutput.append(element, 'old\x1b[');
  terminalOutput.clear(element);
  terminalOutput.append(element, 'new');

  assert.equal(element.textContent, 'new');
});

test('high-volume followed output retains only the configured readable tail', () => {
  const element = outputElement();

  terminalOutput.append(element, 'one\ntwo\nthree\nfour\nfive', { maxLines: 3 });
  assert.equal(element.textContent, 'three\nfour\nfive');

  terminalOutput.append(element, '-1234567890', { maxCharacters: 10 });
  assert.equal(element.textContent.length, 10);
  assert.equal(element.textContent, '1234567890');
});

test('launcher-streamed Compose commands request compact terminal progress', () => {
  const args = terminalComposeArgs(['compose', '--env-file', '/server/.env', 'pull']);

  assert.deepEqual(args, [
    'compose',
    '--ansi',
    'always',
    '--progress',
    'tty',
    '--env-file',
    '/server/.env',
    'pull',
  ]);
  assert.deepEqual(terminalComposeArgs(['info']), ['info']);
});

test('structured operation output bypasses TTY progress arguments', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'server-manager.js'), 'utf8');

  assert.match(
    source,
    /const spawnArgs = captureStructuredOutput \? args : terminalComposeArgs\(args\)/,
  );
  assert.match(source, /spawn\(dockerExecutable, spawnArgs,/);
});
