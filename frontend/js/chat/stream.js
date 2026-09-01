

/**
 * Check whether a chat has an active generation and attach to it.
 *
 * Main-chat navigation passes the transcript request's abort signal and a
 * current-load predicate. This prevents a slower status response from attaching
 * an old generation after the user has already selected another conversation.
 */
async function checkAndAttachOngoingStream(chatId, options = {}) {
    const params = new URLSearchParams({ chat_id: String(chatId) });
    const res = await window.authedFetch(`/api/v1/chats/status?${params.toString()}`, {
        method: 'GET',
        signal: options?.signal,
    });
    if (!res.ok) return; // silently ignore
    const data = await res.json();
    if (typeof options?.isCurrent === 'function' && !options.isCurrent()) {
        return;
    }
    if (data && data.active && data.generation_id) {
        await sendMessage("", true, String(data.generation_id));
    }
}
