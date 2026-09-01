(function () {
    'use strict';

    const CHAT_AUTOMATION_TITLE_PREFIX = /^\s*\[Automation\]\s*/i;

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function normalizeChatSource(source) {
        return String(source ?? '').trim().toLowerCase();
    }

    function getChatSource(chat) {
        if (!chat || typeof chat !== 'object') {
            return '';
        }

        const directSource = normalizeChatSource(chat.source);
        if (directSource) {
            return directSource;
        }

        const metaSource = chat.meta && typeof chat.meta === 'object' ? normalizeChatSource(chat.meta.source) : '';
        return metaSource;
    }

    function isAutomationChat(chat) {
        if (getChatSource(chat) === 'automation') {
            return true;
        }

        const title = String(chat?.title ?? '').trim();
        return CHAT_AUTOMATION_TITLE_PREFIX.test(title);
    }

    function normalizeChatTitle(title, fallbackTitle = '') {
        const rawTitle = String(title ?? '').trim();
        const cleanedTitle = rawTitle.replace(CHAT_AUTOMATION_TITLE_PREFIX, '').trim();
        return cleanedTitle || String(fallbackTitle ?? '').trim();
    }

    function getChatDisplayTitle(chat, fallbackTitle = '') {
        return normalizeChatTitle(chat?.title, fallbackTitle);
    }

    function getChatTitleBadgeMarkup(chat) {
        if (!isAutomationChat(chat)) {
            return '';
        }

        const iconMarkup = typeof Icons === 'object' && Icons?.automation ? Icons.automation : '';
        if (!iconMarkup) {
            return '';
        }

        return `<span class="chat-title-badge" aria-hidden="true">${iconMarkup}</span>`;
    }

    function createMarkupElement(markup) {
        if (typeof document === 'undefined') {
            return null;
        }

        const template = document.createElement('template');
        template.innerHTML = String(markup ?? '').trim();
        return template.content.firstElementChild;
    }

    function buildChatTitleMarkup(chat, titleHtml, { fallbackTitle = '' } = {}) {
        const title = getChatDisplayTitle(chat, fallbackTitle);
        const badgeMarkup = getChatTitleBadgeMarkup(chat);
        return `${badgeMarkup}<span class="chat-title-text">${titleHtml ?? escapeHtml(title)}</span>`;
    }

    function setChatTitleElement(titleElement, chat, { fallbackTitle = '' } = {}) {
        if (!titleElement) {
            return '';
        }

        const title = getChatDisplayTitle(chat, fallbackTitle);
        const badgeMarkup = getChatTitleBadgeMarkup(chat);
        titleElement.classList.add('chat-title-with-badge');
        // Rebuild the contents with DOM nodes so the title always stays text-only.
        while (titleElement.firstChild) {
            titleElement.removeChild(titleElement.firstChild);
        }
        const badgeElement = createMarkupElement(badgeMarkup);
        if (badgeElement) {
            titleElement.appendChild(badgeElement);
        }
        const textElement = document.createElement('span');
        textElement.className = 'chat-title-text';
        textElement.textContent = title;
        titleElement.appendChild(textElement);
        titleElement.title = title;
        return title;
    }

    function getChatTitleTextFromElement(titleElement, fallbackTitle = '') {
        if (!titleElement) {
            return String(fallbackTitle ?? '').trim();
        }

        const titleNode = titleElement.querySelector?.('.chat-title-text');
        const rawTitle = String(titleNode?.textContent || titleElement.textContent || '').trim();
        return normalizeChatTitle(rawTitle, fallbackTitle);
    }

    window.ChatTitleUtils = {
        buildChatTitleMarkup,
        getChatDisplayTitle,
        getChatSource,
        getChatTitleBadgeMarkup,
        getChatTitleTextFromElement,
        isAutomationChat,
        normalizeChatTitle,
        setChatTitleElement,
    };
})();
