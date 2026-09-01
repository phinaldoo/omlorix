const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');

const chatBoxSource = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
const modalSource = readFrontendSource(path.join(__dirname, 'deleteWarningModals.js'), 'utf8');

function sourceBetween(start, end) {
  const startIndex = chatBoxSource.indexOf(start);
  const endIndex = chatBoxSource.indexOf(end, startIndex + start.length);
  assert.notEqual(startIndex, -1, `Missing start marker: ${start}`);
  assert.notEqual(endIndex, -1, `Missing end marker: ${end}`);
  return chatBoxSource.slice(startIndex, endIndex);
}

test('native Google Picker imports and attaches without opening a legacy Drive modal', () => {
  const directImport = sourceBetween(
    'async function importGoogleDriveFileIds',
    'async function importGoogleDriveFilesIntoChat'
  );
  const pickerFlow = sourceBetween(
    'async function importGoogleDriveFilesIntoChat',
    "if (typeof window !== 'undefined')"
  );

  assert.match(pickerFlow, /await nativePicker\.open\(\)/);
  assert.match(pickerFlow, /importGoogleDriveFileIds\(fileIds, \{ attachmentTarget \}\)/);
  assert.match(directImport, /\/api\/v1\/files\/google-drive\/import/);
  assert.match(directImport, /attachImportedFilesToComposer\(imported, attachmentTarget\)/);

  assert.doesNotMatch(chatBoxSource, /openGoogleDriveModal|googleDriveModalState|chatBoxGoogleDriveOverlay/);
  assert.doesNotMatch(modalSource, /chatBoxGoogleDriveOverlay|chatBoxGoogleDriveModal/);
});
