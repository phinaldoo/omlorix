function registerCodeSnippet(code) {
    const id = `code-${Math.random().toString(36).slice(2)}-${Date.now()}`;
    codeSnippetRegistry.set(id, String(code ?? ''));
    return id;
}

function renderMathWithRetry(element, attempt) {
    if (!element) {
        return;
    }
    const safeAttempt = Number.isFinite(attempt) ? attempt : 0;
    const renderer = resolveMathRenderer();

    if (renderer) {
        try {
            renderer(element, {
                delimiters: [
                    { left: '$$', right: '$$', display: true },
                    { left: '\\[', right: '\\]', display: true },
                    { left: '\\begin{equation}', right: '\\end{equation}', display: true },
                    { left: '\\begin{equation*}', right: '\\end{equation*}', display: true },
                    { left: '\\begin{align}', right: '\\end{align}', display: true },
                    { left: '\\begin{align*}', right: '\\end{align*}', display: true },
                    { left: '\\begin{alignat}', right: '\\end{alignat}', display: true },
                    { left: '\\begin{gather}', right: '\\end{gather}', display: true },
                    { left: '\\begin{CD}', right: '\\end{CD}', display: true },
                    { left: '$', right: '$', display: false },
                    { left: '\\(', right: '\\)', display: false }
                ],
                throwOnError: false
            });
        } catch (mathError) {
            console.error('KaTeX render error:', mathError);
        }
        return;
    }

    if (safeAttempt >= MAX_KATEX_RENDER_ATTEMPTS) {
        return;
    }

    setTimeout(() => {
        renderMathWithRetry(element, safeAttempt + 1);
    }, KATEX_RENDER_RETRY_DELAY);
}

function resolveMathRenderer() {
    if (typeof window !== 'undefined') {
        if (typeof window.renderMathInElement === 'function') {
            return window.renderMathInElement;
        }
        if (window.katex && typeof window.katex.renderMathInElement === 'function') {
            return window.katex.renderMathInElement.bind(window.katex);
        }
    }
    return null;
}

function wrapImplicitMathSegments(root) {
    if (!root || typeof document === 'undefined' || typeof Node === 'undefined' || typeof NodeFilter === 'undefined') {
        return;
    }
    if (typeof document.createTreeWalker !== 'function') {
        return;
    }

    const nodesToProcess = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);

    while (walker.nextNode()) {
        const currentNode = walker.currentNode;
        if (!currentNode || typeof currentNode.nodeValue !== 'string') {
            continue;
        }
        const value = currentNode.nodeValue;
        if (!value || value.indexOf('(') === -1 || value.indexOf(')') === -1) {
            continue;
        }
        if (value.indexOf('\\') === -1 && value.indexOf('^') === -1 && value.indexOf('_') === -1) {
            continue;
        }
        if (!isImplicitMathEligibleNode(currentNode)) {
            continue;
        }

        const conversion = convertImplicitMathInText(value);
        if (conversion.changed) {
            nodesToProcess.push({ node: currentNode, value: conversion.text });
        }
    }

    nodesToProcess.forEach(({ node, value }) => {
        node.nodeValue = value;
    });
}

function isImplicitMathEligibleNode(node) {
    if (!node) {
        return false;
    }
    let parent = node.parentNode;
    while (parent) {
        if (parent.nodeType === Node.ELEMENT_NODE) {
            const tagName = parent.tagName;
            if (IMPLICIT_MATH_EXCLUDED_TAGS.includes(tagName)) {
                return false;
            }
            if (parent.classList && (parent.classList.contains('code-block-content') || parent.classList.contains('no-math') || parent.classList.contains('katex'))) {
                return false;
            }
        }
        parent = parent.parentNode;
    }
    return true;
}

function isCharacterEscaped(source, index) {
    if (!source || index <= 0) {
        return false;
    }
    let backslashCount = 0;
    for (let i = index - 1; i >= 0; i -= 1) {
        if (source[i] === '\\') {
            backslashCount += 1;
        } else {
            break;
        }
    }
    return (backslashCount % 2) === 1;
}

