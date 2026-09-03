// Add event listeners to update button visibility when typing and persist draft
function handleChatInputChange() {
    toggleInputButtons();
    if (isChatInputDeferredInputWorkActive()) {
      return;
    }
    writeChatInputDraft(chatInput.value);
}

chatInput.addEventListener("input", handleChatInputChange);

/**
 * Return the menu to its base state after closing. Quick-pick rows commit
 * selections immediately, so closing the menu must not roll them back.
 */
function cleanupChatBoxFilesDropdown() {
  chatBoxAttachmentPanelNavigator?.reset({ focus: false });
  if (chatBoxFilesQuickpickSearch) chatBoxFilesQuickpickSearch.value = '';
  if (chatBoxFilesQuickpickList) chatBoxFilesQuickpickList.innerHTML = '';
  if (chatBoxFilesQuickpickEmpty) chatBoxFilesQuickpickEmpty.hidden = true;
  if (chatBoxChatReferencesQuickpickSearch) chatBoxChatReferencesQuickpickSearch.value = '';
  if (chatBoxChatReferencesQuickpickList) chatBoxChatReferencesQuickpickList.innerHTML = '';
  if (chatBoxChatReferencesQuickpickEmpty) chatBoxChatReferencesQuickpickEmpty.hidden = true;
}

/** Re-measure after asynchronous capability checks change visible actions. */
function scheduleChatBoxAttachmentPanelHeightSync() {
  if (!chatBoxFilesDropdownController?.isOpen()) {
    chatBoxAttachmentPanelNavigator?.syncHeight();
    return;
  }
  requestAnimationFrame(() => chatBoxAttachmentPanelNavigator?.syncHeight());
}

function toggleChatBoxFilesDropdown(open) {
  if (!chatBoxFilesDropdownController) return;
  const shouldOpen = typeof open === 'boolean' ? open : !chatBoxFilesDropdownController.isOpen();
  const wasOpen = chatBoxFilesDropdownController.isOpen();
  chatBoxFilesDropdownController[shouldOpen ? 'open' : 'close']({ reason: 'api' });
  if (!shouldOpen && !wasOpen) {
    cleanupChatBoxFilesDropdown();
  }
}

chatBoxFileButtons.forEach((btn) => {
  btn.addEventListener('click', (event) => {
    event.preventDefault();
    if (!btn || btn.disabled) {
      return;
    }
    toggleChatBoxFilesDropdown();
  });
});

if (chatBoxThinkingButton) {
  chatBoxThinkingButton.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (chatBoxThinkingButton.disabled) {
      return;
    }
    toggleChatBoxFilesDropdown(false);
    toggleChatBoxThinkingDropdown();
  });
}

if (chatBoxAddMeetingButton) {
  chatBoxAddMeetingButton.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    toggleChatBoxFilesDropdown(false);
    openMeetingModal();
  });
}

if (chatBoxUploadFromComputerButton) {
  chatBoxUploadFromComputerButton.addEventListener('click', (event) => {
    event.preventDefault();
    toggleChatBoxFilesDropdown(false);
    if (chatBoxFileInput) {
      chatBoxFileInput.click();
    }
  });
}

if (chatBoxQuickScreenCaptureButton) {
  chatBoxQuickScreenCaptureButton.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    void handleQuickScreenCaptureClick();
  });
}

if (chatBoxAddGoogleDriveButton) {
  chatBoxAddGoogleDriveButton.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!chatBoxConnectionsAvailabilityState.google_drive) {
      return;
    }
    toggleChatBoxFilesDropdown(false);
    void importGoogleDriveFilesIntoChat();
  });
}

if (chatBoxFilesDropdownMenu) {
  chatBoxFilesDropdownMenu.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'u') {
      event.preventDefault();
      toggleChatBoxFilesDropdown(false);
      chatBoxFileInput?.click();
    }
  });
}

if (chatBoxChatReferencesQuickpickSearch) {
  chatBoxChatReferencesQuickpickSearch.addEventListener('input', () => {
    if (quickpickChatsSearchTimer) window.clearTimeout(quickpickChatsSearchTimer);
    quickpickChatsSearchTimer = window.setTimeout(() => {
      chatBoxChatReferencesQuickpickScroll?.scrollTo?.(0, 0);
      void loadChatReferenceQuickpick({ query: chatBoxChatReferencesQuickpickSearch.value, forceRefresh: true });
    }, CHAT_UPLOADED_FILES_SEARCH_DEBOUNCE_MS);
  });
  chatBoxChatReferencesQuickpickSearch.addEventListener('click', (event) => {
    event.stopPropagation();
  });
}

