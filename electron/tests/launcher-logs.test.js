const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { logs: logContract } = require('../../cmd/omlorix-server-cli/server-management-contract.json');

const electronRoot = path.join(__dirname, '..');
const rendererRoot = path.join(electronRoot, 'renderer');

test('Console exposes accessible aggregate, service, line, time, and follow controls', async () => {
  const html = await fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8');
  const consoleStart = html.indexOf('<section id="console"');
  const consoleEnd = html.indexOf('</section>', consoleStart);
  const consoleMarkup = html.slice(consoleStart, consoleEnd);

  assert.match(consoleMarkup, /class="console-log-controls" aria-label="Log controls"/);
  assert.match(consoleMarkup, /id="logServiceSelect"[\s\S]*<option value="">All services<\/option>/);
  assert.match(
    consoleMarkup,
    new RegExp(`id="logLinesInput"[^>]*min="${logContract.minimumLines}"[^>]*max="${logContract.maximumLines}"[^>]*value="${logContract.defaultLines}"`),
  );
  assert.match(
    consoleMarkup,
    new RegExp(`id="logSinceInput"[^>]*maxlength="${logContract.maximumTimeBoundLength}"[^>]*aria-describedby="logOptionsHint logControlStatus"`),
  );
  assert.match(consoleMarkup, /id="startLogFollowButton"/);
  assert.match(consoleMarkup, /id="stopLogFollowButton"[^>]*disabled/);
  assert.match(consoleMarkup, /id="logControlStatus" role="status" aria-live="polite"/);
});

test('trusted IPC exposes a cancellable log stream and forwards only scoped events', async () => {
  const [main, preload] = await Promise.all([
    fs.readFile(path.join(electronRoot, 'main.js'), 'utf8'),
    fs.readFile(path.join(electronRoot, 'preload.js'), 'utf8'),
  ]);

  assert.match(main, /handleTrustedIpc\('server:logs',[^\n]*serverManager\.logs\(options\)/);
  assert.match(main, /handleTrustedIpc\('server:logs-follow-start',[\s\S]*serverManager\.startLogFollow\(options\)/);
  assert.match(main, /handleTrustedIpc\('server:logs-follow-stop',[\s\S]*serverManager\.stopLogFollow\(sessionId\)/);
  assert.match(main, /serverManager\.on\('log-follow-output',[^\n]*sendToRenderer\('server:log-follow-output'/);
  assert.match(main, /serverManager\.on\('log-follow-end',[^\n]*sendToRenderer\('server:log-follow-end'/);
  assert.match(preload, /startLogFollow: \(options\) => invokeServerLog\('server:logs-follow-start', options\)/);
  assert.match(preload, /stopLogFollow: \(sessionId\) => invokeServerLog\('server:logs-follow-stop', sessionId\)/);
  assert.match(preload, /onLogFollowOutput:[\s\S]*ipcRenderer\.on\('server:log-follow-output'/);
  assert.match(preload, /onLogFollowEnd:[\s\S]*ipcRenderer\.on\('server:log-follow-end'/);
});

test('renderer diagnostics remain independent from lifecycle busy state and fit narrow layouts', async () => {
  const [source, css] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'launcher.css'), 'utf8'),
  ]);
  const snapshotStart = source.indexOf('async function loadLogSnapshot(');
  const snapshotEnd = source.indexOf('async function startLogFollow(', snapshotStart);
  const snapshotSource = source.slice(snapshotStart, snapshotEnd);

  assert.match(snapshotSource, /window\.omlorixServer\.logs\(options\)/);
  assert.doesNotMatch(snapshotSource, /setBusy\(/);
  assert.match(source, /window\.omlorixServer\.startLogFollow\(options\)/);
  assert.match(source, /window\.omlorixServer\.stopLogFollow\(state\.logFollowSessionId\)/);
  assert.match(source, /logAction \? logDiagnosticsActive\(\) : nextBusy \|\| envBlocked/);
  assert.doesNotMatch(source, /window\.omlorixServer\.logs\(260\)/);
  assert.doesNotMatch(source, /serviceAction\('logs',[^\n]*260/);
  assert.match(css, /@media \(max-width: 720px\)[\s\S]*\.console-log-controls/);
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.console-log-controls/);
});