function buildMathMask(text) {
    if (!text) {
        return [];
    }
    const mask = new Array(text.length).fill(false);
    const stack = [];

    let index = 0;
    while (index < text.length) {
        if (stack.length > 0) {
            mask[index] = true;
        }

        const currentChar = text[index];
        const top = stack.length > 0 ? stack[stack.length - 1].delim : null;

        if (currentChar === '$' && !isCharacterEscaped(text, index)) {
            const isDouble = (index + 1 < text.length) && text[index + 1] === '$' && !isCharacterEscaped(text, index + 1);
            const delimiter = isDouble ? '$$' : '$';

            if (top === delimiter) {
                mask[index] = true;
                if (isDouble && index + 1 < text.length) {
                    mask[index + 1] = true;
                    index += 1;
                }
                stack.pop();
            } else {
                mask[index] = true;
                if (isDouble && index + 1 < text.length) {
                    mask[index + 1] = true;
                    index += 1;
                }
                stack.push({ delim: delimiter });
            }
        } else if (currentChar === '\\' && !isCharacterEscaped(text, index)) {
            const beginMatch = text.slice(index).match(/^\\begin\{([a-zA-Z0-9*]+)\}/);
            const endMatch = text.slice(index).match(/^\\end\{([a-zA-Z0-9*]+)\}/);

            if (beginMatch) {
                const fullMatch = beginMatch[0];
                const envName = beginMatch[1];
                stack.push({ delim: `\\begin{${envName}}` });
                for (let i = 0; i < fullMatch.length; i++) {
                    mask[index + i] = true;
                }
                index += fullMatch.length;
                continue;
            } else if (endMatch) {
                const fullMatch = endMatch[0];
                const envName = endMatch[1];
                const expected = `\\begin{${envName}}`;
                if (top === expected) {
                    stack.pop();
                    for (let i = 0; i < fullMatch.length; i++) {
                        mask[index + i] = true;
                    }
                    index += fullMatch.length;
                    continue;
                }
            }

            if (index + 1 < text.length) {
                const nextChar = text[index + 1];
                if ((nextChar === '(' || nextChar === '[') && top !== '\\(' && top !== '\\[') {
                    const delimiter = nextChar === '(' ? '\\(' : '\\[';
                    mask[index] = true;
                    mask[index + 1] = true;
                    stack.push({ delim: delimiter });
                    index += 1;
                } else if ((nextChar === ')' || nextChar === ']') && ((nextChar === ')' && top === '\\(') || (nextChar === ']' && top === '\\['))) {
                    mask[index] = true;
                    mask[index + 1] = true;
                    stack.pop();
                    index += 1;
                }
            }
        }

        index += 1;
    }

    return mask;
}

function convertImplicitMathInText(text) {
    if (!text) {
        return { text, changed: false };
    }

    const mathMask = buildMathMask(text);
    let result = '';
    let changed = false;
    let index = 0;

    while (index < text.length) {
        const char = text[index];
        if (char === '(' && !mathMask[index] && isImplicitMathPrefix(text, index)) {
            const closingIndex = findMatchingParenIndex(text, index);
            if (closingIndex > -1 && !mathMask[closingIndex]) {
                const segment = text.slice(index + 1, closingIndex);
                if (shouldWrapImplicitMathSegment(segment)) {
                    result += `\\(${segment.trim()}\\)`;
                    index = closingIndex + 1;
                    changed = true;
                    continue;
                }
            }
        }

        result += char;
        index += 1;
    }

    return { text: changed ? result : text, changed };
}

function isImplicitMathPrefix(source, index) {
    if (index <= 0) {
        return true;
    }
    const prevChar = source[index - 1];
    return IMPLICIT_MATH_PREFIX_ALLOWED.test(prevChar);
}

function findMatchingParenIndex(source, startIndex) {
    let depth = 0;
    for (let i = startIndex; i < source.length; i += 1) {
        const currentChar = source[i];
        if (currentChar === '(') {
            if (i > startIndex && source[i - 1] === '\\') {
                continue;
            }
            depth += 1;
        } else if (currentChar === ')') {
            if (i > startIndex && source[i - 1] === '\\') {
                continue;
            }
            depth -= 1;
            if (depth === 0) {
                return i;
            }
        }
    }
    return -1;
}

function shouldWrapImplicitMathSegment(segment) {
    if (!segment) {
        return false;
    }
    const trimmed = segment.trim();
    if (!trimmed || trimmed.length > IMPLICIT_MATH_MAX_SEGMENT_LENGTH) {
        return false;
    }
    if (trimmed.includes('\\(') || trimmed.includes('\\)') || trimmed.includes('$$')) {
        return false;
    }
    if (trimmed.includes('$')) {
        return false;
    }
    if (!IMPLICIT_MATH_INDICATOR_REGEX.test(trimmed)) {
        return false;
    }
    return true;
}

function unescapeHtml(text) {
    if (text === null || text === undefined) {
        return '';
    }
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
}