if (chatBoxChatReferencesQuickpickScroll) {
  chatBoxChatReferencesQuickpickScroll.addEventListener('scroll', maybeLoadMoreChatReferenceQuickpick, { passive: true });
}

if (chatBoxFilesQuickpickScroll) {
  chatBoxFilesQuickpickScroll.addEventListener('scroll', () => maybeLoadMoreUploadedFiles('quickpick'), { passive: true });
}

if (chatBoxMeetingOverlay) {
  chatBoxMeetingOverlay.addEventListener('click', (event) => {
    if (event.target === chatBoxMeetingOverlay) {
      closeMeetingModal();
    }
  });
  chatBoxMeetingOverlay.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeMeetingModal();
    } else {
      trapMeetingModalFocus(event);
    }
  });
}

if (chatBoxMeetingCloseButton) {
  chatBoxMeetingCloseButton.addEventListener('click', (event) => {
    event.preventDefault();
    closeMeetingModal();
  });
}

if (chatBoxMeetingCancelButton) {
  chatBoxMeetingCancelButton.addEventListener('click', (event) => {
    event.preventDefault();
    closeMeetingModal();
  });
}

if (chatBoxMeetingSubmitButton) {
  chatBoxMeetingSubmitButton.addEventListener('click', (event) => {
    event.preventDefault();
    submitMeetingTranscript();
  });
}

[
  [chatBoxMeetingUploadOption, 'upload'],
  [chatBoxMeetingRecordOption, 'microphone'],
  [chatBoxMeetingScreenOption, 'screen'],
].forEach(([button, source]) => {
  if (!button) return;
  button.addEventListener('click', (event) => {
    event.preventDefault();
    setMeetingSource(source);
  });
  button.addEventListener('keydown', (event) => {
    const sourceButtons = [chatBoxMeetingUploadOption, chatBoxMeetingRecordOption, chatBoxMeetingScreenOption]
      .filter((candidate) => candidate && !candidate.disabled);
    const currentIndex = sourceButtons.indexOf(button);
    let nextIndex = currentIndex;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = (currentIndex + 1) % sourceButtons.length;
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = (currentIndex - 1 + sourceButtons.length) % sourceButtons.length;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = sourceButtons.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    sourceButtons[nextIndex]?.focus();
    sourceButtons[nextIndex]?.click();
  });
});

if (chatBoxMeetingCaptureToggleButton) {
  chatBoxMeetingCaptureToggleButton.addEventListener('click', async (event) => {
    event.preventDefault();
    if (meetingTranscriptState.isRecording) {
      stopMeetingCapture();
      return;
    }
    await startMeetingCapture();
  });
}

if (chatBoxMeetingCaptureDiscardButton) {
  chatBoxMeetingCaptureDiscardButton.addEventListener('click', async (event) => {
    event.preventDefault();
    await discardMeetingCapture();
  });
}

if (chatBoxMeetingDropzone) {
  chatBoxMeetingDropzone.addEventListener('click', (event) => {
    event.preventDefault();
    if (meetingTranscriptState.isSubmitting || meetingTranscriptState.isRecording) {
      return;
    }
    chatBoxMeetingFileInput?.click();
  });
  ['dragenter', 'dragover'].forEach((eventName) => {
    chatBoxMeetingDropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (meetingTranscriptState.isSubmitting || meetingTranscriptState.isRecording) {
        return;
      }
      chatBoxMeetingDropzone.classList.add('is-dragover');
    });
  });
  ['dragleave', 'dragend', 'drop'].forEach((eventName) => {
    chatBoxMeetingDropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      chatBoxMeetingDropzone.classList.remove('is-dragover');
      if (eventName === 'drop' && !meetingTranscriptState.isSubmitting) {
        handleMeetingFileSelection(Array.from(event.dataTransfer?.files || []));
      }
    });
  });
}

if (chatBoxMeetingFileInput) {
  chatBoxMeetingFileInput.addEventListener('change', (event) => {
    handleMeetingFileSelection(Array.from(event.target?.files || []));
    event.target.value = '';
  });
}

if (chatBoxMeetingClearSelectionButton) {
  chatBoxMeetingClearSelectionButton.addEventListener('click', (event) => {
    event.preventDefault();
    if (meetingTranscriptState.isSubmitting || meetingTranscriptState.isRecording) {
      return;
    }
    setMeetingSelectedFile(null);
  });
}

