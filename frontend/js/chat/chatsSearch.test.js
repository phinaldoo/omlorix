const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const INDEX_PATH = path.join(__dirname, '../../index.html');
const I18N_ROOT = path.join(__dirname, '../../i18n');
const CHAT_SEARCH_PATH = path.join(__dirname, 'chatsSearch.js');
const CHAT_SEARCH_CSS_PATH = path.join(__dirname, '../../css/chat/chatsSearchPage.css');
const PROJECTS_CSS_PATH = path.join(__dirname, '../../css/chat/projects.css');

test('chat search page styles live in their dedicated, page-scoped stylesheet', () => {
    const markup = fs.readFileSync(INDEX_PATH, 'utf8');
    const source = fs.readFileSync(CHAT_SEARCH_PATH, 'utf8');
    const chatSearchCss = fs.readFileSync(CHAT_SEARCH_CSS_PATH, 'utf8');
    const projectsCss = fs.readFileSync(PROJECTS_CSS_PATH, 'utf8');

    assert.match(markup, /href="\/css\/chat\/chatsSearchPage\.css"/);
    assert.equal((markup.match(/class="chats-search-input-row"/g) || []).length, 1);
    assert.match(markup, /class="workspace-skills-search-row"/);
    assert.match(chatSearchCss, /#chatsSearchContainer \.chats-search-results\s*\{/);
    assert.match(chatSearchCss, /#chatsSearchContainer \.chats-search-result-highlight\s*\{/);
    assert.doesNotMatch(projectsCss, /chats-search-|#chatsSearchContainer/);
    assert.doesNotMatch(source, /chats-search-result-highlight"\s+style=/);
});

test('chat search uses a dedicated empty-account message', () => {
    const markup = fs.readFileSync(INDEX_PATH, 'utf8');
    const noResultsState = markup.match(
        /id="chatsSearchEmptyNoResults"[\s\S]*?<\/div>/
    )?.[0] || '';
    const noChatsState = markup.match(
        /id="chatsSearchEmptyNoChats"[\s\S]*?<\/div>/
    )?.[0] || '';

    assert.match(noResultsState, /data-i18n="chats_search_empty_title"/);
    assert.match(noResultsState, /data-i18n="chats_search_empty_subtitle"/);
    assert.match(noChatsState, /data-i18n="chats_search_no_chats_title"/);
    assert.match(noChatsState, /data-i18n="chats_search_no_chats_subtitle"/);
    assert.doesNotMatch(noChatsState, /data-i18n="chats_search_empty_/);
});

test('chat results expose a separate keyboard-operable open action', () => {
    const source = fs.readFileSync(CHAT_SEARCH_PATH, 'utf8');

    assert.match(source, /contentContainer = document\.createElement\('button'\)/);
    assert.match(source, /chats-search-result-open/);
    assert.match(source, /\['ArrowDown', 'ArrowUp', 'Home', 'End'\]/);
    assert.match(source, /input\?\.addEventListener\('keydown', focusFirstResult\)/);
    assert.match(source, /resultsHost\?\.addEventListener\('keydown', handleResultNavigation\)/);
});

test('every locale translates the empty-account chat search message', () => {
    const localeDirectories = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    for (const locale of localeDirectories) {
        const dictionaryPath = path.join(I18N_ROOT, locale, 'index.json');
        const dictionary = JSON.parse(fs.readFileSync(dictionaryPath, 'utf8'));

        assert.equal(
            typeof dictionary.chats_search_no_chats_title,
            'string',
            `${locale} must translate chats_search_no_chats_title`
        );
        assert.ok(
            dictionary.chats_search_no_chats_title.trim(),
            `${locale} chats_search_no_chats_title must not be empty`
        );
        assert.equal(
            typeof dictionary.chats_search_no_chats_subtitle,
            'string',
            `${locale} must translate chats_search_no_chats_subtitle`
        );
        assert.ok(
            dictionary.chats_search_no_chats_subtitle.trim(),
            `${locale} chats_search_no_chats_subtitle must not be empty`
        );
    }
});