function getChatBooleanSetting(key, fallback = true) {
    try {
        const stored = localStorage.getItem(key);
        if (stored === 'true' || stored === '1') return true;
        if (stored === 'false' || stored === '0') return false;
    } catch (_) {
        // Ignore localStorage access issues and fall back below
    }

    if (window.chatSetup && Object.prototype.hasOwnProperty.call(window.chatSetup, key)) {
        return Boolean(window.chatSetup[key]);
    }

    return fallback;
}

if (typeof window !== 'undefined') {
    window.getChatBooleanSetting = getChatBooleanSetting;
}

function createMarkdownCodePlaceholder(segments, source) {
    const placeholder = `${MARKDOWN_CODE_PLACEHOLDER_PREFIX}${segments.size}${MARKDOWN_CODE_PLACEHOLDER_SUFFIX}`;
    segments.set(placeholder, source);
    return placeholder;
}

function protectFencedMarkdownCodeBlocks(text, segments) {
    let result = '';
    let index = 0;

    while (index < text.length) {
        const nextNewline = text.indexOf('\n', index);
        const lineEnd = nextNewline === -1 ? text.length : nextNewline + 1;
        const line = text.slice(index, lineEnd);
        const opener = line.match(/^ {0,3}(`{3,}|~{3,})/);

        if (!opener) {
            result += line;
            index = lineEnd;
            continue;
        }

        const fenceStart = index;
        const marker = opener[1][0];
        const minFenceLength = opener[1].length;
        index = lineEnd;

        while (index < text.length) {
            const closingNextNewline = text.indexOf('\n', index);
            const closingLineEnd = closingNextNewline === -1 ? text.length : closingNextNewline + 1;
            const closingLine = text.slice(index, closingLineEnd);
            const closing = closingLine.match(/^ {0,3}(`+|~+)[ \t]*(?:\n|$)/);

            if (closing && closing[1][0] === marker && closing[1].length >= minFenceLength) {
                index = closingLineEnd;
                break;
            }

            index = closingLineEnd;
        }

        const fencedCodeBlock = text.slice(fenceStart, index);
        result += createMarkdownCodePlaceholder(segments, fencedCodeBlock);
    }

    return result;
}

function getMarkdownIndentColumns(line) {
    let columns = 0;
    for (let index = 0; index < line.length; index += 1) {
        const char = line[index];
        if (char === ' ') {
            columns += 1;
        } else if (char === '\t') {
            columns += 4 - (columns % 4);
        } else {
            break;
        }
    }
    return columns;
}

function getPreviousMarkdownLine(text, lineStart) {
    if (lineStart <= 0) {
        return null;
    }
    const previousEnd = lineStart - 1;
    const previousStart = text.lastIndexOf('\n', Math.max(0, previousEnd - 1)) + 1;
    return text.slice(previousStart, previousEnd);
}

function getPreviousNonBlankMarkdownLine(text, lineStart) {
    let scanEnd = lineStart;
    while (scanEnd > 0) {
        const previous = getPreviousMarkdownLine(text, scanEnd);
        if (previous === null) {
            return null;
        }
        if (previous.trim()) {
            return previous;
        }
        scanEnd -= previous.length + 1;
    }
    return null;
}

function getMarkdownListCodeIndent(line) {
    const match = line.match(/^( {0,3})(?:[-+*]|\d{1,9}[.)])([ \t]*)/);
    if (!match) {
        return null;
    }
    const markerColumns = getMarkdownIndentColumns(match[0]);
    const paddingColumns = getMarkdownIndentColumns(match[2] || '');
    const contentIndent = paddingColumns > 0 && paddingColumns < 5
        ? markerColumns
        : getMarkdownIndentColumns(match[1]) + match[0].trimEnd().length + 1;
    return contentIndent + 4;
}