[chatBoxMeetingConsentCheckbox, chatBoxMeetingLegalBasisDetails, chatBoxMeetingRetentionDays].forEach((element) => {
  if (!element) return;
  element.addEventListener('input', () => updateMeetingSelectionUi());
  element.addEventListener('change', () => updateMeetingSelectionUi());
});

// The legal-basis field uses the shared custom-select widget, which emits a
// `customSelectChange` event (not native input/change) when its value updates.
if (chatBoxMeetingLegalBasis) {
  chatBoxMeetingLegalBasis.addEventListener('customSelectChange', () => updateMeetingSelectionUi());
}

if (chatBoxMeetingOverlay) {
  updateMeetingSourceOptions();
}

// Dynamic recorder text depends on both the selected capture source and the
// current language, so restore it after the general translator updates the DOM.
document.addEventListener('i18n:updated', updateMeetingCaptureUi);

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    const hadTransientSurfaceOpen = Boolean(
      chatBoxFilesDropdownMenu?.classList.contains('open')
      || chatBoxThinkingDropdownMenu?.classList.contains('open')
      || isMeetingModalOpen()
    );
    toggleChatBoxFilesDropdown(false);
    toggleChatBoxThinkingDropdown(false);
    if (isMeetingModalOpen()) {
      closeMeetingModal();
    }
    if (hadTransientSurfaceOpen) {
      // Mark Escape as consumed so the lower-priority global shortcut router
      // never interprets the same keypress as a request to stop generation.
      event.preventDefault();
      event.stopImmediatePropagation?.();
    }
  }
});

if (typeof window !== 'undefined') {
  window.getSelectedUploadedFileIdsForChat = getSelectedUploadedFileIds;
  window.setSelectedUploadedFileIdsForChat = setSelectedUploadedFileIds;
  window.clearChatUploadedFilesSelection = clearChatUploadedFilesSelection;
  window.getSelectedChatReferenceIds = getSelectedChatReferenceIds;
  window.getSelectedChatReferencePayload = getSelectedChatReferencePayload;
  window.fetchChatReferenceCandidates = fetchChatReferenceCandidates;
  window.clearAllChatReferenceAttachments = clearAllChatReferenceAttachments;
  window.removeChatReferenceAttachment = removeChatReferenceAttachment;
  window.addChatReferenceAttachment = addChatReferenceAttachment;
  window.ChatFilesMenu = {
    createMenuElement: createChatFilesMenuElement,
    createQuickpickCheckbox: createChatFilesQuickpickCheckbox,
    updateMenuVisibility: updateChatFilesMenuElementVisibility,
    getConnectionsAvailability: () => ({ ...chatBoxConnectionsAvailabilityState }),
    refreshConnectionsAvailability: loadChatBoxConnectionsAvailability,
    actions: {
      openMeeting: openMeetingModal,
      uploadFromComputer: () => chatBoxFileInput?.click(),
      quickScreenCapture: (options = {}) => handleQuickScreenCaptureClick(options),
      openGoogleDrive: (options = {}) => importGoogleDriveFilesIntoChat(options),
      openUploadedFiles: () => {
        toggleChatBoxFilesDropdown(true);
        chatBoxAttachmentPanelNavigator?.open('files');
      },
      openChatReferences: () => {
        toggleChatBoxFilesDropdown(true);
        chatBoxAttachmentPanelNavigator?.open('chats');
      },
    },
  };
  window.refreshChatUploadedFilesList = ({ force = false } = {}) => {
    if (force && chatBoxAttachmentPanelNavigator?.activePanel === 'files') {
      cachedUploadedFiles.lastFetched = 0;
      loadUploadedFilesForChatBox({ forceRefresh: true, surface: 'quickpick' });
    }
  };
}

if (chatBoxFileInput) {
  chatBoxFileInput.addEventListener('change', async (event) => {
    const files = Array.from(event.target.files || []);
    if (files.length && typeof window.handleChatFileSelection === 'function') {
      const unsupported = files.filter((file) => !isFileSupportedForCurrentModel(file));
      try {
        await window.handleChatFileSelection(files);
      } catch (error) {
        console.error('handleChatFileSelection failed', error);
      }

      if (unsupported.length && typeof notifyWarning === 'function') {
        const label = unsupported.map((f) => f?.name || 'file').slice(0, 6).join(', ');
        const suffix = unsupported.length > 6 ? ` (+${unsupported.length - 6} more)` : '';
        notifyWarning(`Some selected files are not natively supported by this model. They were uploaded as non-native file references instead: ${label}${suffix}`);
      }
    }
    event.target.value = '';
  });
}

