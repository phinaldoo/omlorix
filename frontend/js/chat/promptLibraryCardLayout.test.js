const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const CHAT_DIRECTORY = __dirname;
const WORKSPACE_SOURCE = fs.readFileSync(path.join(CHAT_DIRECTORY, 'workspace.js'), 'utf8');
const AGENTS_SOURCE = fs.readFileSync(path.join(CHAT_DIRECTORY, 'agents.js'), 'utf8');
const WORKSPACE_LIBRARY_CSS = fs.readFileSync(
    path.join(CHAT_DIRECTORY, '..', '..', 'css', 'chat', 'workspace-library.css'),
    'utf8',
);

test('user prompt cards place updated metadata before right-aligned actions', () => {
    assert.match(WORKSPACE_SOURCE, /card\.className = 'prompt-library-card user-prompt-card'/);
    assert.match(
        WORKSPACE_SOURCE,
        /prompt-library-card-content[\s\S]*prompt-library-card-updated[\s\S]*prompt-library-card-actions/,
    );
    assert.match(
        WORKSPACE_SOURCE,
        /formatPromptUpdatedAt\(prompt\.updated_at\)\s*\|\| this\.formatPromptUpdatedAt\(prompt\.created_at\)/,
    );
    assert.match(
        WORKSPACE_LIBRARY_CSS,
        /\.user-prompt-card > \.prompt-library-card-actions,[\s\S]*?\.agent-library-card > \.prompt-library-card-actions\s*\{[\s\S]*?justify-content: flex-end;/,
    );
});

test('agent cards use a dedicated right-aligned action row', () => {
    assert.match(AGENTS_SOURCE, /card\.className = 'prompt-library-card agent-library-card'/);
    assert.match(
        WORKSPACE_LIBRARY_CSS,
        /\.agent-library-card > \.prompt-library-card-actions\s*\{[\s\S]*?justify-content: flex-end;/,
    );
});

test('agent card identity keeps icon, custom name, and base model in one centered row', () => {
    assert.match(
        AGENTS_SOURCE,
        /agent-library-card-identity[\s\S]*agent-library-card-icon[\s\S]*prompt-library-card-title[\s\S]*agent-library-card-model/,
    );
    assert.match(
        WORKSPACE_LIBRARY_CSS,
        /\.prompt-library-card-header > div\.agent-library-card-identity\s*\{[\s\S]*?flex-direction: row;[\s\S]*?align-items: center;[\s\S]*?flex-wrap: nowrap;/,
    );
});

test('agent cards resolve the base model name before the initial render', () => {
    assert.match(
        AGENTS_SOURCE,
        /function getBaseModelLabel\(baseModelId\)[\s\S]*?model\?\.name \|\| t\('agents_base_model_unknown'/,
    );
    assert.doesNotMatch(
        AGENTS_SOURCE,
        /return model\?\.name \|\| baseModelId/,
    );
    assert.match(
        AGENTS_SOURCE,
        /AgentsState\.initialized = true;[\s\S]*?try \{[\s\S]*?await loadReferenceData\(\);[\s\S]*?catch \(error\) \{[\s\S]*?AgentsState\.baseModels = \[\];[\s\S]*?\}[\s\S]*?await loadAgents\(\);/,
    );
});

test('agent cards never expose instructions or updated timestamps', () => {
    const renderListSource = AGENTS_SOURCE.slice(
        AGENTS_SOURCE.indexOf('function renderList()'),
        AGENTS_SOURCE.indexOf('function closeAgentModelDropdown()'),
    );
    assert.doesNotMatch(renderListSource, /agent\?\.instruction|agent\.instruction/);
    assert.doesNotMatch(renderListSource, /prompt-library-card-description/);
    assert.doesNotMatch(renderListSource, /prompt-library-card-content/);
    assert.doesNotMatch(renderListSource, /prompt-library-card-updated/);
});

test('every locale translates prompt revision and conflict controls', () => {
    const i18nDirectory = path.join(CHAT_DIRECTORY, '..', '..', 'i18n');
    const locales = fs.readdirSync(i18nDirectory);

    for (const locale of locales) {
        const indexPath = path.join(i18nDirectory, locale, 'index.json');
        if (!fs.existsSync(indexPath)) continue;
        const translations = JSON.parse(fs.readFileSync(indexPath, 'utf8'));
        [
            'prompt_library_updated_prefix',
            'prompt_library_updated_by',
            'prompt_editor_revision_meta',
            'prompt_editor_unknown_editor',
            'prompt_conflict_title',
            'prompt_conflict_keep_draft',
        ].forEach((key) => {
            assert.ok(translations[key], `expected ${key} for ${locale}`);
        });
    }
});