function isMarkdownBlockBoundaryBeforeIndentedCode(line) {
    const trimmed = String(line || '').trim();
    return /^(#{1,6})(?:\s|$)/.test(trimmed)
        || /^(`{3,}|~{3,})/.test(trimmed)
        || /^ {0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line);
}

function canStartIndentedMarkdownCodeBlock(text, lineStart, line) {
    if (!line.trim() || !/^(?: {4,}|\t)/.test(line)) {
        return false;
    }

    const indentColumns = getMarkdownIndentColumns(line);
    const previousLine = getPreviousMarkdownLine(text, lineStart);
    const previousNonBlank = getPreviousNonBlankMarkdownLine(text, lineStart);
    const listCodeIndent = previousNonBlank ? getMarkdownListCodeIndent(previousNonBlank) : null;

    if (listCodeIndent !== null && indentColumns < listCodeIndent) {
        return false;
    }

    if (!previousLine || !previousLine.trim()) {
        return true;
    }

    if (/^ {0,3}>/.test(previousLine)) {
        return false;
    }

    return isMarkdownBlockBoundaryBeforeIndentedCode(previousLine);
}

function protectIndentedMarkdownCodeBlocks(text, segments) {
    let result = '';
    let index = 0;

    while (index < text.length) {
        const nextNewline = text.indexOf('\n', index);
        const lineEnd = nextNewline === -1 ? text.length : nextNewline + 1;
        const line = text.slice(index, lineEnd);

        if (!canStartIndentedMarkdownCodeBlock(text, index, line)) {
            result += line;
            index = lineEnd;
            continue;
        }

        const blockStart = index;
        index = lineEnd;

        while (index < text.length) {
            const blockNextNewline = text.indexOf('\n', index);
            const blockLineEnd = blockNextNewline === -1 ? text.length : blockNextNewline + 1;
            const blockLine = text.slice(index, blockLineEnd);

            if (blockLine.trim() && !/^(?: {4,}|\t)/.test(blockLine)) {
                break;
            }

            index = blockLineEnd;
        }

        const indentedCodeBlock = text.slice(blockStart, index);
        result += createMarkdownCodePlaceholder(segments, indentedCodeBlock);
    }

    return result;
}

function protectInlineMarkdownCodeSpans(text, segments) {
    let result = '';
    let index = 0;

    while (index < text.length) {
        if (text[index] !== '`') {
            result += text[index];
            index += 1;
            continue;
        }

        let tickCount = 1;
        while (index + tickCount < text.length && text[index + tickCount] === '`') {
            tickCount += 1;
        }

        const delimiter = '`'.repeat(tickCount);
        const closingIndex = text.indexOf(delimiter, index + tickCount);

        if (closingIndex === -1) {
            result += delimiter;
            index += tickCount;
            continue;
        }

        const codeSpan = text.slice(index, closingIndex + tickCount);
        result += createMarkdownCodePlaceholder(segments, codeSpan);
        index = closingIndex + tickCount;
    }

    return result;
}

function protectMarkdownCodeSegments(text) {
    if (!text) {
        return { text, segments: new Map() };
    }

    const segments = new Map();

    // LaTeX protection must not inspect literal examples. Shield Markdown code
    // first, then restore it before handing the source to markdown-it.
    const withoutFences = protectFencedMarkdownCodeBlocks(String(text), segments);
    const withoutIndentedBlocks = protectIndentedMarkdownCodeBlocks(withoutFences, segments);
    const withoutInlineCode = protectInlineMarkdownCodeSpans(withoutIndentedBlocks, segments);

    return { text: withoutInlineCode, segments };
}

function restoreMarkdownCodeSegments(text, segments) {
    if (!segments || segments.size === 0) {
        return text;
    }

    let result = String(text || '');
    Array.from(segments.entries()).reverse().forEach(([placeholder, source]) => {
        while (result.includes(placeholder)) {
            result = result.replace(placeholder, source);
        }
    });
    return result;
}