window.addEventListener('modelSupportedFileFormats:updated', () => {
  try {
    updateChatBoxUploadAcceptAttribute();
    renderQuickpickList(chatBoxFilesQuickpickSearch?.value || '');
  } catch (_) {
    // no-op
  }
});

// Initial accept attribute
updateChatBoxUploadAcceptAttribute();

// One window-level listener set is enough: drag events bubble from both the
// page and the active full-screen overlay, while nested dedicated dropzones
// can still stop propagation and retain ownership of their own file flow.
bindChatDropZone(window);
window.addEventListener('dragend', resetChatDropState);
window.addEventListener('blur', resetChatDropState);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    resetChatDropState();
  }
});
bindChatReferenceDropZone(chatBoxArea);
bindChatReferenceDropZone(document.getElementById('chatBox'));
bindChatReferenceDropZone(document.getElementById('chatBoxInput'));

/**
 * Check generation state without triggering cancellation. The prioritized
 * Escape manager uses this predicate before deciding which surface owns Esc.
 */
function canCancelActiveGeneration({ scope = 'all' } = {}) {
  const cancelTargetKey = resolveChatSendCancellationTarget(scope);
  if (isChatSendCancellationPending(cancelTargetKey)) {
    return false;
  }
  const split = window.SplitScreenManager;
  const isSplit = Boolean(split && split.active === true);
  const splitTarget = isSplit && scope === 'target'
    ? (typeof split.sendTarget === 'string' ? split.sendTarget : 'both')
    : 'both';
  return isSplit
    ? split.isSendTargetGenerating(splitTarget) === true
    : window.isGenerating === true;
}

function cancelActiveGeneration({ showVisualFeedback = true, scope = 'all' } = {}) {
  const cancelTargetKey = resolveChatSendCancellationTarget(scope);
  if (isChatSendCancellationPending(cancelTargetKey)) {
    return true;
  }

  const split = window.SplitScreenManager;
  const isSplit = Boolean(split && split.active === true);
  const shouldCancelTargetOnly = isSplit && scope === 'target';
  const splitCancelTarget = shouldCancelTargetOnly
    ? (typeof split.sendTarget === 'string' ? split.sendTarget : 'both')
    : 'both';
  const hasGeneration = isSplit
    ? (split.isSendTargetGenerating(splitCancelTarget) === true)
    : (window.isGenerating === true);

  if (!hasGeneration) {
    return false;
  }

  if (showVisualFeedback) {
    setChatSendCancellationPending(true, cancelTargetKey);
    try {
      toggleInputButtons();
    } catch (_) {}
  }

  const revertVisualFeedback = () => {
    if (!showVisualFeedback) return;
    setChatSendCancellationPending(false, cancelTargetKey);
    try {
      toggleInputButtons();
    } catch (_) {}
  };

  const invokeInlineCancel = () => {
    (async () => {
      let cancelAccepted = false;
      try {
        if (isSplit && typeof split.cancelSendTargetGeneration === 'function') {
          cancelAccepted = await split.cancelSendTargetGeneration(splitCancelTarget);
          return;
        }
        const genId = window.currentGenerationId;
        if (!genId) {
          window.pendingCancelGeneration = true;
          cancelAccepted = true;
          return;
        }
        const params = new URLSearchParams({ generation_id: String(genId) });
        const response = await window.authedFetch(`/api/v1/chats/cancel?${params.toString()}`, {
          method: 'POST',
          headers: {
            'accept': 'application/json',
          },
          body: '',
        });
        cancelAccepted = Boolean(response?.ok);
      } catch (error) {
        console.error('Failed to cancel active generation', error);
      } finally {
        if (!cancelAccepted) {
          revertVisualFeedback();
        }
      }
    })();
  };

  if (!isSplit && typeof window.cancelGeneration === 'function') {
    try {
      window.cancelGeneration();
    } catch (error) {
      console.error('Failed to cancel active generation', error);
      revertVisualFeedback();
    }
  } else {
    invokeInlineCancel();
  }

  return true;
}

// The command palette and the low-priority Escape router use the exact same
// cancellation path as the visible Stop button. Keeping one implementation
// prevents split-screen target and pending-state behavior from drifting.
if (typeof window !== 'undefined') {
  window.canCancelActiveGeneration = canCancelActiveGeneration;
  window.cancelActiveGeneration = cancelActiveGeneration;
}

