// ------------------------------------------------------------
// Archived Chats Modal
// ------------------------------------------------------------

const archivedChatsOverlay = document.getElementById('archivedChatsOverlay');
const archivedChatsModal = document.getElementById('archivedChatsModal');
const archivedChatsCloseButton = document.getElementById('archivedChatsCloseButton');
const archivedChatsLoading = document.getElementById('archivedChatsLoading');
const archivedChatsEmpty = document.getElementById('archivedChatsEmpty');
const archivedChatsList = document.getElementById('archivedChatsList');
var chatTitleUtils = window.ChatTitleUtils || {};
let archivedChatsCloseSequence = 0;
let archivedChatsCloseTimer = 0;
let archivedChatsPreviouslyFocused = null;
let archivedChatsPreviousBodyOverflow = null;

function archivedChatsT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}


function setSectionVisibility(element, isVisible) {
    if (!element) return;

    const defaultDisplay = element.dataset.defaultDisplay || window.getComputedStyle(element).display || 'flex';
    if (!element.dataset.defaultDisplay && defaultDisplay !== 'none') {
        element.dataset.defaultDisplay = defaultDisplay;
    }

    if (isVisible) {
        element.hidden = false;
        element.removeAttribute('aria-hidden');
        element.style.display = element.dataset.defaultDisplay || 'flex';
    } else {
        element.hidden = true;
        element.setAttribute('aria-hidden', 'true');
        element.style.display = 'none';
    }
}



function openArchivedChatsModal() {
    archivedChatsPreviouslyFocused = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    if (archivedChatsPreviousBodyOverflow === null) {
        archivedChatsPreviousBodyOverflow = document.body.style.overflow;
    }
    archivedChatsCloseSequence += 1;
    window.clearTimeout(archivedChatsCloseTimer);
    archivedChatsCloseTimer = 0;
    archivedChatsOverlay.classList.remove('is-closing');
    archivedChatsOverlay.hidden = false;
    archivedChatsOverlay.inert = false;
    archivedChatsOverlay.classList.add('open');
    archivedChatsOverlay.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    window.requestAnimationFrame(() => archivedChatsCloseButton?.focus({ preventScroll: true }));
    loadArchivedChats();
}



function closeArchivedChatsModal() {
    if (!archivedChatsOverlay.classList.contains('open')) return;

    const closeSequence = ++archivedChatsCloseSequence;
    archivedChatsOverlay.classList.remove('open');
    archivedChatsOverlay.classList.add('is-closing');
    archivedChatsOverlay.setAttribute('aria-hidden', 'true');
    archivedChatsOverlay.inert = true;
    if (archivedChatsPreviousBodyOverflow !== null) {
        document.body.style.overflow = archivedChatsPreviousBodyOverflow;
        archivedChatsPreviousBodyOverflow = null;
    }
    if (archivedChatsPreviouslyFocused && document.contains(archivedChatsPreviouslyFocused)) {
        archivedChatsPreviouslyFocused.focus({ preventScroll: true });
    }
    archivedChatsPreviouslyFocused = null;

    // Keep the shared shell mounted until its standard exit motion finishes.
    // The sequence guard prevents an older close from hiding a reopened modal.
    let handleAnimationEnd = null;
    const finishClose = () => {
        if (closeSequence !== archivedChatsCloseSequence || archivedChatsOverlay.classList.contains('open')) return;
        window.clearTimeout(archivedChatsCloseTimer);
        archivedChatsCloseTimer = 0;
        if (handleAnimationEnd) {
            archivedChatsModal?.removeEventListener('animationend', handleAnimationEnd);
        }
        archivedChatsOverlay.classList.remove('is-closing');
        archivedChatsOverlay.hidden = true;
    };
    handleAnimationEnd = (event) => {
        if (event.target !== archivedChatsModal) return;
        archivedChatsModal.removeEventListener('animationend', handleAnimationEnd);
        finishClose();
    };
    archivedChatsModal?.addEventListener('animationend', handleAnimationEnd);
    archivedChatsCloseTimer = window.setTimeout(finishClose, 240);
}



async function loadArchivedChats() {
    setSectionVisibility(archivedChatsLoading, true);
    setSectionVisibility(archivedChatsEmpty, false);
    setSectionVisibility(archivedChatsList, false);
    archivedChatsList.innerHTML = '';

    try {
        const response = await window.authedFetch('/api/v1/chats/archived', { method: 'GET' });
        if (!response.ok) {
            throw new Error(archivedChatsT('archived_chats_fetch_failed', 'Failed to fetch archived chats'));
        }
        const chats = await response.json();

        setSectionVisibility(archivedChatsLoading, false);

        if (chats.length === 0) {
            setSectionVisibility(archivedChatsEmpty, true);
            setSectionVisibility(archivedChatsList, false);
            return;
        }

        setSectionVisibility(archivedChatsEmpty, false);
        setSectionVisibility(archivedChatsList, true);
        renderArchivedChats(chats);
    } catch (error) {
        console.error('Error loading archived chats:', error);
        setSectionVisibility(archivedChatsLoading, false);
        setSectionVisibility(archivedChatsEmpty, true);
        setSectionVisibility(archivedChatsList, false);
        if (typeof notifyError === 'function') {
            notifyError(archivedChatsT('archived_chats_load_failed', 'Failed to load archived chats'));
        }
    }
}