function getMarkdownRenderer() {
    if (markdownRendererInstance) {
        return markdownRendererInstance;
    }
    if (typeof window === 'undefined' || typeof window.markdownit === 'undefined') {
        return null;
    }

    const md = window.markdownit({
        html: false,
        linkify: true,
        typographer: true,
        breaks: true,
        highlight: function () {
            return '';
        }
    });

    const chatSanitizer = window.ChatSanitizer;
    const isSafeRenderedHref = (rawHref) => Boolean(
        chatSanitizer
        && typeof chatSanitizer.isSafeUrl === 'function'
        && chatSanitizer.isSafeUrl(rawHref)
    ) || Boolean(
        window.ChatMarkdownFileRefs
        && typeof window.ChatMarkdownFileRefs.getFileIdFromUrl === 'function'
        && window.ChatMarkdownFileRefs.getFileIdFromUrl(rawHref)
    );

    md.validateLink = function (url) {
        return isSafeRenderedHref(url);
    };

    const deflistPlugin = window.markdownitDeflist || window.markdownItDeflist;
    const abbrPlugin = window.markdownitAbbr || window.markdownItAbbr;
    const taskListPlugin = window.markdownitTaskLists || window.markdownItTaskLists;
    const markPlugin = window.markdownitMark || window.markdownItMark;
    const supPlugin = window.markdownitSup || window.markdownItSup;
    const subPlugin = window.markdownitSub || window.markdownItSub;
    const katexPlugin = window.markdownitKatex || window.markdownItKatex;
    const alertsPlugin = window.markdownitAlerts;

    if (deflistPlugin) md.use(deflistPlugin);
    if (abbrPlugin) md.use(abbrPlugin);
    if (taskListPlugin) md.use(taskListPlugin);
    if (markPlugin) md.use(markPlugin);
    if (supPlugin) md.use(supPlugin);
    if (subPlugin) md.use(subPlugin);
    if (alertsPlugin) md.use(alertsPlugin);
    if (katexPlugin && window.katex) {
        try {
            md.use(katexPlugin, {
                throwOnError: false,
                strict: 'ignore'
            });
        } catch (error) {
            console.error('Failed to initialize KaTeX plugin:', error);
        }
    }

    const defaultTableOpen = md.renderer.rules.table_open || function (tokens, idx, options, env, self) {
        return self.renderToken(tokens, idx, options);
    };
    const defaultLinkOpen = md.renderer.rules.link_open || function (tokens, idx, options, env, self) {
        return self.renderToken(tokens, idx, options);
    };

    const defaultTableClose = md.renderer.rules.table_close || function (tokens, idx, options, env, self) {
        return self.renderToken(tokens, idx, options);
    };

    let tableNestLevel = 0;

    md.renderer.rules.fence = function (tokens, idx) {
        const token = tokens[idx];
        const code = token.content || '';
        const escapedCode = escapeHtml(code);
        const rawLangInfo = (token.info || '').trim();
        const primaryLang = rawLangInfo.split(/\s+/)[0] || '';
        const normalizedLang = normalizeHighlightLanguage(primaryLang);
        const canRunPython = isPythonExecutionLanguage(normalizedLang) && canRunPythonCodeBlocks();
        const blockId = 'code-block-' + Math.random().toString(36).slice(2, 11);
        const snippetId = registerCodeSnippet(code);
        const previewKind = getCodePreviewKind(primaryLang || normalizedLang, code);
        const defaultDisplayLang = primaryLang || normalizedLang || 'plaintext';
        const previewLabel = getCodePreviewLabel(previewKind);
        const displayLang = previewKind && previewLabel !== 'Code'
            ? previewLabel
            : defaultDisplayLang;
        const codeBlockViewLabel = getCodeBlockActionLabel('code_block_view_mode_label', 'Code block view');
        const codeTabLabel = getCodeBlockActionLabel('code_block_tab_code', 'Code');
        const previewTabLabel = getCodeBlockActionLabel('code_block_tab_preview', 'Preview');
        const codeTabId = `${blockId}-tab-code`;
        const previewTabId = `${blockId}-tab-preview`;
        const codePanelId = `${blockId}-panel-code`;
        const previewPanelId = `${blockId}-panel-preview`;
        const htmlSettingsMenuId = `${blockId}-html-settings-menu`;
        const htmlScriptsToggleId = `${blockId}-html-settings-scripts`;
        const htmlExternalContentToggleId = `${blockId}-html-settings-external-content`;
        const codeLanguageClassName = getCodeBlockSyntaxLanguage(primaryLang, normalizedLang, previewKind);
        const languageClass = `language-${escapeHtml(codeLanguageClassName)}`;
        const previewToggle = previewKind
            ? `
                <div class="code-block-mode-toggle" role="tablist" aria-label="${escapeHtml(codeBlockViewLabel)}" data-i18n-attr="aria-label:code_block_view_mode_label">
                    <button type="button" class="code-view-toggle-btn is-active" id="${codeTabId}" role="tab" aria-selected="true" aria-controls="${codePanelId}" tabindex="0" data-view="code" aria-label="${escapeHtml(codeTabLabel)}" data-i18n-attr="aria-label:code_block_tab_code">
                        ${MARKDOWN_CODE_SVG}
                        <span data-i18n="code_block_tab_code">${escapeHtml(codeTabLabel)}</span>
                    </button>
                    <button type="button" class="code-view-toggle-btn" id="${previewTabId}" role="tab" aria-selected="false" aria-controls="${previewPanelId}" tabindex="-1" data-view="preview" aria-label="${escapeHtml(previewTabLabel)}" data-i18n-attr="aria-label:code_block_tab_preview">
                        ${MARKDOWN_PREVIEW_SVG}
                        <span data-i18n="code_block_tab_preview">${escapeHtml(previewTabLabel)}</span>
                    </button>
                </div>
            `
            : '';
        const previewPanel = previewKind
            ? `
                <div class="code-block-panel code-block-panel-preview" id="${previewPanelId}" role="tabpanel" aria-labelledby="${previewTabId}" data-view="preview" hidden>
                    <div class="code-block-preview-pane" data-preview-kind="${escapeHtml(previewKind)}" data-preview-state="idle"></div>
                </div>
            `
            : '';
        const previewReloadAction = previewKind
            ? `
                <button type="button" class="code-action-btn reload-preview-btn" ${getCodeBlockActionA11yAttrs('code_block_reload_preview', 'Reload preview')} data-action="reload-preview">
                    ${MARKDOWN_RELOAD_SVG}
                </button>
            `
            : '';
        const vegaPreviewExpandAction = previewKind === 'vega' || previewKind === 'vega-lite'
            ? `
                <button type="button" class="code-action-btn vega-preview-external-resources-btn" ${getCodeBlockActionA11yAttrs('code_block_vega_external_resources_review', 'Review external connections')} data-action="toggle-vega-external-resources" aria-hidden="true" hidden>
                    ${MARKDOWN_EXTERNAL_CONTENT_SVG}
                </button>
                <button type="button" class="code-action-btn vega-preview-expand-btn" ${getCodeBlockActionA11yAttrs('code_block_open_large_preview', 'Open large preview')} data-action="expand-vega-preview">
                    ${MARKDOWN_EXPAND_PREVIEW_SVG}
                </button>
            `
            : '';
        const htmlPreviewCapabilityActions = previewKind === 'html'
            ? `
                <div class="code-block-html-settings">
                    <button type="button" class="code-action-btn code-block-html-settings-trigger" aria-haspopup="dialog" aria-expanded="false" aria-controls="${htmlSettingsMenuId}" ${getCodeBlockActionA11yAttrs('canvas_html_preview_settings', 'HTML preview settings')}>
                        ${MARKDOWN_SETTINGS_SVG}
                    </button>
                    <div class="code-block-html-settings-menu" id="${htmlSettingsMenuId}" role="dialog" aria-label="${escapeHtml(getCodeBlockActionLabel('canvas_html_preview_settings', 'HTML preview settings'))}" data-i18n-attr="aria-label:canvas_html_preview_settings" hidden>
                        <label class="code-block-html-settings-menu-item" for="${htmlExternalContentToggleId}">
                            <span class="code-block-html-settings-menu-icon" aria-hidden="true">${MARKDOWN_EXTERNAL_CONTENT_SVG}</span>
                            <span class="code-block-html-settings-menu-label" data-i18n="canvas_html_external_content">${escapeHtml(getCodeBlockActionLabel('canvas_html_external_content', 'External content'))}</span>
                            <span class="toggle-switch">
                                <input class="toggle-input html-preview-capability-toggle html-preview-external-content-toggle" id="${htmlExternalContentToggleId}" type="checkbox" role="switch" data-html-preview-permission="external-content">
                                <span class="toggle-slider" aria-hidden="true"></span>
                            </span>
                        </label>
                        <label class="code-block-html-settings-menu-item" for="${htmlScriptsToggleId}">
                            <span class="code-block-html-settings-menu-icon" aria-hidden="true">${MARKDOWN_RUN_SVG}</span>
                            <span class="code-block-html-settings-menu-label" data-i18n="canvas_html_interactions">${escapeHtml(getCodeBlockActionLabel('canvas_html_interactions', 'Interactions (requires external content)'))}</span>
                            <span class="toggle-switch">
                                <input class="toggle-input html-preview-capability-toggle html-preview-scripts-toggle" id="${htmlScriptsToggleId}" type="checkbox" role="switch" data-html-preview-permission="scripts">
                                <span class="toggle-slider" aria-hidden="true"></span>
                            </span>
                        </label>
                    </div>
                </div>
            `
            : '';
        const runAction = canRunPython
            ? `
                <button type="button" class="code-action-btn code-action-btn-primary run-code-btn" ${getCodeBlockActionA11yAttrs('code_block_run_python_stream_locked', 'Run Python code after generation finishes')} data-code-id="${snippetId}" data-language="python" data-stream-locked="true" data-running="false" disabled aria-disabled="true">
                    ${getRunCodeButtonMarkup(false)}
                </button>
            `
            : '';

        return `
            <div class="code-block-wrapper${previewKind ? ' code-block-wrapper-previewable' : ''}${canRunPython ? ' code-block-wrapper-runnable' : ''}" data-code-block-id="${blockId}" data-language="${escapeHtml(normalizedLang)}" data-language-display="${escapeHtml(displayLang)}"${previewKind ? ` data-preview-kind="${escapeHtml(previewKind)}"` : ''}${canRunPython ? ' data-code-executable="python"' : ''}>
                <div class="code-block-header">
                    <div class="code-block-meta">
                        <span class="code-block-language">${escapeHtml(displayLang)}</span>
                        
                    </div>
                    <div class="code-block-actions">
                        ${previewToggle}
                        ${htmlPreviewCapabilityActions}
                        ${vegaPreviewExpandAction}
                        ${previewReloadAction}
                        ${runAction}
                        <button type="button" class="code-action-btn copy-code-btn" ${getCodeBlockActionA11yAttrs('code_block_copy_code', 'Copy code')} data-code-id="${snippetId}">
                            ${MARKDOWN_COPY_SVG}
                        </button>
                        <button type="button" class="code-action-btn download-code-btn" ${getCodeBlockActionA11yAttrs('code_block_download_code', 'Download code')} data-code-id="${snippetId}" data-lang="${escapeHtml(normalizedLang)}">
                            ${MARKDOWN_DOWNLOAD_SVG}
                        </button>
                        <button type="button" class="code-action-btn collapse-code-btn" ${getCodeBlockActionA11yAttrs('code_block_collapse', 'Collapse code block')}>
                            ${MARKDOWN_COLLAPSE_SVG}
                        </button>
                    </div>
                </div>
                <div class="code-block-content" data-content-id="${blockId}">
                    <div class="code-block-panel code-block-panel-code is-active" id="${codePanelId}"${previewKind ? ` role="tabpanel" aria-labelledby="${codeTabId}"` : ''} data-view="code">
                        <pre class="${languageClass}"><code class="${languageClass}" data-code-id="${snippetId}">${escapedCode}</code></pre>
                    </div>
                    ${previewPanel}
                </div>
                ${canRunPython ? '<div class="code-execution-results" data-state="idle" hidden></div>' : ''}
            </div>
        `;
    };

    md.renderer.rules.table_open = function (tokens, idx, options, env, self) {
        tableNestLevel++;
        const tableHtml = defaultTableOpen(tokens, idx, options, env, self);

        if (tableNestLevel === 1) {
            const copyTableLabel = getChatPreviewTranslation('chat_copy_table_markdown_aria', 'Copy table markdown');
            return `
                <div class="table-wrapper">
                    <div class="table-actions">
                        <button class="table-copy-btn" title="${escapeHtml(copyTableLabel)}" aria-label="${escapeHtml(copyTableLabel)}" data-i18n-attr="title:chat_copy_table_markdown_aria;aria-label:chat_copy_table_markdown_aria">
                            ${MARKDOWN_COPY_SVG}
                        </button>
                    </div>
                    ${tableHtml}`;
        }

        return tableHtml;
    };

    md.renderer.rules.table_close = function (tokens, idx, options, env, self) {
        const tableHtml = defaultTableClose(tokens, idx, options, env, self);
        const isTopLevel = tableNestLevel === 1;
        tableNestLevel--;

        if (isTopLevel) {
            return `${tableHtml}</div>`;
        }

        return tableHtml;
    };

    md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
        const token = tokens[idx];
        const hrefIndex = token.attrIndex('href');
        if (hrefIndex >= 0) {
            const hrefValue = token.attrs[hrefIndex][1];
            if (!isSafeRenderedHref(hrefValue)) {
                token.attrs[hrefIndex][1] = '#';
            }
        }

        token.attrSet('rel', 'noopener noreferrer nofollow');
        token.attrSet('target', '_blank');

        return defaultLinkOpen(tokens, idx, options, env, self);
    };

    const baseRender = md.render.bind(md);

    function protectLatexBlocks(text) {
        if (!text) return { text, blocks: new Map() };
        
        const blocks = new Map();
        let result = text;
        
        // Protect LaTeX environments (align, equation, gather, etc)
        result = result.replace(/\\begin\{(equation|align|gather|alignat|CD|split)\*?\}([\s\S]*?)\\end\{\1\*?\}/g, (match) => {
            const id = latexPlaceholderCounter++;
            const placeholder = `${LATEX_PLACEHOLDER_PREFIX}${id}${LATEX_PLACEHOLDER_SUFFIX}`;
            blocks.set(placeholder, match);
            return placeholder;
        });
        
        // Protect display math: \[...\] - use non-greedy match with dotall behavior
        result = result.replace(/\\\[([\s\S]*?)\\\]/g, (match) => {
            const id = latexPlaceholderCounter++;
            const placeholder = `${LATEX_PLACEHOLDER_PREFIX}${id}${LATEX_PLACEHOLDER_SUFFIX}`;
            blocks.set(placeholder, match);
            return placeholder;
        });
        
        // Protect display math: $$...$$ (multiline)
        result = result.replace(/\$\$([\s\S]*?)\$\$/g, (match) => {
            const id = latexPlaceholderCounter++;
            const placeholder = `${LATEX_PLACEHOLDER_PREFIX}${id}${LATEX_PLACEHOLDER_SUFFIX}`;
            blocks.set(placeholder, match);
            return placeholder;
        });
        
        // Protect inline math: \(...\) - be careful not to match across too much text
        result = result.replace(/\\\(([\s\S]*?)\\\)/g, (match) => {
            const id = latexPlaceholderCounter++;
            const placeholder = `${LATEX_PLACEHOLDER_PREFIX}${id}${LATEX_PLACEHOLDER_SUFFIX}`;
            blocks.set(placeholder, match);
            return placeholder;
        });
        
        // Protect inline math: $...$ (single line, non-greedy)
        result = result.replace(/\$([^$\n]+?)\$/g, (match, content) => {
            // Skip if it looks like currency (e.g., $100)
            if (/^\d+([,.]\d+)?$/.test(content.trim())) {
                return match;
            }
            const id = latexPlaceholderCounter++;
            const placeholder = `${LATEX_PLACEHOLDER_PREFIX}${id}${LATEX_PLACEHOLDER_SUFFIX}`;
            blocks.set(placeholder, match);
            return placeholder;
        });
        
        return { text: result, blocks };
    }
    
    function restoreLatexBlocks(html, blocks) {
        if (!blocks || blocks.size === 0) return html;
        
        let result = html;
        
        // Replace placeholders with original LaTeX content
        // Iterate in reverse order to handle nested placeholders (e.g. environment inside $$)
        Array.from(blocks.entries()).reverse().forEach(([placeholder, latex]) => {
            while (result.includes(placeholder)) {
                result = result.replace(placeholder, latex);
            }
        });
        
        return result;
    }

    function processNestedMarkdown(html, env, depth) {
        if (typeof DOMParser === 'undefined') {
            return html;
        }
        const currentDepth = depth || 0;
        if (!html) {
            return html;
        }
        let processedHtml = html;
        try {
            const parser = new DOMParser();
            const doc = parser.parseFromString(processedHtml, 'text/html');
            const cells = doc.querySelectorAll('td, th');

            cells.forEach(cell => {
                const textContent = cell.textContent;
                if (!textContent) {
                    return;
                }
                if (!/[`*_\[\]~^#$\\]/.test(textContent)) {
                    return;
                }

                try {
                    tableNestLevel = 0;
                    const normalizedSegment = convertImplicitMathInText(textContent);
                    const nestedHtml = baseRender(normalizedSegment.text, env || {});
                    const nestedProcessed = currentDepth >= 3
                        ? nestedHtml
                        : processNestedMarkdown(nestedHtml, env, currentDepth + 1);
                    const cleaned = nestedProcessed.replace(/^<p>|<\/p>\s*$/g, '');
                    cell.innerHTML = cleaned;
                    wrapImplicitMathSegments(cell);
                    renderMathWithRetry(cell, 0);
                } catch (nestedError) {
                    // Ignore nested rendering errors to keep original content
                }
            });

            processedHtml = doc.body.innerHTML;
        } catch (error) {
            // Fallback to original html on parser failure
        }

        return processedHtml;
    }

    md.render = function (src, env) {
        tableNestLevel = 0;
        latexPlaceholderCounter = 0;

        const preprocessedSrc = window.ChatMarkdownUtils
            && typeof window.ChatMarkdownUtils.normalizeMarkdownForRender === 'function'
            ? window.ChatMarkdownUtils.normalizeMarkdownForRender(src)
            : src;

        const { text: codeProtectedSrc, segments: codeSegments } = protectMarkdownCodeSegments(preprocessedSrc);

        // Protect LaTeX blocks before markdown processing
        const { text: protectedSrc, blocks } = protectLatexBlocks(codeProtectedSrc);
        const markdownSource = restoreMarkdownCodeSegments(protectedSrc, codeSegments);

        const html = baseRender(markdownSource, env);

        // Restore LaTeX blocks after markdown processing
        const restoredHtml = restoreLatexBlocks(html, blocks);

        return processNestedMarkdown(restoredHtml, env, 0);
    };

    markdownRendererInstance = md;
    return markdownRendererInstance;
}