if (chatStopButton) {
  chatStopButton.addEventListener('click', (event) => {
    event.preventDefault();
    if (typeof event.stopPropagation === 'function') {
      event.stopPropagation();
    }
    if (isChatSendCancellationPending()) {
      return;
    }
    cancelActiveGeneration({ showVisualFeedback: true, scope: 'target' });
  });
}

// Clear input after sending
sendButton.addEventListener("click", () => {
    if (isChatSendCancellationPending()) {
        return;
    }

    if (isCurrentSendContextGenerating()) {
        if (hasQueueableChatContent()) {
            if (tryQueueCurrentInput({ showOverlay: false })) {
                resetDoubleEnterTimer();
                toggleInputButtons();
                return;
            }
        }
        if (cancelActiveGeneration({ showVisualFeedback: true, scope: 'target' })) {
            return;
        }
        return;
    }

    dispatchCurrentDraftMessage();
});

if (callButton) {
  callButton.addEventListener('click', async (event) => {
    event.preventDefault();
    if (!window.realtimeCall) {
      notifyError?.(getChatI18nString('chat_realtime_controller_unavailable', 'Realtime call controller is not initialized.'));
      return;
    }
    if (isChatUploading()) {
      showUploadInProgressWarning();
      return;
    }
    if (isRealtimeCallActive() || isRealtimeCallConnecting()) {
      await window.realtimeCall.stop({ reason: 'chatbox_call_button' });
      toggleInputButtons();
      return;
    }
    if (typeof window.realtimeCall.activateCallRoute === 'function') {
      await window.realtimeCall.activateCallRoute();
    } else {
      const started = await window.realtimeCall.start();
      if (!started) {
        return;
      }
    }
    toggleInputButtons();
  });
}

// Submit with Enter by default, or Ctrl/Cmd+Enter when the user enables modifier-only sending.
let isComposing = false;
const DOUBLE_ENTER_CANCEL_WINDOW_MS = 1200;
// These timer fields are intentionally `var` so startup flows can safely call
// reset helpers before this section finishes evaluating without hitting a TDZ.
var lastEnterKeyTime = 0;
var doubleEnterCancelTimeoutId = 0;

function isCtrlEnterToSendEnabled() {
  if (typeof window !== 'undefined' && typeof window.getChatBooleanSetting === 'function') {
    return window.getChatBooleanSetting('ctrl_enter_to_send', false);
  }

  try {
    const stored = localStorage.getItem('ctrl_enter_to_send');
    if (stored === 'true' || stored === '1') return true;
    if (stored === 'false' || stored === '0') return false;
  } catch (_) {
    // Ignore localStorage access issues and fall back below.
  }

  if (typeof window !== 'undefined' && window.chatSetup && Object.prototype.hasOwnProperty.call(window.chatSetup, 'ctrl_enter_to_send')) {
    return Boolean(window.chatSetup.ctrl_enter_to_send);
  }

  return false;
}

function shouldTriggerChatSubmitOnEnterEvent(event, { isComposingEvent = false } = {}) {
  if (
    !event ||
    event.key !== 'Enter' ||
    event.shiftKey ||
    event.altKey ||
    isComposingEvent ||
    event.repeat
  ) {
    return false;
  }

  if (isCtrlEnterToSendEnabled()) {
    return Boolean(event.ctrlKey || event.metaKey);
  }

  return !event.ctrlKey && !event.metaKey;
}

if (typeof window !== 'undefined') {
  window.shouldTriggerChatSubmitOnEnterEvent = shouldTriggerChatSubmitOnEnterEvent;
}

