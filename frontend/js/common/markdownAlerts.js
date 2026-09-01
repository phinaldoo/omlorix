(function (root, factory) {
    'use strict';

    const api = factory(root);

    // Keep the plugin name aligned with the other markdown-it globals used by
    // Omlorix while also exposing the icon enhancer for sanitized render paths.
    root.markdownitAlerts = api.plugin;
    root.ChatMarkdownAlerts = api;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
    'use strict';

    const ALERT_MARKER_PATTERN = /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*(?:\n|$)/i;
    const ALERT_TYPES = Object.freeze({
        note: Object.freeze({
            translationKey: 'markdown_alert_note',
            fallbackLabel: 'Note',
            iconKey: 'info',
        }),
        tip: Object.freeze({
            translationKey: 'markdown_alert_tip',
            fallbackLabel: 'Tip',
            iconKey: 'markdownAlertTip',
        }),
        important: Object.freeze({
            translationKey: 'markdown_alert_important',
            fallbackLabel: 'Important',
            iconKey: 'markdownAlertImportant',
        }),
        warning: Object.freeze({
            translationKey: 'markdown_alert_warning',
            fallbackLabel: 'Warning',
            iconKey: 'warning',
        }),
        caution: Object.freeze({
            translationKey: 'markdown_alert_caution',
            fallbackLabel: 'Caution',
            iconKey: 'markdownAlertCaution',
        }),
    });

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /** Resolve the visible alert title at render time so the active locale wins. */
    function getAlertLabel(type) {
        const definition = ALERT_TYPES[type];
        if (!definition) return '';
        if (typeof root.getTranslation === 'function') {
            return root.getTranslation(definition.translationKey, definition.fallbackLabel);
        }
        return definition.fallbackLabel;
    }

    /**
     * Remove the marker and its following Markdown line break from the first
     * inline token. The remaining child tokens retain bold, links, code spans,
     * and every other inline feature already parsed by markdown-it.
     */
    function removeMarkerFromInlineToken(inlineToken, markerMatch) {
        const children = Array.isArray(inlineToken.children)
            ? inlineToken.children.slice()
            : [];

        if (children[0]?.type === 'text') {
            children.shift();
        }
        if (children[0]?.type === 'softbreak' || children[0]?.type === 'hardbreak') {
            children.shift();
        }

        // markdown-it can retain empty text tokens around formatted content.
        // Removing only edge empties keeps meaningful whitespace inside links
        // and emphasis intact while making the empty-paragraph check reliable.
        while (children[0]?.type === 'text' && !children[0].content) {
            children.shift();
        }
        while (children.at(-1)?.type === 'text' && !children.at(-1).content) {
            children.pop();
        }

        inlineToken.content = String(inlineToken.content || '').slice(markerMatch[0].length);
        inlineToken.children = children;
        return children.length === 0;
    }

    /**
     * Convert GitHub alert blockquotes after inline parsing. Working with the
     * token stream avoids false positives in fenced code, inline code, and
     * ordinary prose while preserving the original Markdown in storage.
     */
    function transformAlertTokens(state) {
        const tokens = state.tokens;

        for (let index = 0; index < tokens.length; index += 1) {
            const blockquoteOpen = tokens[index];
            const paragraphOpen = tokens[index + 1];
            const inlineToken = tokens[index + 2];
            const paragraphClose = tokens[index + 3];

            if (
                blockquoteOpen?.type !== 'blockquote_open'
                || paragraphOpen?.type !== 'paragraph_open'
                || inlineToken?.type !== 'inline'
                || paragraphClose?.type !== 'paragraph_close'
                || paragraphOpen.level !== blockquoteOpen.level + 1
            ) {
                continue;
            }

            const markerMatch = String(inlineToken.content || '').match(ALERT_MARKER_PATTERN);
            if (!markerMatch) continue;

            const type = markerMatch[1].toLowerCase();
            if (!ALERT_TYPES[type]) continue;

            blockquoteOpen.attrJoin('class', `markdown-alert markdown-alert-${type}`);
            blockquoteOpen.meta = blockquoteOpen.meta || {};
            blockquoteOpen.meta.omlorixAlertType = type;

            const paragraphIsEmpty = removeMarkerFromInlineToken(inlineToken, markerMatch);
            if (paragraphIsEmpty) {
                // A standalone marker paragraph is implementation syntax, not
                // visible content. Lists, code blocks, or later paragraphs then
                // become the first body element immediately after the title.
                tokens.splice(index + 1, 3);
            }
        }
    }

    /** Render only trusted, static structure; user content remains in normal tokens. */
    function renderAlertTitle(type) {
        const label = getAlertLabel(type);
        return `<p class="markdown-alert-title"><span class="markdown-alert-icon markdown-alert-icon-${type}" aria-hidden="true"></span><span class="markdown-alert-label">${escapeHtml(label)}</span></p>\n`;
    }

    /** Register GitHub-style alert support on one markdown-it renderer. */
    function plugin(md) {
        if (!md || md.__omlorixAlertsRegistered) return;

        Object.defineProperty(md, '__omlorixAlertsRegistered', {
            configurable: false,
            enumerable: false,
            value: true,
            writable: false,
        });

        md.core.ruler.after('inline', 'omlorix_markdown_alerts', transformAlertTokens);

        const defaultBlockquoteOpen = md.renderer.rules.blockquote_open
            || function (tokens, index, options, env, self) {
                return self.renderToken(tokens, index, options);
            };

        md.renderer.rules.blockquote_open = function (tokens, index, options, env, self) {
            const openingTag = defaultBlockquoteOpen(tokens, index, options, env, self);
            const type = tokens[index]?.meta?.omlorixAlertType;
            return ALERT_TYPES[type] ? `${openingTag}${renderAlertTitle(type)}` : openingTag;
        };
    }

    /**
     * Insert trusted shared icons after untrusted Markdown HTML has crossed its
     * sanitizer boundary. This keeps raw SVG disabled in the rich editor while
     * still giving alerts the same icon treatment on every rendering surface.
     */
    function enhanceIcons(container) {
        if (!container?.querySelectorAll || !root.Icons) return;

        container.querySelectorAll('.markdown-alert-icon').forEach((iconElement) => {
            if (iconElement.childNodes?.length) return;

            const type = Object.keys(ALERT_TYPES).find((candidate) => (
                iconElement.classList.contains(`markdown-alert-icon-${candidate}`)
            ));
            const iconKey = type ? ALERT_TYPES[type].iconKey : '';
            const iconMarkup = iconKey ? root.Icons[iconKey] : '';
            if (!iconMarkup) return;

            iconElement.innerHTML = iconMarkup;
            iconElement.querySelectorAll('svg').forEach((svg) => {
                svg.setAttribute('aria-hidden', 'true');
                svg.setAttribute('focusable', 'false');
            });
        });
    }

    return Object.freeze({
        alertTypes: ALERT_TYPES,
        enhanceIcons,
        getAlertLabel,
        plugin,
    });
});
