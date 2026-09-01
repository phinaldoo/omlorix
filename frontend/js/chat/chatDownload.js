// Fertig
const DOWNLOAD_FORMATS = {
    downloadChatPdfButton: 'pdf',
    downloadChatJsonButton: 'json',
    downloadChatWordButton: 'docx',
    downloadChatMarkdownButton: 'md',
    downloadChatTxtButton: 'txt'
};

function chatDownloadT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function chatDownloadFormatT(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(chatDownloadT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

function resolveFilename(response) {
    const match = (response.headers.get('content-disposition') || '')
        .match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i);
    if (!match) return null;
    try { if (match[1]) return decodeURIComponent(match[1]); } catch {}
    return match[2] || null;
}

function getChatTitle(chatId) {
    return chatId 
        ? document.querySelector(`#history .sidebar-element[data-chat-id="${CSS.escape(chatId)}"] p`)?.textContent?.trim()
        : null;
}

function sanitizeFilename(name) {
    return (String(name || 'Chat').trim().slice(0, 120)
        .replace(/[\/:*?"<>|]/g, '-')
        .replace(/\s+/g, ' ')) || 'Chat';
}

/**
 * Download a chat in the requested export format.
 *
 * Split-screen callers pass an explicit chat id because the main chat
 * container no longer identifies which visible conversation the user chose.
 * Existing full-screen callers can continue relying on the container fallback.
 */
async function downloadChat(format, options = {}) {
    const chatId = String(
        options.chatId
        || document.getElementById('chatContainer')?.dataset.chatId
        || ''
    ).trim();
    if (!chatId) return notifyError(chatDownloadT('chat_download_no_chat_selected', 'No chat selected to download.'));

    try {
        const params = new URLSearchParams({ chat_id: chatId, format });
        const response = await window.authedFetch(`/api/v1/chats/download?${params}`);

        if (response.status === 401) return redirectToLogin();
        if (!response.ok) {
            notifyError(chatDownloadFormatT(
                'chat_download_server_status',
                'Server responded with status {status}',
                { status: response.status }
            ));
            return;
        }

        const blob = await response.blob();
        const filename = resolveFilename(response)
            || `${sanitizeFilename(options.title || getChatTitle(chatId))}.${format}`;
        
        const url = URL.createObjectURL(blob);
        const a = Object.assign(document.createElement('a'), { href: url, download: filename });
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 100);
    } catch (error) {
        console.error('Chat download failed:', error);
        notifyError(chatDownloadT(
            'chat_download_unexpected_error',
            'An unexpected error occurred while downloading the chat.'
        ));
    }
}

// Panel-specific action menus need the same downloader without temporarily
// mutating the main chat container's global selection.
window.downloadChat = downloadChat;

document.addEventListener('DOMContentLoaded', () => {
    Object.entries(DOWNLOAD_FORMATS).forEach(([id, format]) => {
        const btn = document.getElementById(id);
        btn?.addEventListener('click', async () => {
            window.closeHeaderDropdown?.();
            btn.disabled = true;
            btn.setAttribute('aria-busy', 'true');
            try { await downloadChat(format); }
            finally { btn.disabled = false; btn.removeAttribute('aria-busy'); }
        });
    });
});
