const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const frontendRoot = path.join(__dirname, '..', '..', '..');
const indexSource = fs.readFileSync(path.join(frontendRoot, 'index.html'), 'utf8');
const languageSource = fs.readFileSync(path.join(frontendRoot, 'js/common/language.js'), 'utf8');
const elementsStyles = fs.readFileSync(path.join(frontendRoot, 'css/common/elements.css'), 'utf8');

const controls = [
    {
        page: 'memory',
        id: 'userSettingsMemoryEnabledToggle',
        titleId: 'memoryEnabledTitle',
        descriptionId: 'memoryEnabledDescription',
        titleKey: 'workspace_memories_enabled_title',
        descriptionKey: 'workspace_memories_enabled_desc',
    },
    {
        page: 'memory',
        id: 'userSettingsMemoryIncludeContextToggle',
        titleId: 'memoryIncludeContextTitle',
        descriptionId: 'memoryIncludeContextDescription',
        titleKey: 'workspace_memories_include_context_title',
        descriptionKey: 'workspace_memories_include_context_desc',
    },
    {
        page: 'memory',
        id: 'userSettingsMemoryAutoCreateToggle',
        titleId: 'memoryAutoCreateTitle',
        descriptionId: 'memoryAutoCreateDescription',
        titleKey: 'workspace_memories_auto_create_title',
        descriptionKey: 'workspace_memories_auto_create_desc',
    },
    {
        page: 'chat',
        id: 'userSettingsShowMessageNavToggle',
        titleId: 'chatShowMessageNavTitle',
        descriptionId: 'chatShowMessageNavDescription',
        titleKey: 'chat_experience_show_message_nav_title',
        descriptionKey: 'chat_experience_show_message_nav_desc',
    },
    {
        page: 'chat',
        id: 'userSettingsShowModelSettingsToggle',
        titleId: 'chatShowModelSettingsTitle',
        descriptionId: 'chatShowModelSettingsDescription',
        titleKey: 'chat_experience_show_model_settings_title',
        descriptionKey: 'chat_experience_show_model_settings_desc',
    },
    {
        page: 'chat',
        id: 'userSettingsShowAssistantMetadataToggle',
        titleId: 'chatShowAssistantMetadataTitle',
        descriptionId: 'chatShowAssistantMetadataDescription',
        titleKey: 'chat_experience_show_assistant_message_metadata_title',
        descriptionKey: 'chat_experience_show_assistant_message_metadata_desc',
    },
    {
        page: 'chat',
        id: 'userSettingsRenderUserMarkdownToggle',
        titleId: 'chatRenderUserMarkdownTitle',
        descriptionId: 'chatRenderUserMarkdownDescription',
        titleKey: 'chat_experience_render_user_markdown_title',
        descriptionKey: 'chat_experience_render_user_markdown_desc',
    },
    {
        page: 'chat',
        id: 'userSettingsRenderAssistantMarkdownToggle',
        titleId: 'chatRenderAssistantMarkdownTitle',
        descriptionId: 'chatRenderAssistantMarkdownDescription',
        titleKey: 'chat_experience_render_assistant_markdown_title',
        descriptionKey: 'chat_experience_render_assistant_markdown_desc',
    },
    {
        page: 'chat',
        id: 'userSettingsCtrlEnterToSendToggle',
        titleId: 'chatCtrlEnterToSendTitle',
        descriptionId: 'chatCtrlEnterToSendDescription',
        titleKey: 'chat_experience_ctrl_enter_to_send_title',
        descriptionKey: 'chat_experience_ctrl_enter_to_send_desc',
    },
    {
        page: 'chat',
        id: 'userSettingsAlwaysTemporaryToggle',
        titleId: 'chatAlwaysTemporaryTitle',
        descriptionId: 'chatAlwaysTemporaryDescription',
        titleKey: 'chat_experience_temporary_chat_title',
        descriptionKey: 'chat_experience_temporary_chat_desc',
    },
    {
        page: 'chat',
        id: 'userSettingsChatFullWidthToggle',
        titleId: 'chatFullWidthTitle',
        descriptionId: 'chatFullWidthDescription',
        titleKey: 'chat_customization_full_width_title',
        descriptionKey: 'chat_customization_full_width_desc',
    },
];

