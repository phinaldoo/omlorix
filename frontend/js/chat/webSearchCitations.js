const CITATIONS_SIDEBAR_VISIBLE_CLASS = 'visible';
const CITATIONS_SIDEBAR_OPEN_CLASS = 'open';
const CITATIONS_TRANSITION_DURATION = 300;
const CITATIONS_TRANSITION_BUFFER = 100;
const CITATIONS_FOCUSABLE_SELECTOR = [
    'button:not([disabled])',
    '[href]',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
].join(', ');

// These will be initialized when DOM is ready
let citationsSidebar = null;
let citationsSidebarClose = null;
let citationsSidebarContent = null;
let citationsSidebarBackdrop = null;

// Initialize elements when DOM is ready
function initializeElements() {
    citationsSidebar = document.getElementById('citationsSidebar');
    citationsSidebarClose = document.getElementById('citationsSidebarClose');
    citationsSidebarContent = document.getElementById('citationsSidebarContent');
    citationsSidebarBackdrop = document.getElementById('citationsSidebarBackdrop');
    
    if (citationsSidebar) {
        citationsSidebar.setAttribute('aria-hidden', 'true');
    }
    
    if (citationsSidebarClose) {
        citationsSidebarClose.addEventListener('click', closeCitationsSidebar);
    }
    
    if (citationsSidebarBackdrop) {
        citationsSidebarBackdrop.setAttribute('aria-hidden', 'true');
        citationsSidebarBackdrop.addEventListener('click', closeCitationsSidebar);
    }

    document.addEventListener('keydown', handleCitationsSidebarKeydown, true);
    document.addEventListener('i18n:updated', rerenderCurrentCitations);
    
    // Register escape handler for citations sidebar
    if (typeof window !== 'undefined' && window.registerEscapeHandler) {
        window.registerEscapeHandler({
            id: 'citations-sidebar',
            priority: 110,
            isActive: () => {
                return citationsSidebar && 
                       citationsSidebar.classList.contains(CITATIONS_SIDEBAR_VISIBLE_CLASS) &&
                       citationsSidebar.classList.contains(CITATIONS_SIDEBAR_OPEN_CLASS);
            },
            close: closeCitationsSidebar
        });
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeElements);
} else {
    initializeElements();
}

const citationsSidebarState = {
    transitionEndHandler: null,
    closingTimeoutId: null,
    lastFocusedElement: null,
};

function clearCitationsCloseState() {
    if (!citationsSidebar) return;
    if (citationsSidebarState.transitionEndHandler) {
        citationsSidebar.removeEventListener('transitionend', citationsSidebarState.transitionEndHandler);
        citationsSidebarState.transitionEndHandler = null;
    }
    if (citationsSidebarState.closingTimeoutId) {
        clearTimeout(citationsSidebarState.closingTimeoutId);
        citationsSidebarState.closingTimeoutId = null;
    }
}

function isCitationsSidebarOpen() {
    return Boolean(
        citationsSidebar &&
        citationsSidebar.classList.contains(CITATIONS_SIDEBAR_VISIBLE_CLASS) &&
        citationsSidebar.classList.contains(CITATIONS_SIDEBAR_OPEN_CLASS)
    );
}

