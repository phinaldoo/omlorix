// Split Screen Manager internals. Load before ../splitScreen.js in the documented order.

// ───── URL Persistence ─────

function splitScreenInternalBuildSplitScreenURL() {
    const url = new URL(splitScreenInternalSPLIT_SCREEN_PATHNAME, window.location.origin);
    url.searchParams.set('split', '1');
    if (splitScreenInternalState.leftChatId) {
        url.searchParams.set('left', splitScreenInternalState.leftChatId);
    }
    if (splitScreenInternalState.rightChatId) {
        url.searchParams.set('right', splitScreenInternalState.rightChatId);
    }
    return url;
}

function splitScreenInternalUpdateURL({ push = false } = {}) {
    if (!splitScreenInternalState.active) {
        // Remove split params from URL if present
        const url = new URL(window.location.href);
        if (splitScreenInternalHasSplitRouteParams(url)) {
            url.searchParams.delete('split');
            url.searchParams.delete('left');
            url.searchParams.delete('right');
            history.replaceState(history.state, '', url.toString());
        }
        return;
    }

    const url = new URL(window.location.href);
    url.pathname = splitScreenInternalSPLIT_SCREEN_PATHNAME;
    // Keep an explicit marker even when both panels are new/temporary so a
    // refresh or copied URL can faithfully restore split mode.
    url.searchParams.set('split', '1');
    if (splitScreenInternalState.leftChatId) {
        url.searchParams.set('left', splitScreenInternalState.leftChatId);
    } else {
        url.searchParams.delete('left');
    }
    if (splitScreenInternalState.rightChatId) {
        url.searchParams.set('right', splitScreenInternalState.rightChatId);
    } else {
        url.searchParams.delete('right');
    }
    const nextState = { ...(history.state || {}), splitScreen: true };
    if (push && !splitScreenInternalHasSplitRouteParams(new URL(window.location.href))) {
        history.pushState(nextState, '', url.toString());
    } else {
        history.replaceState(nextState, '', url.toString());
    }
}

function splitScreenInternalSyncFromURL() {
    const url = new URL(window.location.href);
    const shouldRestoreSplit = splitScreenInternalHasSplitRouteParams(url);
    if (!shouldRestoreSplit) {
        if (splitScreenInternalAllowNextNonSplitHistoryNavigation) {
            // Guarded teardown has already completed. Let the main popstate
            // router load the destination that the user originally chose.
            splitScreenInternalAllowNextNonSplitHistoryNavigation = false;
            return false;
        }
        if (splitScreenInternalState.active) {
            // Popstate is not cancelable. Put split mode back into the
            // current history slot while the accessible confirmation runs,
            // then replay the Back navigation only after teardown succeeds.
            history.pushState(
                { ...(history.state || {}), splitScreen: true },
                '',
                splitScreenInternalBuildSplitScreenURL().toString()
            );
            if (!splitScreenInternalSplitHistoryExitInProgress) {
                splitScreenInternalSplitHistoryExitInProgress = true;
                Promise.resolve(splitScreenInternalRequestDisable({ skipLoadFallback: true }))
                    .then((canLeave) => {
                        if (!canLeave) return;
                        splitScreenInternalAllowNextNonSplitHistoryNavigation = true;
                        history.back();
                    })
                    .catch((error) => {
                        console.error('Failed to leave split screen during history navigation', error);
                    })
                    .finally(() => {
                        splitScreenInternalSplitHistoryExitInProgress = false;
                    });
            }
            return true;
        }
        return false;
    }

    if (!splitScreenInternalState.active) {
        splitScreenInternalEnable({ pushHistory: false, restoreCurrent: false });
    }

    const leftChatId = url.searchParams.get('left');
    let rightChatId = url.searchParams.get('right');
    if (leftChatId && rightChatId && String(leftChatId) === String(rightChatId)) {
        rightChatId = null;
        url.searchParams.delete('right');
        history.replaceState(history.state, '', url.toString());
        notifyWarning?.(splitScreenInternalSplitScreenT(
            'split_screen_duplicate_chat_warning',
            'That chat is already open in the other panel.'
        ));
    }

    if (leftChatId) {
        splitScreenInternalLoadChatIntoPanel(leftChatId, 'left', { force: true });
    } else {
        splitScreenInternalClearPanelState('left');
    }
    if (rightChatId) {
        splitScreenInternalLoadChatIntoPanel(rightChatId, 'right', { force: true });
    } else {
        splitScreenInternalClearPanelState('right');
    }
    return true;
}

/**
 * Open a sidebar chat in a requested panel, carrying the currently visible
 * main chat into the opposite panel when this starts split-screen mode.
 */
