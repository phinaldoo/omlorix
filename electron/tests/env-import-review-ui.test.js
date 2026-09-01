const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const repositoryRoot = path.join(__dirname, '..', '..');

test('env import review uses a labelled, vertically structured responsive panel', async () => {
  const [html, css, renderer] = await Promise.all([
    fs.readFile(path.join(repositoryRoot, 'electron/renderer/launcher.html'), 'utf8'),
    fs.readFile(path.join(repositoryRoot, 'electron/renderer/launcher.css'), 'utf8'),
    fs.readFile(path.join(repositoryRoot, 'electron/renderer/launcher.js'), 'utf8'),
  ]);

  assert.match(html, /id="envImportReview"[^>]*aria-labelledby="envImportReviewTitle"/);
  assert.match(html, /<dl id="envImportSummary" class="env-import-summary"><\/dl>/);
  assert.match(html, /id="envImportSource" dir="ltr"/);
  assert.match(html, /id="replaceMissingEnvInput" type="checkbox"/);
  assert.match(html, /id="envImportResult"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(css, /\.env-import-review\s*\{[\s\S]*?flex-direction:\s*column/);
  assert.match(css, /\.env-import-result\s*\{[\s\S]*?border-left:\s*4px solid var\(--success\)/);
  assert.match(css, /\.env-import-mode\.opt:has\(input:checked\)/);
  assert.match(css, /\.env-import-summary\s*\{[\s\S]*?grid-template-columns:\s*repeat\(5/);
  assert.match(css, /\.env-import-key-list\s*\{[\s\S]*?max-height:\s*158px[\s\S]*?overflow:\s*auto/);
  assert.match(renderer, /keyList\.setAttribute\('role', 'list'\)/);
  assert.match(renderer, /launcherT\('launcher_ui_imported', 'Imported'\)/);
  assert.doesNotMatch(renderer, /omlorix:request-env-import/);
  assert.match(renderer, /function renderEnvImportResult/);
  assert.match(renderer, /if \(!preview\.replacement\) els\.replaceMissingEnvInput\.checked = false/);
  assert.match(renderer, /replaceMissing:\s*Boolean\(selectedEnvImportPreview\(\)\?\.replaceMissing\)/);
  assert.match(renderer, /disabled = state\.busy \|\| !preview\.replacement/);
  assert.match(renderer, /!displayPreview\.replaceMissing && displayPreview\.customKeys\?\.length/);
  assert.match(renderer, /launcher_ui_one_known_key_is_not_in_the_import_file/);
  assert.match(renderer, /launcher_ui_count_known_keys_are_not_in_the_import_file/);
  assert.match(renderer, /launcher_ui_one_line_is_not_a_key_value_assignment/);
  assert.match(renderer, /launcher_ui_count_lines_are_not_key_value_assignments/);
  assert.doesNotMatch(renderer, /\? 'key is' : 'keys are'/);
  assert.doesNotMatch(renderer, /\? 'line is' : 'lines are'/);
});

test('env import summary labels are translated in every launcher locale', () => {
  const translations = require('../renderer/launcher-translations');
  const keys = [
    'launcher_ui_imported',
    'launcher_ui_changed',
    'launcher_ui_new',
    'launcher_ui_unchanged',
    'launcher_ui_the_last_value_is_used_for_these_keys',
    'launcher_ui_import_applied',
    'launcher_ui_import_applied_restart_needed',
    'launcher_ui_import_applied_restart_manually',
    'launcher_ui_import_no_changes',
    'launcher_ui_reset_variables_missing_from_file',
    'launcher_ui_reset_variables_missing_description',
    'launcher_ui_replacement_impact',
    'launcher_ui_merge_impact',
    'launcher_ui_missing_known_keys_will_reset',
    'launcher_ui_missing_known_reset_body',
    'launcher_ui_custom_keys_will_be_removed',
    'launcher_ui_missing_custom_remove_body',
    'launcher_ui_one_known_key_is_not_in_the_import_file',
    'launcher_ui_count_known_keys_are_not_in_the_import_file',
    'launcher_ui_one_line_is_not_a_key_value_assignment',
    'launcher_ui_count_lines_are_not_key_value_assignments',
  ];

  for (const [locale, catalog] of Object.entries(translations.locales)) {
    for (const key of keys) {
      assert.equal(typeof catalog[key], 'string', `${key} is missing for ${locale}`);
      assert.ok(catalog[key].trim(), `${key} is empty for ${locale}`);
    }
    const importGrammar = keys
      .filter((key) => /known_key|line_is|count_lines/.test(key))
      .map((key) => catalog[key])
      .join(' ');
    assert.doesNotMatch(importGrammar, /\b(?:key is|keys are|line is|lines are)\b/i, `${locale} contains English grammar`);
  }
});