function renderArchivedChats(chats) {
    archivedChatsList.innerHTML = '';
    setSectionVisibility(archivedChatsList, chats.length > 0);
    
    chats.forEach(chat => {
        const item = document.createElement('div');
        item.className = 'archived-chat-item';
        item.dataset.chatId = chat.id;
        item.dataset.chatSource = chatTitleUtils.isAutomationChat?.(chat)
            ? 'automation'
            : String(chat.source ?? chat?.meta?.source ?? '').trim().toLowerCase();
        
        const title = chatTitleUtils.getChatDisplayTitle?.(chat, archivedChatsT('chat_reference_untitled', 'Untitled chat'))
            || archivedChatsT('chat_reference_untitled', 'Untitled chat');
        const date = new Date(chat.last_updated_at).toLocaleDateString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });

        item.innerHTML = `
            <div class="archived-chat-info">
                <p class="archived-chat-title chat-title-with-badge"></p>
                <p class="archived-chat-date">${date}</p>
            </div>
            <div class="archived-chat-actions">
                <button class="archived-chat-btn unarchive" data-chat-id="${chat.id}" title="${escapeHtml(archivedChatsT('archived_chats_unarchive_title', 'Unarchive chat'))}">
                    ${Icons.archive}
                    <span>${escapeHtml(archivedChatsT('archived_chats_unarchive_action', 'Unarchive'))}</span>
                </button>
            </div>
        `;
        const titleEl = item.querySelector('.archived-chat-title');
        if (typeof chatTitleUtils.setChatTitleElement === 'function') {
            chatTitleUtils.setChatTitleElement(titleEl, chat, { fallbackTitle: archivedChatsT('chat_reference_untitled', 'Untitled chat') });
        } else if (titleEl) {
            titleEl.textContent = title;
        }

        const unarchiveBtn = item.querySelector('.archived-chat-btn.unarchive');
        unarchiveBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            await unarchiveChat(chat.id, item);
        });

        archivedChatsList.appendChild(item);
    });
}



async function unarchiveChat(chatId, itemElement) {
    const btn = itemElement.querySelector('.archived-chat-btn.unarchive');
    btn.disabled = true;
    
    try {
        const response = await window.authedFetch('/api/v1/chats/unarchive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chat_id: chatId })
        });

        if (!response.ok) {
            throw new Error(archivedChatsT('archived_chats_unarchive_failed', 'Failed to unarchive chat'));
        }

        itemElement.style.opacity = '0';
        itemElement.style.transform = 'translateX(20px)';
        itemElement.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
        
        setTimeout(() => {
            itemElement.remove();

            const remainingItems = archivedChatsList.querySelectorAll('.archived-chat-item');
            if (remainingItems.length === 0) {
                setSectionVisibility(archivedChatsList, false);
                setSectionVisibility(archivedChatsEmpty, true);
            }
        }, 200);

        if (typeof initChatList === 'function') {
            initChatList();
        }
        
        if (typeof notifySuccess === 'function') {
            notifySuccess(archivedChatsT('archived_chats_unarchive_success', 'Chat unarchived'));
        }
    } catch (error) {
        console.error('Error unarchiving chat:', error);
        btn.disabled = false;
        if (typeof notifyError === 'function') {
            notifyError(archivedChatsT('archived_chats_unarchive_failed', 'Failed to unarchive chat'));
        }
    }
}



function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

if (archivedChatsCloseButton) {
    archivedChatsCloseButton.addEventListener('click', closeArchivedChatsModal);
}

if (archivedChatsOverlay) {
    archivedChatsOverlay.addEventListener('click', (e) => {
        if (e.target === archivedChatsOverlay) {
            closeArchivedChatsModal();
        }
    });

    archivedChatsOverlay.addEventListener('keydown', (event) => {
        if (event.key !== 'Tab' || !archivedChatsOverlay.classList.contains('open')) return;
        const focusable = Array.from(archivedChatsModal.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        )).filter((element) => !element.hidden && element.getClientRects().length > 0);
        if (!focusable.length) {
            event.preventDefault();
            archivedChatsModal.focus();
            return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });
}

if (typeof window.registerEscapeHandler === 'function') {
    window.registerEscapeHandler({
        id: 'archived-chats-modal',
        priority: 180,
        isActive: () => archivedChatsOverlay.classList.contains('open'),
        close: closeArchivedChatsModal,
    });
}