async function splitScreenInternalOpenSidebarChatInPanel(chatId, side, title = '', projectId = null) {
    const normalizedChatId = String(chatId || '').trim();
    if (!normalizedChatId || !['left', 'right'].includes(side)) return false;

    if (!splitScreenInternalState.active) {
        const chatContainer = document.getElementById('chatContainer');
        const currentChatId = String(chatContainer?.getAttribute('data-chat-id') || '').trim();
        const currentProjectId = String(chatContainer?.getAttribute('data-project-id') || '').trim();
        const currentChatTitle = splitScreenInternalGetSidebarChatTitle(currentChatId);
        const hasUnsavedMainConversation = !currentChatId && splitScreenInternalMainChatHasConversation();
        const mainGenerationActive = Boolean(
            window.isGenerating
            || chatContainer?.getAttribute('data-active-generation')
        );

        // Main temporary streams do not have a persisted chat id that can be
        // reattached to a split panel. Preserve the active view and ask the
        // user to finish or stop it before entering split-screen.
        if (!currentChatId && mainGenerationActive) {
            notifyWarning?.(splitScreenInternalSplitScreenT(
                'split_screen_wait_for_main_generation',
                'Wait for the current response to finish or stop it before opening split screen.'
            ));
            return false;
        }

        if (splitScreenInternalEnable({ restoreCurrent: false }) === false) {
            return false;
        }

        const mainContainer = document.getElementById('chatAreaContainer');
        if (currentChatId && currentChatId !== normalizedChatId) {
            if (mainContainer) mainContainer.innerHTML = '';
            const otherSide = splitScreenInternalGetOtherSide(side);
            const restored = await splitScreenInternalLoadChatIntoPanel(currentChatId, otherSide, {
                force: true,
                title: currentChatTitle,
                projectId: currentProjectId,
            });
            if (!restored) return false;
        } else if (hasUnsavedMainConversation) {
            const otherSide = splitScreenInternalGetOtherSide(side);
            if (!splitScreenInternalMoveMainConversationIntoPanel(otherSide)) {
                // Nothing has been cleared when preservation fails, so leave
                // split mode without replacing the user's main transcript.
                splitScreenInternalDisable({ skipLoadFallback: true });
                return false;
            }
        } else if (mainContainer) {
            mainContainer.innerHTML = '';
        }
    }

    return splitScreenInternalLoadChatIntoPanel(normalizedChatId, side, { title, projectId });
}

// ───── Responsive Handler ─────

function splitScreenInternalSetCompactPanel(side, { focusPanel = false } = {}) {
    const normalizedSide = side === 'right' ? 'right' : 'left';
    splitScreenInternalState.compactSide = normalizedSide;
    ['left', 'right'].forEach((candidateSide) => {
        const active = candidateSide === normalizedSide;
        const panel = candidateSide === 'left' ? splitScreenInternalGetLeftPanel() : splitScreenInternalGetRightPanel();
        const headerSlot = splitScreenInternalGetPanelHeaderSlot(candidateSide);
        panel?.classList.toggle('compact-active', active);
        panel?.setAttribute('aria-hidden', document.body.classList.contains('split-screen-compact') && !active ? 'true' : 'false');
        headerSlot?.classList.toggle('compact-active', active);
        headerSlot?.setAttribute('aria-hidden', document.body.classList.contains('split-screen-compact') && !active ? 'true' : 'false');
    });
    splitScreenInternalGetCompactTabs()?.querySelectorAll('[data-compact-panel]').forEach((tab) => {
        const active = tab.dataset.compactPanel === normalizedSide;
        tab.classList.toggle('active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
    });
    if (focusPanel) {
        splitScreenInternalGetPanelActionsButton(normalizedSide)?.focus?.();
    }
}

function splitScreenInternalSetCompactMode(enabled) {
    const compact = Boolean(enabled);
    document.body.classList.toggle('split-screen-compact', compact);
    splitScreenInternalGetCompactTabs()?.toggleAttribute('hidden', !compact);
    splitScreenInternalGetCompactDescription()?.toggleAttribute('hidden', !compact);
    [
        { panel: splitScreenInternalGetLeftPanel(), tabId: 'splitCompactTabLeft' },
        { panel: splitScreenInternalGetRightPanel(), tabId: 'splitCompactTabRight' },
    ].forEach(({ panel, tabId }) => {
        if (!panel) return;
        if (compact) {
            panel.setAttribute('role', 'tabpanel');
            panel.setAttribute('aria-labelledby', tabId);
        } else {
            panel.removeAttribute('role');
            panel.removeAttribute('aria-labelledby');
        }
    });
    if (compact) {
        splitScreenInternalSetCompactPanel(splitScreenInternalState.compactSide);
    } else {
        [splitScreenInternalGetLeftPanel(), splitScreenInternalGetRightPanel()].forEach((panel) => panel?.removeAttribute('aria-hidden'));
        [splitScreenInternalGetPanelHeaderSlot('left'), splitScreenInternalGetPanelHeaderSlot('right')].forEach((slot) => slot?.removeAttribute('aria-hidden'));
    }
    splitScreenInternalScheduleSplitHeaderGutterSync();
}

function splitScreenInternalHandleResize() {
    if (!splitScreenInternalState.active) {
        splitScreenInternalSetCompactMode(false);
        return;
    }
    const wrapperWidth = splitScreenInternalGetWrapper()?.getBoundingClientRect().width || window.innerWidth;
    splitScreenInternalSetCompactMode(wrapperWidth < splitScreenInternalMIN_SPLIT_SCREEN_WIDTH);
    splitScreenInternalScheduleSplitHeaderGutterSync();
}