function getCitationTranslation(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function getCitationsFocusableElements() {
    if (!citationsSidebar || typeof citationsSidebar.querySelectorAll !== 'function') {
        return [];
    }

    return Array.from(citationsSidebar.querySelectorAll(CITATIONS_FOCUSABLE_SELECTOR)).filter((element) => {
        if (!element || element.hidden || element.closest?.('[hidden]')) {
            return false;
        }

        if (typeof window !== 'undefined' && typeof window.getComputedStyle === 'function') {
            const computedStyle = window.getComputedStyle(element);
            if (computedStyle.display === 'none' || computedStyle.visibility === 'hidden' || computedStyle.visibility === 'collapse') {
                return false;
            }
        }

        if (typeof element.getClientRects === 'function') {
            return element.getClientRects().length > 0;
        }

        return true;
    });
}

function focusCitationsSidebarPrimaryAction() {
    const focusable = getCitationsFocusableElements();
    const target = focusable[0] || citationsSidebar;
    target?.focus?.();
}

function restoreCitationsSidebarFocus() {
    const target = citationsSidebarState.lastFocusedElement;
    citationsSidebarState.lastFocusedElement = null;
    if (target && typeof target.focus === 'function') {
        try {
            target.focus();
        } catch (_) {}
    }
}

function handleCitationsSidebarKeydown(event) {
    if (event.key !== 'Tab' || !isCitationsSidebarOpen()) {
        return;
    }

    const focusable = getCitationsFocusableElements();
    if (!focusable.length) {
        event.preventDefault();
        citationsSidebar?.focus?.();
        return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const activeElement = document.activeElement;

    if (!citationsSidebar?.contains(activeElement)) {
        event.preventDefault();
        first.focus();
        return;
    }

    if (event.shiftKey && activeElement === first) {
        event.preventDefault();
        last.focus();
        return;
    }

    if (!event.shiftKey && activeElement === last) {
        event.preventDefault();
        first.focus();
    }
}

function removeCitationsVisibility() {
    if (!citationsSidebar) return;
    clearCitationsCloseState();
    citationsSidebar.classList.remove(CITATIONS_SIDEBAR_VISIBLE_CLASS);
    citationsSidebar.classList.remove(CITATIONS_SIDEBAR_OPEN_CLASS);
    if (citationsSidebarBackdrop) {
        citationsSidebarBackdrop.classList.remove(CITATIONS_SIDEBAR_VISIBLE_CLASS);
        citationsSidebarBackdrop.setAttribute('aria-hidden', 'true');
    }
    restoreCitationsSidebarFocus();
}

function setCitationsSidebarState(isOpen) {
    if (!citationsSidebar) {
        return;
    }

    const nextState = Boolean(isOpen);

    if (nextState) {
        clearCitationsCloseState();
        const activeElement = document.activeElement;
        if (activeElement && activeElement !== document.body && !citationsSidebar.contains(activeElement)) {
            citationsSidebarState.lastFocusedElement = activeElement;
        }
        
        // Show backdrop
        if (citationsSidebarBackdrop) {
            citationsSidebarBackdrop.classList.add(CITATIONS_SIDEBAR_VISIBLE_CLASS);
            citationsSidebarBackdrop.setAttribute('aria-hidden', 'false');
        }
        
        // Show sidebar
        citationsSidebar.classList.add(CITATIONS_SIDEBAR_VISIBLE_CLASS);
        citationsSidebar.setAttribute('aria-hidden', 'false');
        
        requestAnimationFrame(() => {
            citationsSidebar.classList.add(CITATIONS_SIDEBAR_OPEN_CLASS);
            focusCitationsSidebarPrimaryAction();
        });
        return;
    }

    citationsSidebar.setAttribute('aria-hidden', 'true');

    if (
        !citationsSidebar.classList.contains(CITATIONS_SIDEBAR_VISIBLE_CLASS) &&
        !citationsSidebar.classList.contains(CITATIONS_SIDEBAR_OPEN_CLASS)
    ) {
        return;
    }

    clearCitationsCloseState();

    citationsSidebarState.transitionEndHandler = (event) => {
        if (event.target !== citationsSidebar) return;
        if (event.propertyName !== 'transform' && event.propertyName !== 'opacity') return;
        if (citationsSidebar.classList.contains(CITATIONS_SIDEBAR_OPEN_CLASS)) return;
        removeCitationsVisibility();
    };

    citationsSidebar.addEventListener('transitionend', citationsSidebarState.transitionEndHandler);

    citationsSidebarState.closingTimeoutId = window.setTimeout(() => {
        if (!citationsSidebar.classList.contains(CITATIONS_SIDEBAR_OPEN_CLASS)) {
            removeCitationsVisibility();
        }
    }, CITATIONS_TRANSITION_DURATION + CITATIONS_TRANSITION_BUFFER);

    requestAnimationFrame(() => {
        citationsSidebar.classList.remove(CITATIONS_SIDEBAR_OPEN_CLASS);
        if (citationsSidebarBackdrop) {
            citationsSidebarBackdrop.classList.remove(CITATIONS_SIDEBAR_VISIBLE_CLASS);
            citationsSidebarBackdrop.setAttribute('aria-hidden', 'true');
        }
    });
}

function openCitationsSidebar() {
    setCitationsSidebarState(true);
}

function closeCitationsSidebar() {
    setCitationsSidebarState(false);
}

function toggleCitationsSidebar() {
    if (!citationsSidebar) {
        return;
    }
    const nextState = !citationsSidebar.classList.contains(CITATIONS_SIDEBAR_OPEN_CLASS);
    setCitationsSidebarState(nextState);
}

// Store current citations for the active message
let currentMessageCitations = [];

// Function to extract citations from a message
function extractCitationsFromMessage(messageId) {
    const citations = [];
    const messageContainer = document.getElementById('a-' + messageId);
    if (!messageContainer) {
        return citations;
    }

    // Try multiple approaches to find citations
    
    // Approach 1: Check if citations are stored in dataset
    try {
        const citationsData = messageContainer.dataset.citations;
        if (citationsData) {
            const parsed = JSON.parse(citationsData);
            if (Array.isArray(parsed)) {
                citations.push(...parsed);
                return citations;
            }
        }
    } catch (e) {}
    
    // Approach 2: Parse message content structure
    try {
        const messageData = messageContainer.dataset.messageData;
        if (messageData) {
            const parsed = JSON.parse(messageData);
            if (parsed.content && Array.isArray(parsed.content)) {
                parsed.content.forEach(block => {
                    if (block.type === 'tool_call_result' && block.meta && block.meta.citations) {
                        citations.push(...block.meta.citations);
                    }
                });
            }
        }
    } catch (e) {}
    
    // Approach 3: Check assistant metadata for citations
    try {
        const metadataStr = messageContainer.dataset.assistantMetadata;
        if (metadataStr) {
            const metadata = JSON.parse(metadataStr);
            if (metadata.citations && Array.isArray(metadata.citations)) {
                citations.push(...metadata.citations);
            }
        }
    } catch (e) {}
    
    // Approach 4: Look for citations in the actual rendered message content
    // This is a fallback for when the message was loaded from history
    try {
        // Check if there are any tool call result blocks in the DOM
        const toolBlocks = messageContainer.querySelectorAll('[data-tool-citations]');
        toolBlocks.forEach(block => {
            try {
                const blockCitations = JSON.parse(block.dataset.toolCitations);
                if (Array.isArray(blockCitations)) {
                    citations.push(...blockCitations);
                }
            } catch (e) {}
        });
    } catch (e) {}

    // Remove duplicates based on URL
    const uniqueCitations = [];
    const seenUrls = new Set();
    citations.forEach(citation => {
        const rawUrl = citation && typeof citation.url === 'string' ? citation.url : '';
        if (rawUrl && !seenUrls.has(rawUrl)) {
            seenUrls.add(rawUrl);
            uniqueCitations.push(citation);
        }
    });

    return uniqueCitations;
}

// Function to get domain from URL
function getDomainFromUrl(url) {
    try {
        const urlObj = new URL(url);
        if (!['http:', 'https:'].includes(urlObj.protocol)) {
            return '';
        }
        return urlObj.hostname.replace('www.', '');
    } catch (e) {
        return '';
    }
}

function getSafeCitationUrl(url) {
    try {
        const urlObj = new URL(url);
        if (!['http:', 'https:'].includes(urlObj.protocol)) {
            return null;
        }
        return urlObj.href;
    } catch (e) {
        return null;
    }
}

function createCitationExternalIcon() {
    const hasRegistry = typeof Icons !== 'undefined' && Icons?.createSvgElement && Icons?.externalLink;
    const icon = hasRegistry
        ? Icons.createSvgElement(Icons.externalLink, 'citation-external-icon')
        : document.createElement('span');
    icon.classList.add('citation-external-icon');
    icon.setAttribute('aria-hidden', 'true');
    if (hasRegistry) {
        icon.setAttribute('width', '16');
        icon.setAttribute('height', '16');
    } else {
        icon.textContent = '↗';
    }
    return icon;
}

function renderEmptyCitationsState() {
    const emptyState = document.createElement('div');
    emptyState.className = 'citations-empty-state';

    const hasRegistry = typeof Icons !== 'undefined' && Icons?.createSvgElement && Icons?.search;
    const icon = hasRegistry ? Icons.createSvgElement(Icons.search) : document.createElement('span');
    icon.setAttribute('aria-hidden', 'true');
    if (hasRegistry) {
        icon.setAttribute('width', '48');
        icon.setAttribute('height', '48');
    } else {
        icon.textContent = '⌕';
    }

    const message = document.createElement('p');
    message.textContent = getCitationTranslation('chat_citations_empty', 'No sources available');

    emptyState.appendChild(icon);
    emptyState.appendChild(message);
    citationsSidebarContent.replaceChildren(emptyState);
}

// Function to render citations in the sidebar
function renderCitations(citations) {
    if (!citationsSidebarContent) return;

    if (!citations || citations.length === 0) {
        renderEmptyCitationsState();
        return;
    }

    citationsSidebarContent.textContent = '';
    const citationsList = document.createElement('div');
    citationsList.className = 'citations-list';

    citations.forEach((citation) => {
        const safeUrl = getSafeCitationUrl(citation?.url);
        const domain = getDomainFromUrl(safeUrl || citation?.url);
        const title = citation?.title || domain || getCitationTranslation('chat_citations_fallback_title', 'Source');
        const snippet = citation?.snippet || '';

        const card = document.createElement(safeUrl ? 'a' : 'div');
        card.className = 'citation-card';
        if (safeUrl) {
            card.href = safeUrl;
            card.target = '_blank';
            card.rel = 'noopener noreferrer';
        }

        const header = document.createElement('div');
        header.className = 'citation-card-header';

        const favicon = document.createElement('div');
        favicon.className = 'citation-favicon';

        const fallback = document.createElement('span');
        fallback.className = 'citation-favicon-fallback';
        fallback.textContent = (domain || title).charAt(0).toUpperCase();
        favicon.appendChild(fallback);

        const titleWrapper = document.createElement('div');
        titleWrapper.className = 'citation-title-wrapper';

        const titleEl = document.createElement('div');
        titleEl.className = 'citation-title';
        titleEl.textContent = title;
        titleWrapper.appendChild(titleEl);

        const domainEl = document.createElement('div');
        domainEl.className = 'citation-domain';
        domainEl.textContent = domain || getCitationTranslation('chat_citations_unverified', 'Unverified source');
        titleWrapper.appendChild(domainEl);

        header.appendChild(favicon);
        header.appendChild(titleWrapper);
        if (safeUrl) {
            header.appendChild(createCitationExternalIcon());
        }
        card.appendChild(header);

        if (snippet) {
            const snippetEl = document.createElement('div');
            snippetEl.className = 'citation-snippet';
            snippetEl.textContent = snippet;
            card.appendChild(snippetEl);
        }

        citationsList.appendChild(card);
    });

    citationsSidebarContent.appendChild(citationsList);
}

function rerenderCurrentCitations() {
    if (!citationsSidebarContent) {
        return;
    }
    renderCitations(currentMessageCitations);
}

// Function to show citations for a specific message
function showCitationsForMessage(messageId) {
    const citations = extractCitationsFromMessage(messageId);
    currentMessageCitations = citations;
    renderCitations(citations);
    openCitationsSidebar();
}

window.openCitationsSidebar = openCitationsSidebar;
window.closeCitationsSidebar = closeCitationsSidebar;
window.toggleCitationsSidebar = toggleCitationsSidebar;
window.showCitationsForMessage = showCitationsForMessage;
window.extractCitationsFromMessage = extractCitationsFromMessage;