if (chatInput) {
  chatInput.addEventListener('compositionstart', () => { isComposing = true; });
  chatInput.addEventListener('compositionend', () => { isComposing = false; });
  // ---- Large Paste Modal ----
  let _largePasteOverlay = null;
  let _largePasteCurrentText = '';
  let _largePastePreviousFocus = null;

  function _getLargePasteTranslation(key, fallback) {
    return getChatI18nString(key, fallback);
  }

  function _formatLargePasteTranslation(key, fallback, vars) {
    if (typeof window.formatTranslation === 'function') {
      return window.formatTranslation(key, fallback, vars);
    }
    const template = _getLargePasteTranslation(key, fallback);
    return String(template).replace(/\{(\w+)\}/g, (_, token) => String(vars?.[token] ?? ''));
  }

  function _formatLargePasteStatCount(value) {
    const locale = document.documentElement?.lang || undefined;
    return Number(value || 0).toLocaleString(locale);
  }

  function _updateLargePasteStats(overlay, text = _largePasteCurrentText) {
    if (!overlay || typeof text !== 'string' || !text.length) {
      return;
    }

    const chars = text.length;
    const lines = text.split('\n').length;
    const bytes = new Blob([text]).size;
    const charStat = overlay.querySelector('[data-large-paste-stat="chars"]');
    const lineStat = overlay.querySelector('[data-large-paste-stat="lines"]');
    const sizeStat = overlay.querySelector('[data-large-paste-stat="size"]');
    if (charStat) {
      charStat.textContent = _formatLargePasteTranslation(
        'chat_large_paste_stat_characters',
        '{count} characters',
        { count: _formatLargePasteStatCount(chars) },
      );
    }
    if (lineStat) {
      lineStat.textContent = _formatLargePasteTranslation(
        'chat_large_paste_stat_lines',
        '{count} lines',
        { count: _formatLargePasteStatCount(lines) },
      );
    }
    if (sizeStat) sizeStat.textContent = _formatPasteSize(bytes);
  }

  function _updateLargePasteModalTranslations() {
    const overlay = _largePasteOverlay;
    if (!overlay) return;

    const dialog = overlay.querySelector('.large-paste-modal');
    const titleText = overlay.querySelector('[data-large-paste-role="title-text"]');
    const closeButton = overlay.querySelector('[data-large-paste-action="cancel"]');
    const info = overlay.querySelector('[data-large-paste-role="info"]');
    const pasteText = overlay.querySelector('[data-large-paste-role="paste-action-text"]');
    const fileText = overlay.querySelector('[data-large-paste-role="file-action-text"]');

    const title = _getLargePasteTranslation('chat_large_paste_title', 'Large text pasted');
    if (dialog) dialog.setAttribute('aria-label', title);
    if (titleText) titleText.textContent = title;
    if (closeButton) {
      closeButton.setAttribute('aria-label', _getLargePasteTranslation('chat_large_paste_close', 'Cancel'));
    }
    if (info) {
      info.textContent = _getLargePasteTranslation(
        'chat_large_paste_info',
        'The pasted text is quite long. You can paste it normally or attach it as a text file to keep your chat cleaner.',
      );
    }
    if (pasteText) {
      pasteText.textContent = _getLargePasteTranslation('chat_large_paste_action_paste', 'Paste as text');
    }
    if (fileText) {
      fileText.textContent = _getLargePasteTranslation('chat_large_paste_action_file', 'Attach as file');
    }
    _updateLargePasteStats(overlay);
  }

  function _getLargePasteOverlay() {
    if (_largePasteOverlay) return _largePasteOverlay;
    const overlay = document.createElement('div');
    overlay.className = 'large-paste-modal-overlay shared-modal-overlay';
    overlay.hidden = true;
    overlay.setAttribute('aria-hidden', 'true');
    overlay.innerHTML = `
      <div class="large-paste-modal shared-modal shared-modal--fit" role="dialog" aria-modal="true" aria-label="" tabindex="-1">
        <div class="large-paste-modal__header shared-modal-header shared-modal-header--main">
          <span class="large-paste-modal__title shared-modal-title">
            ${Icons.file}
          </span>
          <button type="button" class="om-button shared-modal-close" aria-label="" data-large-paste-action="cancel">
            ${Icons.close}
          </button>
        </div>
        <div class="large-paste-modal__body shared-modal-body">
          <p class="large-paste-modal__info" data-large-paste-role="info"></p>
          <div class="large-paste-modal__preview">
            <pre class="large-paste-modal__preview-text"></pre>
          </div>
          <div class="large-paste-modal__stats">
            <span class="large-paste-modal__stat" data-large-paste-stat="chars"></span>
            <span class="large-paste-modal__stat" data-large-paste-stat="lines"></span>
            <span class="large-paste-modal__stat" data-large-paste-stat="size"></span>
          </div>
        </div>
        <div class="large-paste-modal__actions shared-modal-footer">
          <button type="button" class="om-button border cancel" data-large-paste-action="paste">
            ${Icons.textLines}
            <span data-large-paste-role="paste-action-text"></span>
          </button>
          <button type="button" class="om-button border submit" data-large-paste-action="file">
            ${Icons.file}
            <span data-large-paste-role="file-action-text"></span>
          </button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    _largePasteOverlay = overlay;
    _updateLargePasteModalTranslations();

    overlay.addEventListener('click', (e) => {
      const action = e.target.closest('[data-large-paste-action]')?.dataset.largePasteAction;
      if (action) {
        _resolveLargePaste(action);
      } else if (e.target === overlay) {
        _resolveLargePaste('cancel');
      }
    });

    return overlay;
  }

  let _largePasteResolver = null;

  function _resolveLargePaste(action) {
    const overlay = _largePasteOverlay;
    if (overlay) {
      overlay.classList.remove('open');
      overlay.setAttribute('aria-hidden', 'true');
      overlay.hidden = true;
      document.body.classList.remove('modal-open');
    }
    document.removeEventListener('keydown', _largePasteEscHandler);
    if (_largePasteResolver) {
      _largePasteResolver(action);
      _largePasteResolver = null;
    }
    const previousFocus = _largePastePreviousFocus;
    _largePastePreviousFocus = null;
    if (previousFocus?.isConnected) {
      previousFocus.focus({ preventScroll: true });
    }
  }

  function _largePasteEscHandler(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      _resolveLargePaste('cancel');
      return;
    }
    if (e.key === 'Tab' && _largePasteOverlay && !_largePasteOverlay.hidden) {
      const dialog = _largePasteOverlay.querySelector('[role="dialog"]');
      const focusable = Array.from(dialog?.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
      ) || []).filter((element) => !element.hidden && element.getClientRects().length > 0);
      if (!focusable.length) {
        e.preventDefault();
        dialog?.focus({ preventScroll: true });
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  function _formatPasteSize(bytes) {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${bytes} B`;
  }

  function _showLargePasteModal(text) {
    return new Promise((resolve) => {
      _largePasteResolver = resolve;
      _largePastePreviousFocus = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
      _largePasteCurrentText = String(text || '');
      const overlay = _getLargePasteOverlay();
      const previewEl = overlay.querySelector('.large-paste-modal__preview-text');
      if (previewEl) {
        previewEl.textContent = text.slice(0, 800);
      }
      _updateLargePasteModalTranslations();

      overlay.hidden = false;
      overlay.classList.add('open');
      overlay.setAttribute('aria-hidden', 'false');
      document.body.classList.add('modal-open');
      document.addEventListener('keydown', _largePasteEscHandler);

      requestAnimationFrame(() => {
        const fileBtn = overlay.querySelector('[data-large-paste-action="file"]');
        if (fileBtn) fileBtn.focus();
      });
    });
  }

  document.addEventListener('i18n:updated', () => {
    _updateLargePasteModalTranslations();
  });

  function _insertTextIntoChatInput(text) {
    const textValue = String(text || '');
    const shouldDeferInputWork = textValue.length >= LARGE_PASTE_CHAR_THRESHOLD;
    let handled = false;
    try {
      chatInput.focus();
      if (!shouldDeferInputWork && typeof document.execCommand === 'function') {
        handled = document.execCommand('insertText', false, textValue);
      }
    } catch (_) {}
    if (!handled) {
      try {
        const start = chatInput.selectionStart;
        const end = chatInput.selectionEnd;
        if (typeof chatInput.setRangeText === 'function') {
          chatInput.setRangeText(textValue, start, end, 'end');
        } else {
          const value = chatInput.value;
          const nextValue = value.slice(0, start) + textValue + value.slice(end);
          chatInput.value = nextValue;
          const pos = start + textValue.length;
          chatInput.setSelectionRange(pos, pos);
        }
      } catch (_) {
        chatInput.value += textValue;
      }
    }
    if (shouldDeferInputWork) {
      deferNextChatInputExpensiveInputWork = true;
    }
    try {
      chatInput.dispatchEvent(new Event('input', { bubbles: true }));
    } finally {
      if (shouldDeferInputWork) {
        deferNextChatInputExpensiveInputWork = false;
        scheduleDeferredChatInputWork('large-paste');
      }
    }
    if (!shouldDeferInputWork) {
      requestAnimationFrame(() => { chatInput.scrollTop = chatInput.scrollHeight; });
    }
  }

  function _attachTextAsFile(text) {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const timestamp = `${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
    const filename = `pasted_text_${timestamp}.txt`;
    const blob = new Blob([text], { type: 'text/plain' });
    const file = new File([blob], filename, { type: 'text/plain', lastModified: now.getTime() });
    if (typeof window.handleChatFileSelection === 'function') {
      window.handleChatFileSelection([file]);
    }
  }

  chatInput.addEventListener('paste', (event) => {
    if (!event || !event.clipboardData) {
      return;
    }
    const clipboardFiles = Array.from(event.clipboardData.files || []).filter((file) => file && file.size > 0);
    if (clipboardFiles.length) {
      event.preventDefault();
      try {
        if (typeof event.stopPropagation === 'function') {
          event.stopPropagation();
        }
        if (typeof event.stopImmediatePropagation === 'function') {
          event.stopImmediatePropagation();
        }
      } catch (_) {}
      if (typeof window.handleChatFileSelection === 'function') {
        try {
          const result = window.handleChatFileSelection(clipboardFiles);
          if (result && typeof result.catch === 'function') {
            result.catch((error) => { console.error('handleChatFileSelection paste error', error); });
          }
        } catch (error) {
          console.error('handleChatFileSelection paste error', error);
        }
      }
      const textData = event.clipboardData.getData('text');
      if (typeof textData === 'string' && textData.length) {
        _insertTextIntoChatInput(textData);
      }
      return;
    }

    // Text-only paste – check if it's large
    const textData = event.clipboardData.getData('text');
    if (typeof textData !== 'string' || !textData.length) {
      return;
    }
    if (textData.length < LARGE_PASTE_CHAR_THRESHOLD) {
      return; // let the browser handle normal pastes
    }

    // Large text – show the modal
    event.preventDefault();
    _showLargePasteModal(textData).then((action) => {
      if (action === 'paste') {
        _insertTextIntoChatInput(textData);
      } else if (action === 'file') {
        _attachTextAsFile(textData);
      }
      // 'cancel' → do nothing
    });
  });
  chatInput.addEventListener('keydown', (e) => {
    if (e.__skillMentionHandled) {
      return;
    }
    if (!shouldTriggerChatSubmitOnEnterEvent(e, { isComposingEvent: isComposing })) {
      return;
    }

    e.preventDefault();
    if (typeof e.stopPropagation === 'function') {
      e.stopPropagation();
    }
    if (typeof e.stopImmediatePropagation === 'function') {
      e.stopImmediatePropagation();
    }
    if (isChatUploading()) {
      showUploadInProgressWarning();
      return;
    }
    const isGeneratingNow = isCurrentSendContextGenerating();
    if (isGeneratingNow && isChatSendCancellationPending()) {
      return;
    }
    if (isGeneratingNow) {
      if (hasQueueableChatContent()) {
        if (tryQueueCurrentInput({ showOverlay: false })) {
          resetDoubleEnterTimer();
          return;
        }
      }
      handleDoubleEnterCancel();
      return;
    }
    dispatchCurrentDraftMessage();
  });
}

function handleDoubleEnterCancel() {
  if (!isCurrentSendContextGenerating()) {
    resetDoubleEnterTimer();
    return false;
  }
  if (isChatSendCancellationPending()) {
    resetDoubleEnterTimer();
    return true;
  }
  const now = Date.now();
  if (lastEnterKeyTime && (now - lastEnterKeyTime) <= DOUBLE_ENTER_CANCEL_WINDOW_MS) {
    resetDoubleEnterTimer();
    cancelActiveGeneration({ showVisualFeedback: true, scope: 'target' });
    return true;
  }
  lastEnterKeyTime = now;
  setSendButtonRequestingCancel(true);
  if (doubleEnterCancelTimeoutId) {
    clearTimeout(doubleEnterCancelTimeoutId);
  }
  doubleEnterCancelTimeoutId = setTimeout(() => {
    resetDoubleEnterTimer();
  }, DOUBLE_ENTER_CANCEL_WINDOW_MS);
  return false;
}

function resetDoubleEnterTimer() {
  if (doubleEnterCancelTimeoutId) {
    clearTimeout(doubleEnterCancelTimeoutId);
    doubleEnterCancelTimeoutId = 0;
  }
  lastEnterKeyTime = 0;
  setSendButtonRequestingCancel(false);
}

const shouldBypassGlobalEnter = (target) => {
  if (!target) return true;
  if (target === chatInput) return true;
  if (target instanceof HTMLInputElement) return true;
  if (target instanceof HTMLTextAreaElement) return true;
  const tagName = target.tagName ? target.tagName.toLowerCase() : '';
  if (tagName === 'select' || tagName === 'button') {
    return true;
  }
  if (typeof target.isContentEditable === 'boolean' && target.isContentEditable) {
    return true;
  }
  return false;
};

document.addEventListener('keydown', (event) => {
  if (
    event.key !== 'Enter' ||
    event.shiftKey || event.altKey || event.ctrlKey || event.metaKey ||
    isComposing ||
    event.repeat
  ) {
    return;
  }
  if (shouldBypassGlobalEnter(event.target)) {
    return;
  }
  const cancelled = handleDoubleEnterCancel();
  if (cancelled) {
    event.preventDefault();
  }
}, true);

window.addEventListener('realtime:state', handleRealtimeDictationStateChange);