function escapeRegex(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function tagWithId(tagName, id) {
    const matches = indexSource.match(
        new RegExp(`<${tagName}\\b[^>]*\\bid="${escapeRegex(id)}"[^>]*>`, 'gi'),
    ) || [];
    assert.equal(matches.length, 1, `expected exactly one ${tagName}#${id}`);
    return matches[0];
}

function attribute(tag, name) {
    return tag.match(new RegExp(`\\b${escapeRegex(name)}="([^"]*)"`, 'i'))?.[1] || '';
}

function pageSource(page) {
    const start = indexSource.indexOf(`data-us-page="${page}"`);
    assert.notEqual(start, -1, `missing ${page} settings page`);
    const nextPage = indexSource.indexOf('<div class="us-page', start + 1);
    return indexSource.slice(start, nextPage === -1 ? undefined : nextPage);
}

test('all Chat and Memory toggles reference their visible translated title and description', () => {
    const expectedIdsByPage = new Map([
        ['chat', controls.filter(({ page }) => page === 'chat').map(({ id }) => id).sort()],
        ['memory', controls.filter(({ page }) => page === 'memory').map(({ id }) => id).sort()],
    ]);

    for (const [page, expectedIds] of expectedIdsByPage) {
        const toggleIds = [...pageSource(page).matchAll(/<input\b[^>]*>/gi)]
            .map(([tag]) => ({
                id: attribute(tag, 'id'),
                type: attribute(tag, 'type'),
                classes: attribute(tag, 'class').split(/\s+/),
            }))
            .filter(({ type, classes }) => type === 'checkbox' && classes.includes('toggle-input'))
            .map(({ id }) => id)
            .sort();

        assert.deepEqual(toggleIds, expectedIds, `${page} must not gain an unnamed toggle`);
    }

    for (const control of controls) {
        const input = tagWithId('input', control.id);
        const title = tagWithId('h3', control.titleId);
        const description = tagWithId('p', control.descriptionId);

        assert.equal(attribute(input, 'type'), 'checkbox');
        assert.equal(attribute(input, 'aria-labelledby'), control.titleId);
        assert.equal(attribute(input, 'aria-describedby'), control.descriptionId);
        assert.equal(attribute(title, 'data-i18n'), control.titleKey);
        assert.equal(attribute(description, 'data-i18n'), control.descriptionKey);
        assert.doesNotMatch(input, /\btabindex="-1"|\bdisabled\b/i);

        const labelledToggle = new RegExp(
            `<label\\b[^>]*class="[^"]*\\btoggle-switch\\b[^"]*"[^>]*>\\s*`
            + `<input\\b[^>]*\\bid="${escapeRegex(control.id)}"[^>]*>\\s*`
            + '<span\\b[^>]*class="toggle-slider"[^>]*aria-hidden="true"[^>]*><\\/span>\\s*<\\/label>',
            'i',
        );
        assert.match(indexSource, labelledToggle, `${control.id} must retain native label activation`);
    }
});

test('all supported locales produce unique non-empty names and descriptions for the toggles', () => {
    const supportedLocalesMatch = languageSource.match(/SUPPORTED_LANGS\s*=\s*(\[[^;]+\])/);
    assert.ok(supportedLocalesMatch, 'missing SUPPORTED_LANGS');
    const supportedLocales = JSON.parse(supportedLocalesMatch[1]);

    for (const locale of supportedLocales) {
        const translations = JSON.parse(
            fs.readFileSync(path.join(frontendRoot, 'i18n', locale, 'index.json'), 'utf8'),
        );
        const names = controls.map(({ titleKey }) => String(translations[titleKey] || '').trim());
        const descriptions = controls.map(
            ({ descriptionKey }) => String(translations[descriptionKey] || '').trim(),
        );

        assert.ok(names.every(Boolean), `${locale} has an empty toggle name`);
        assert.equal(new Set(names).size, controls.length, `${locale} toggle names must be unique`);
        assert.ok(descriptions.every(Boolean), `${locale} has an empty toggle description`);
    }
});

test('the native toggles expose a visible keyboard focus indicator', () => {
    assert.match(
        elementsStyles,
        /\.toggle-input:focus-visible\s*\+\s*\.toggle-slider\s*\{[^}]*outline:\s*2px solid var\(--primary-color\);[^}]*outline-offset:\s*2px;/s,
    );
});
