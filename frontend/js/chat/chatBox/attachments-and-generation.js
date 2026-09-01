const CHAT_INPUT_STORAGE_KEY = 'chat_box_input_draft';
const CHAT_INPUT_STORAGE_KEY_PREFIX = `${CHAT_INPUT_STORAGE_KEY}:`;
const CHAT_INPUT_DRAFT_TTL_MS = 30 * 24 * 60 * 60 * 1000;
const CHAT_INPUT_DRAFT_PRUNE_INTERVAL_MS = 60 * 60 * 1000;
const chatBoxFilesContainer = document.getElementById('chatBoxFiles');
const chatBoxFileInput = document.getElementById('chatBoxFileInput');
const chatBoxMeetingFileInput = document.getElementById('chatBoxMeetingFileInput');
const chatBoxFileButtons = Array.from(fileButtons);
const CHAT_BOX_DROPDOWN_GROUP = 'chat-box-composer-dropdowns';
const chatBoxAttachmentPanelNavigator = window.createDropdownPanelNavigator?.({
  dropdown: chatBoxFilesDropdownMenu,
  minHeight: 160,
  getFocusTarget: (panelName, panel) => {
    if (panelName === 'files') return chatBoxFilesQuickpickSearch;
    if (panelName === 'chats') return chatBoxChatReferencesQuickpickSearch;
    return panel.querySelector('button');
  },
  onNavigate: ({ panelName }) => {
    if (panelName === 'files') {
      void ensureQuickpickFilesLoaded();
    } else if (panelName === 'chats') {
      void loadChatReferenceQuickpick({ query: chatBoxChatReferencesQuickpickSearch?.value || '' });
    }
  },
});
const chatBoxThinkingDropdownController = window.createDropdownController?.({
  id: 'chat-box-thinking-dropdown',
  group: CHAT_BOX_DROPDOWN_GROUP,
  trigger: chatBoxThinkingButton,
  dropdown: chatBoxThinkingDropdownMenu,
  root: chatBoxThinkingContainer,
  bindTrigger: false,
  escapePriority: 90,
});
const chatBoxFilesDropdownController = window.createDropdownController?.({
  id: 'chat-box-files-dropdown',
  group: CHAT_BOX_DROPDOWN_GROUP,
  trigger: chatBoxFileButtons,
  dropdown: chatBoxFilesDropdownMenu,
  root: chatBoxFilesDropdownContainer,
  expandedElements: chatBoxFileButtons,
  bindTrigger: false,
  escapePriority: 90,
  onBeforeOpen: () => {
    chatBoxAttachmentPanelNavigator?.reset({ focus: false });
  },
  onOpen: () => {
    void loadChatBoxConnectionsAvailability();
  },
  onClose: () => {
    cleanupChatBoxFilesDropdown();
  },
});
const chatBoxAttachmentElements = new Map();
let chatBoxReferencePartCount = 0;
const chatDropOverlay = document.getElementById('chatDropOverlay');
const CHAT_DROP_OVERLAY_OWNER_ATTR = 'data-drop-overlay-owner';
const CHAT_DROP_OVERLAY_OWNER_CHAT = 'chat';
const chatDropState = { counter: 0, active: false };
let pendingScrollButtonOffsetUpdate = null;
let chatBoxFilesResizeObserver = null;
let activeChatInputDraftStorageKey = '';
let lastChatInputDraftPruneAt = 0;
let isApplyingChatComposerStateSnapshot = false;

if (chatDropOverlay) {
  // Keep SVG source centralized with the application's other reusable icons.
  const dropIcon = document.getElementById('dropOverlayIcon');
  if (dropIcon && typeof Icons !== 'undefined' && Icons.upload) {
    dropIcon.innerHTML = Icons.upload;
  }
}

function scheduleScrollButtonOffsetUpdate() {
  if (pendingScrollButtonOffsetUpdate) {
    cancelAnimationFrame(pendingScrollButtonOffsetUpdate);
  }
  pendingScrollButtonOffsetUpdate = requestAnimationFrame(() => {
    pendingScrollButtonOffsetUpdate = null;
    const chatContainer = document.getElementById('chatContainer');
    if (!chatContainer) return;

    let extraOffset = 0;
    if (chatBoxFilesContainer && chatBoxFilesContainer.classList.contains('active')) {
      const rect = chatBoxFilesContainer.getBoundingClientRect();
      const margin = (() => {
        try {
          const styles = window.getComputedStyle(chatBoxFilesContainer);
          const marginBottom = parseFloat(styles.marginBottom || styles.marginBlockEnd || '0');
          const paddingBottom = parseFloat(styles.paddingBottom || '0');
          return [marginBottom, paddingBottom]
            .filter((value) => Number.isFinite(value))
            .reduce((sum, value) => sum + value, 0);
        } catch (_) {
          return 0;
        }
      })();
      extraOffset = rect.height || chatBoxFilesContainer.offsetHeight || 0;
      if (!Number.isFinite(extraOffset)) {
        extraOffset = 0;
      }
      extraOffset = Math.max(0, Math.round(extraOffset + margin));
    }

    chatContainer.style.setProperty('--scroll-btn-extra', `${extraOffset}px`);
    if (typeof window.updateScrollButtonVisibility === 'function') {
      window.updateScrollButtonVisibility();
    }
  });
}

function initScrollButtonResizeObservers() {
  if (typeof ResizeObserver === 'undefined') {
    return;
  }
  if (chatBoxFilesContainer && !chatBoxFilesResizeObserver) {
    chatBoxFilesResizeObserver = new ResizeObserver(() => scheduleScrollButtonOffsetUpdate());
    chatBoxFilesResizeObserver.observe(chatBoxFilesContainer);
  }
}

initScrollButtonResizeObservers();

const chatBoxChooseUploadedFilesButton = document.getElementById('chatBoxChooseUploadedFilesButton');
const chatBoxMeetingOverlay = document.getElementById('chatBoxMeetingOverlay');
const chatBoxMeetingModal = document.getElementById('chatBoxMeetingModal');
const chatBoxMeetingCloseButton = document.getElementById('chatBoxMeetingCloseButton');
const chatBoxMeetingCancelButton = document.getElementById('chatBoxMeetingCancelButton');
const chatBoxMeetingSubmitButton = document.getElementById('chatBoxMeetingSubmitButton');
const chatBoxMeetingSourceTabs = document.getElementById('chatBoxMeetingSourceTabs');
const chatBoxMeetingUploadOption = document.getElementById('chatBoxMeetingUploadOption');
const chatBoxMeetingRecordOption = document.getElementById('chatBoxMeetingRecordOption');
const chatBoxMeetingScreenOption = document.getElementById('chatBoxMeetingScreenOption');
const chatBoxMeetingUploadPanel = document.getElementById('chatBoxMeetingUploadPanel');
const chatBoxMeetingCapturePanel = document.getElementById('chatBoxMeetingCapturePanel');
const chatBoxMeetingResultPanel = document.getElementById('chatBoxMeetingResultPanel');
const chatBoxMeetingRecorder = chatBoxMeetingCapturePanel?.querySelector('.chat-meeting-recorder') || null;
const chatBoxMeetingDropzone = document.getElementById('chatBoxMeetingDropzone');
const chatBoxMeetingSelection = document.getElementById('chatBoxMeetingSelection');
const chatBoxMeetingSelectionName = document.getElementById('chatBoxMeetingSelectionName');
const chatBoxMeetingSelectionDetails = document.getElementById('chatBoxMeetingSelectionDetails');
const chatBoxMeetingClearSelectionButton = document.getElementById('chatBoxMeetingClearSelectionButton');
const chatBoxMeetingProgress = document.getElementById('chatBoxMeetingProgress');
const chatBoxMeetingProgressFill = document.getElementById('chatBoxMeetingProgressFill');
const chatBoxMeetingProgressLabel = document.getElementById('chatBoxMeetingProgressLabel');
const chatBoxMeetingProgressDetail = document.getElementById('chatBoxMeetingProgressDetail');
const chatBoxMeetingNote = document.getElementById('chatBoxMeetingNote');
const chatBoxMeetingCaptureModeLabel = document.getElementById('chatBoxMeetingCaptureModeLabel');
const chatBoxMeetingCaptureStatus = document.getElementById('chatBoxMeetingCaptureStatus');
const chatBoxMeetingCaptureDetails = document.getElementById('chatBoxMeetingCaptureDetails');
const chatBoxMeetingCaptureTimer = document.getElementById('chatBoxMeetingCaptureTimer');
const chatBoxMeetingCaptureToggleButton = document.getElementById('chatBoxMeetingCaptureToggleButton');
const chatBoxMeetingCaptureDiscardButton = document.getElementById('chatBoxMeetingCaptureDiscardButton');
const chatBoxMeetingCaptureHint = document.getElementById('chatBoxMeetingCaptureHint');
const chatBoxMeetingGovernance = document.getElementById('chatBoxMeetingGovernance');
const chatBoxMeetingConsentCheckbox = document.getElementById('chatBoxMeetingConsentCheckbox');
const chatBoxMeetingLegalBasis = document.getElementById('chatBoxMeetingLegalBasis');
const chatBoxMeetingLegalBasisDetails = document.getElementById('chatBoxMeetingLegalBasisDetails');
const chatBoxMeetingRetentionDays = document.getElementById('chatBoxMeetingRetentionDays');
const chatBoxChooseChatReferencesButton = document.getElementById('chatBoxChooseChatReferencesButton');
const chatBoxChooseChatReferencesMenuItem = document.getElementById('chatBoxChooseChatReferencesMenuItem');
const chatBoxChooseUploadedFilesMenuItem = document.getElementById('chatBoxChooseUploadedFilesMenuItem');
const chatBoxConnectionsMenuItem = document.getElementById('chatBoxConnectionsMenuItem');
const chatBoxOpenConnectionsButton = document.getElementById('chatBoxOpenConnectionsButton');
const chatBoxChatReferencesQuickpickSearch = document.getElementById('chatBoxChatReferencesQuickpickSearch');
const chatBoxChatReferencesQuickpickScroll = document.getElementById('chatBoxChatReferencesQuickpickScroll');
const chatBoxChatReferencesQuickpickList = document.getElementById('chatBoxChatReferencesQuickpickList');
const chatBoxChatReferencesQuickpickEmpty = document.getElementById('chatBoxChatReferencesQuickpickEmpty');
const chatBoxFilesQuickpickSearch = document.getElementById('chatBoxFilesQuickpickSearch');
const chatBoxFilesQuickpickScroll = document.getElementById('chatBoxFilesQuickpickScroll');
const chatBoxFilesQuickpickList = document.getElementById('chatBoxFilesQuickpickList');
const chatBoxFilesQuickpickEmpty = document.getElementById('chatBoxFilesQuickpickEmpty');
const cachedUploadedFiles = {
  list: [],
  lastFetched: 0,
  queryKey: '',
  total: 0,
  hasMore: false,
  loading: false,
  requestId: 0,
  error: '',
};
const chatBoxUploadedFilesById = new Map();
let chatBoxSelectedUploadedFileIds = new Set();
let chatBoxDisplayedUploadedFiles = [];
const chatSendTooltipContainer = document.querySelector('.chat-send-tooltip');
const chatSendTooltipEl = document.getElementById('chatBoxSendTooltip');
const chatSendSpinnerMarkup = '<span class="chat-send-spinner" aria-hidden="true"></span>';
const chatSendStoppingSpinnerMarkup = '<span class="chat-send-spinner chat-send-spinner--canceling" aria-hidden="true"></span>';
const chatSendQueueIconMarkup = Icons.addToQueue;
let chatSendTooltipSetupAttempts = 0;
const CHAT_UPLOAD_TOOLTIP_KEY = 'chat_uploading_files_tooltip';
const CHAT_UPLOAD_WARNING_KEY = 'chat_uploading_files_warning';
const CHAT_QUEUE_MESSAGE_TOOLTIP_KEY = 'chat_queue_message_tooltip';
const CHAT_QUEUE_TOOLTIP_KEY = 'chat_queueing_messages_tooltip';
const CHAT_STOP_TOOLTIP_KEY = 'chat_stop_generation_tooltip';
const CHAT_STOPPING_TOOLTIP_KEY = 'chat_stopping_generation_tooltip';
const CHAT_SEND_TOOLTIP_KEY = 'chat_send_button_tooltip';
const meetingTranscriptState = {
  selectedFile: null,
  isSubmitting: false,
  lastFocusedElement: null,
  uploadProgress: 0,
  source: 'upload',
  isRecording: false,
  mediaRecorder: null,
  stream: null,
  captureChunks: [],
  captureMimeType: '',
  captureSource: '',
  captureStartedAt: 0,
  captureTimerId: null,
  captureSessionId: 0,
  discardCaptureOnStop: false,
};

function getSelectedUploadedFileIds() {
  return Array.from(chatBoxSelectedUploadedFileIds);
}

/** Keep every visible file-picker row synchronized with shared selection state. */
function setUploadedFileSelectionUi(fileId, selected) {
  document.querySelectorAll('.chatbox-files-quickpick__item[data-file-id]').forEach((element) => {
    if (element.dataset.fileId !== fileId) return;
    element.classList.toggle('is-selected', selected);
    if (element.getAttribute('role') === 'option') {
      element.setAttribute('aria-selected', selected ? 'true' : 'false');
    }
    const checkbox = element.querySelector('input[type="checkbox"]');
    if (checkbox) {
      checkbox.checked = selected;
    }
  });
}

function attachImportedFilesToComposer(importedFiles, target = null) {
  const imported = Array.isArray(importedFiles) ? importedFiles : [];
  if (!imported.length) {
    return;
  }
  if (target && typeof target.addUploadedFiles === 'function') {
    target.addUploadedFiles(imported);
    return;
  }
  const selectedIds = getSelectedUploadedFileIds();
  const nextIds = [...new Set([...selectedIds, ...imported.map((file) => String(file?.file_id ?? file?.id ?? ''))])];
  setSelectedUploadedFileIds(nextIds, { notify: true });
}

function applyUploadedFileSelectionState(rawId, selected, file, { notify = true } = {}) {
  const fileId = String(rawId || '');
  if (!fileId) return;

  if (selected) {
    chatBoxSelectedUploadedFileIds.add(fileId);
  } else {
    chatBoxSelectedUploadedFileIds.delete(fileId);
  }

  if (file && typeof file === 'object') {
    chatBoxUploadedFilesById.set(fileId, file);
  }
  setUploadedFileSelectionUi(fileId, selected);

  if (notify && typeof window.notifyChatUploadedFileToggled === 'function') {
    const metadata = file || chatBoxUploadedFilesById.get(fileId) || null;
    if (metadata) {
      window.notifyChatUploadedFileToggled(metadata, selected);
    } else {
      window.notifyChatUploadedFileToggled({ file_id: fileId }, selected);
    }
  }
}

function clearSelectedUploadedFiles({ notify = true } = {}) {
  if (!chatBoxSelectedUploadedFileIds.size) {
    return;
  }
  const ids = Array.from(chatBoxSelectedUploadedFileIds);
  ids.forEach((id) => {
    applyUploadedFileSelectionState(id, false, null, { notify });
  });
  persistCurrentChatInputDraft();
}

function setSelectedUploadedFileIds(ids, { notify = false } = {}) {
  const nextIds = new Set(Array.isArray(ids) ? ids.map((id) => String(id)) : []);
  const currentIds = Array.from(chatBoxSelectedUploadedFileIds);

  currentIds.forEach((id) => {
    if (!nextIds.has(id)) {
      applyUploadedFileSelectionState(id, false, null, { notify });
    }
  });

  nextIds.forEach((id) => {
    if (!chatBoxSelectedUploadedFileIds.has(id)) {
      const meta = chatBoxUploadedFilesById.get(id) || (cachedUploadedFiles.list || []).find((file) => String(file?.file_id ?? file?.id ?? '') === id) || null;
      applyUploadedFileSelectionState(id, true, meta, { notify });
    }
  });
  persistCurrentChatInputDraft();
}

function clearChatUploadedFilesSelection(options) {
  clearSelectedUploadedFiles(options);
}

function indexChatUploadedFiles(files) {
  chatBoxUploadedFilesById.clear();
  if (!Array.isArray(files)) {
    return;
  }
  files.forEach((file) => {
    const fileId = String(file?.file_id ?? file?.id ?? '').trim();
    if (fileId) {
      chatBoxUploadedFilesById.set(fileId, file);
    }
  });
}

function canShowConnectionsPanel() {
  return Boolean(chatBoxConnectionsAvailabilityState.google_drive);
}

function closeChatBoxConnectionsPanel() {
  if (chatBoxAttachmentPanelNavigator?.activePanel === 'connections') {
    chatBoxAttachmentPanelNavigator.reset({ focus: false });
  }
}

function applyChatBoxConnectionsVisibility() {
  const canShowGoogleDrive = Boolean(chatBoxConnectionsAvailabilityState.google_drive);

  if (chatBoxAddGoogleDriveMenuItem) {
    chatBoxAddGoogleDriveMenuItem.style.display = canShowGoogleDrive ? '' : 'none';
  }

  if (chatBoxConnectionsMenuItem) {
    const showConnections = canShowConnectionsPanel();
    chatBoxConnectionsMenuItem.style.display = showConnections ? '' : 'none';
    if (!showConnections) {
      closeChatBoxConnectionsPanel();
    }
  }
  document.querySelectorAll('.js-chat-files-menu').forEach(updateChatFilesMenuElementVisibility);
  scheduleChatBoxAttachmentPanelHeightSync();
}

function normalizeConnectionsProvider(provider) {
  const normalized = String(provider || '').trim().toLowerCase();
  if (!normalized) {
    return '';
  }
  if (normalized === 'google-drive') {
    return 'google_drive';
  }
  return normalized;
}

function deriveChatBoxConnectionsAvailabilityFromCatalog(items) {
  const availability = {
    google_drive: false,
  };
  const list = Array.isArray(items) ? items : [];
  list.forEach((item) => {
    const provider = normalizeConnectionsProvider(item?.provider);
    if (!Object.prototype.hasOwnProperty.call(availability, provider)) {
      return;
    }
    const connection = item?.connection;
    if (!connection || connection.enabled === false) {
      return;
    }
    const connected = connection.connected === true
      || String(connection?.state || connection?.status?.state || '').trim().toLowerCase() === 'connected';
    if (connected) {
      availability[provider] = true;
    }
  });
  return availability;
}

async function loadChatBoxConnectionsAvailability({ force = false } = {}) {
  if (chatBoxConnectionsAvailabilityState.loading) {
    return;
  }
  if (chatBoxConnectionsAvailabilityState.loaded && !force) {
    return;
  }
  chatBoxConnectionsAvailabilityState.loading = true;
  try {
    const response = await window.authedFetch('/api/v1/connections/catalog', {
      method: 'GET',
      cache: 'no-store',
    });
    if (!response.ok) {
      return;
    }
    const payload = await response.json().catch(() => null);
    const availability = deriveChatBoxConnectionsAvailabilityFromCatalog(payload?.items);
    chatBoxConnectionsAvailabilityState.google_drive = availability.google_drive;
    chatBoxConnectionsAvailabilityState.loaded = true;
    applyChatBoxConnectionsVisibility();
    applyChatBoxFeatureVisibility();
  } catch (_) {
    // Ignore and keep current visibility state
  } finally {
    chatBoxConnectionsAvailabilityState.loading = false;
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('connections:catalogUpdated', (event) => {
    const availability = deriveChatBoxConnectionsAvailabilityFromCatalog(event?.detail?.items);
    chatBoxConnectionsAvailabilityState.google_drive = availability.google_drive;
    chatBoxConnectionsAvailabilityState.loaded = true;
    applyChatBoxConnectionsVisibility();
    applyChatBoxFeatureVisibility();
  });
}

function isQuickpickAvailable() {
  return Boolean(chatBoxFilesQuickpickList && chatBoxChatReferencesQuickpickList);
}

function sortChatBoxFilesForQuickpick(files) {
  if (!Array.isArray(files) || !files.length) {
    return [];
  }
  const sorted = [...files];
  sorted.sort(compareFilesByTimestamp);
  return sorted;
}

function renderQuickpickList(filterText) {
  if (!chatBoxFilesQuickpickList) return;
  const normalized = String(filterText || '').trim().toLowerCase();
  const files = cachedUploadedFiles.list || [];
  const sortedFiles = sortChatBoxFilesForQuickpick(files);
  const matches = sortedFiles.filter((file) => {
    if (!normalized) return true;
    const name = String(file.meta?.original_filename || file.name || '').toLowerCase();
    return name.includes(normalized);
  });
  chatBoxFilesQuickpickList.innerHTML = '';
  if (!matches.length) {
    if (chatBoxFilesQuickpickEmpty) {
      chatBoxFilesQuickpickEmpty.hidden = false;
      chatBoxFilesQuickpickEmpty.textContent = cachedUploadedFiles.error
        || (cachedUploadedFiles.loading
          ? getChatI18nString('files_loading', 'Loading files...')
          : normalized
            ? getChatI18nString('chat_files_quickpick_no_matching_files', 'No matching files')
            : getChatI18nString('chat_files_quickpick_empty_files', 'No files found'));
    }
    return;
  }
  if (chatBoxFilesQuickpickEmpty) chatBoxFilesQuickpickEmpty.hidden = true;

  const frag = document.createDocumentFragment();
  matches.forEach((file) => {
    const fileId = String(file?.file_id ?? file?.id ?? '').trim();
    if (!fileId) return;
    const isSelected = chatBoxSelectedUploadedFileIds.has(fileId);
    const item = document.createElement('label');
    item.className = 'chatbox-files-quickpick__item' + (isSelected ? ' is-selected' : '');
    item.dataset.fileId = fileId;

    const iconWrapper = document.createElement('span');
    iconWrapper.className = 'chatbox-files-quickpick__item-icon';
    const iconImg = document.createElement('img');
    const iconName = typeof window.getFileIconForType === 'function'
      ? window.getFileIconForType(file.file_type || file.mime_type)
      : 'txt.svg';
    iconImg.src = `/assets/file_svgs/${iconName}`;
    iconImg.alt = '';
    iconImg.width = 20;
    iconImg.height = 20;
    iconWrapper.appendChild(iconImg);

    const nameSpan = document.createElement('span');
    nameSpan.className = 'chatbox-files-quickpick__item-name';
    nameSpan.textContent = file.meta?.original_filename || file.name || 'File';

    const checkbox = createChatFilesQuickpickCheckbox(isSelected);
    item.append(iconWrapper, nameSpan, checkbox);

    checkbox.addEventListener('change', (event) => {
      event.stopPropagation();
      const nowSelected = checkbox.checked;
      chatBoxUploadedFilesById.set(fileId, file);
      applyUploadedFileSelectionState(fileId, nowSelected, file);
    });

    frag.appendChild(item);
  });
  chatBoxFilesQuickpickList.appendChild(frag);
}

async function ensureQuickpickFilesLoaded() {
  const now = Date.now();
  const options = buildUploadedFilesQueryOptions({ surface: 'quickpick', offset: 0 });
  const queryKey = getUploadedFilesQueryKey(options);
  if (cachedUploadedFiles.list.length && cachedUploadedFiles.queryKey === queryKey && now - cachedUploadedFiles.lastFetched < 30000) {
    renderQuickpickList(chatBoxFilesQuickpickSearch?.value || '');
    return;
  }
  await loadUploadedFilesForChatBox({ forceRefresh: false, surface: 'quickpick' });
  renderQuickpickList(chatBoxFilesQuickpickSearch?.value || '');
}

if (chatBoxFilesQuickpickSearch) {
  chatBoxFilesQuickpickSearch.addEventListener('input', () => {
    if (quickpickFilesSearchTimer) {
      window.clearTimeout(quickpickFilesSearchTimer);
    }
    quickpickFilesSearchTimer = window.setTimeout(() => {
      chatBoxFilesQuickpickScroll?.scrollTo?.(0, 0);
      void loadUploadedFilesForChatBox({ forceRefresh: true, surface: 'quickpick' });
    }, CHAT_UPLOADED_FILES_SEARCH_DEBOUNCE_MS);
  });
  chatBoxFilesQuickpickSearch.addEventListener('click', (e) => {
    e.stopPropagation();
  });
}

// ── End quick-pick ──────────────────────────────────────────────────────

function readChatInputDraftEntry(context = getCurrentChatInputDraftContext()) {
  if (typeof context === 'string') context = { storageKey: context };
  const storageKey = context?.storageKey || '';
  try {
    const stored = storageKey ? localStorage.getItem(storageKey) : null;
    const entry = parseChatInputDraftStorageEntry(stored);
    if (entry.exists) {
      if (isChatInputDraftExpired(entry)) {
        localStorage.removeItem(storageKey);
        return { value: '', composerState: null };
      }
      return { value: entry.value, composerState: null };
    }
  } catch (_) {
    return { value: '', composerState: null };
  }
  return { value: '', composerState: null };
}

function writeChatInputDraft(value) {
  const context = arguments.length > 1 && arguments[1] && typeof arguments[1] === 'object' && Object.prototype.hasOwnProperty.call(arguments[1], 'storageKey')
    ? arguments[1]
    : getCurrentChatInputDraftContext();
  const storageKey = typeof arguments[1] === 'string'
    ? arguments[1]
    : context?.storageKey || '';

  persistChatInputDraftValue(value, storageKey);
}

function clearChatInputDraft(context = getCurrentChatInputDraftContext()) {
  const storageKey = typeof context === 'string'
    ? context
    : context?.storageKey || '';
  if (!storageKey) {
    return;
  }
  try {
    localStorage.removeItem(storageKey);
  } catch (_) {
    // Ignore localStorage access issues.
  }
}

function persistCurrentChatInputDraft() {
  if (isApplyingChatComposerStateSnapshot || !chatInput) {
    return;
  }
  writeChatInputDraft(chatInput.value || '');
}

function syncChatInputDraftContext(options = {}) {
  const nextContext = getCurrentChatInputDraftContext();
  const nextStorageKey = nextContext?.storageKey || '';
  if (!nextStorageKey || nextStorageKey === activeChatInputDraftStorageKey) {
    return;
  }

  if (chatInput && activeChatInputDraftStorageKey) {
    persistChatInputDraftValue(chatInput.value || '', activeChatInputDraftStorageKey);
  }

  activeChatInputDraftStorageKey = nextStorageKey;
  if (!chatInput) {
    return;
  }

  const draftEntry = readChatInputDraftEntry(nextContext);
  chatInput.value = draftEntry.value;
  toggleInputButtons();
  syncChatTextareaHeightWhenVisible(options.reason || 'restore');
}

function restoreChatInputDraft() {
  removeExpiredChatInputDrafts();
  syncChatInputDraftContext({ reason: 'restore' });
}

function observeChatInputDraftContextChanges() {
  if (!chatContainer || typeof MutationObserver === 'undefined') {
    return;
  }

  const observer = new MutationObserver((mutations) => {
    const shouldSync = mutations.some((mutation) => mutation.type === 'attributes');
    if (shouldSync) {
      syncChatInputDraftContext();
    }
  });

  observer.observe(chatContainer, {
    attributes: true,
    attributeFilter: ['data-chat-id', 'data-project-id', 'data-temp-chat', 'data-pending-chat', 'data-chat-draft-temp-id'],
  });
}

// Preserve the original send icon so we can restore it after stopping
let __originalSendIconHTML = sendButton ? sendButton.innerHTML : '';
let __originalStopIconHTML = chatStopButton ? chatStopButton.innerHTML : '';
const chatSendCancellationPendingByTarget = new Map();

function getCurrentChatSendCancellationTarget() {
  return getSplitScreenGenerationScope().target;
}

function isChatSendCancellationPending(target = getCurrentChatSendCancellationTarget()) {
  return chatSendCancellationPendingByTarget.get(target) === true;
}

function setChatSendCancellationPending(pending, target = getCurrentChatSendCancellationTarget()) {
  const next = Boolean(pending);
  if (target === 'all') {
    chatSendCancellationPendingByTarget.clear();
  } else if (next) {
    chatSendCancellationPendingByTarget.set(target, true);
  } else {
    chatSendCancellationPendingByTarget.delete(target);
  }

  const currentTarget = getCurrentChatSendCancellationTarget();
  const shouldReflectPending = target === 'all'
    ? false
    : currentTarget === target
      ? next
      : isChatSendCancellationPending(currentTarget);
  if (sendButton) {
    sendButton.dataset.cancelPending = shouldReflectPending ? 'true' : 'false';
    sendButton.classList.toggle('is-canceling', shouldReflectPending);
    sendButton.setAttribute('aria-busy', shouldReflectPending ? 'true' : 'false');
  }
  applyChatStopButtonMode();
}

function resolveChatSendCancellationTarget(scope = 'target') {
  const split = window.SplitScreenManager;
  if (!split || split.active !== true) {
    return 'single';
  }
  if (scope === 'target') {
    return typeof split.sendTarget === 'string' ? split.sendTarget : 'both';
  }
  return 'both';
}

function setChatSendTooltipEnabled(enabled) {
  if (!chatSendTooltipContainer) {
    return;
  }
  chatSendTooltipContainer.dataset.tooltipEnabled = enabled ? 'true' : 'false';
  if (!enabled) {
    // Stop and Send share this tooltip. When cancellation changes the button
    // back to Send while it is still hovered/focused, dismiss the old Stop
    // tooltip before the trigger can be hidden and measured at the viewport's
    // top-left corner.
    chatSendTooltipContainer.dispatchEvent(new Event('omlorix:tooltip-dismiss'));
  }
}

function getChatI18nString(key, fallback) {
  if (typeof window.getTranslation === 'function') {
    try {
      const value = window.getTranslation(key, fallback);
      if (typeof value === 'string' && value.trim().length) {
        return value;
      }
    } catch (_) {}
  }
  return fallback;
}

function formatChatI18nString(key, fallback, vars = {}) {
  if (typeof window.formatTranslation === 'function') {
    try {
      return window.formatTranslation(key, fallback, vars);
    } catch (_) {}
  }
  return String(getChatI18nString(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
    const value = vars[token];
    return value === undefined || value === null ? '' : String(value);
  });
}

function setChatComposerAttachmentRemoveLabel(removeTarget, attachmentName) {
  if (!removeTarget) return;
  const name = String(attachmentName || '').trim()
    || getChatI18nString('files_file', 'File');
  const label = formatChatI18nString(
    'chat_sr_remove_attachment',
    'Remove attachment: {name}',
    { name }
  );
  removeTarget.setAttribute('aria-label', label);
  removeTarget.title = label;
}

function setSendButtonRequestingCancel(enabled) {
  if (!sendButton) {
    return;
  }
  try {
    sendButton.classList.toggle('requesting-cancel', Boolean(enabled));
  } catch (_) {}
}

function applyChatStopButtonMode({ isGeneratingNow = isCurrentSendContextGenerating(), hasQueueableInput = hasQueueableChatContent() } = {}) {
  if (!chatStopButton) return;

  const shouldShow = Boolean(isGeneratingNow && hasQueueableInput);
  const isCancelPending = Boolean(isGeneratingNow && isChatSendCancellationPending());
  const stopCopy = getChatI18nString(CHAT_STOP_TOOLTIP_KEY, 'Stop response');
  const stoppingCopy = getChatI18nString(CHAT_STOPPING_TOOLTIP_KEY, 'Stopping response...');
  const copy = isCancelPending ? stoppingCopy : stopCopy;

  chatStopButton.hidden = !shouldShow;
  chatStopButton.dataset.mode = isCancelPending ? 'stopping' : 'stop';
  chatStopButton.disabled = !shouldShow || isCancelPending;
  chatStopButton.setAttribute('aria-disabled', chatStopButton.disabled ? 'true' : 'false');
  chatStopButton.setAttribute('aria-busy', isCancelPending ? 'true' : 'false');
  chatStopButton.title = copy;
  chatStopButton.setAttribute('aria-label', copy);

  if (isCancelPending) {
    chatStopButton.innerHTML = chatSendStoppingSpinnerMarkup;
  } else if (typeof Icons !== 'undefined' && Icons.stop) {
    chatStopButton.innerHTML = Icons.stop;
  } else {
    chatStopButton.innerHTML = __originalStopIconHTML;
  }
}

function formatChatTranscriptionErrorMessage(payload, fallbackMessage, statusCode) {
  if (typeof omlorixFormatTranscriptionErrorMessage === 'function') {
    try {
      return omlorixFormatTranscriptionErrorMessage(payload, fallbackMessage, statusCode);
    } catch (_) {}
  }
  const normalizedStatus = Number(statusCode);
  if (Number.isFinite(normalizedStatus) && normalizedStatus > 0) {
    return `${fallbackMessage} (HTTP ${normalizedStatus})`;
  }
  return fallbackMessage;
}

function showUploadInProgressWarning() {
  const warningCopy = getChatI18nString(CHAT_UPLOAD_WARNING_KEY, 'Please wait for your files to finish uploading.');
  if (typeof notifyWarning === 'function') {
    notifyWarning(warningCopy);
  }
}

function trySetupChatSendTooltip(force = false) {
  if (!chatSendTooltipContainer) return;
  if (!force && chatSendTooltipContainer.dataset.tooltipInitialized === 'true') {
    return;
  }
  if (typeof window.setupTooltip === 'function') {
    window.setupTooltip(chatSendTooltipContainer);
    return;
  }
  if (chatSendTooltipSetupAttempts > 5) {
    return;
  }
  chatSendTooltipSetupAttempts += 1;
  setTimeout(() => trySetupChatSendTooltip(true), 150 * chatSendTooltipSetupAttempts);
}

trySetupChatSendTooltip();

document.addEventListener('i18n:updated', () => {
  // Translated labels can change the measured menu height while it is open.
  scheduleChatBoxAttachmentPanelHeightSync();
  updateChatModelAvailabilityUi();
  if (sendButton?.dataset.uploading === 'true') {
    setChatSendUploadingState(true);
    return;
  }
  applySendButtonMode();
});

function updateChatSendTooltip(text) {
  if (!chatSendTooltipEl || typeof text !== 'string') return;
  chatSendTooltipEl.textContent = text;
}

function isChatUploading() {
  return Boolean(window.chatUploadsInProgress);
}

function getSplitScreenGenerationScope() {
  const split = window.SplitScreenManager;
  if (!split || split.active !== true) {
    return {
      isSplit: false,
      isTargetGenerating: window.isGenerating === true,
      target: 'single',
    };
  }

  const target = typeof split.sendTarget === 'string' ? split.sendTarget : 'both';
  const isTargetGenerating = split.isSendTargetGenerating(target) === true;

  return {
    isSplit: true,
    isTargetGenerating,
    target,
  };
}

function isCurrentSendContextGenerating() {
  return getSplitScreenGenerationScope().isTargetGenerating;
}

function setChatSendUploadingState(uploading) {
  if (!sendButton) return;
  const isUploading = Boolean(uploading);
  const contextGenerating = isCurrentSendContextGenerating();
  window.chatUploadsInProgress = isUploading;
  sendButton.classList.toggle('is-uploading', isUploading);
  if (isUploading) {
    sendButton.disabled = true;
    sendButton.setAttribute('aria-disabled', 'true');
    sendButton.dataset.uploading = 'true';
    const uploadingCopy = getChatI18nString(CHAT_UPLOAD_TOOLTIP_KEY, 'Uploading files…');
    if (!contextGenerating) {
      sendButton.innerHTML = chatSendSpinnerMarkup;
      sendButton.title = uploadingCopy;
      sendButton.setAttribute('aria-label', uploadingCopy);
    }
    updateChatSendTooltip(uploadingCopy);
    setChatSendTooltipEnabled(true);
  } else {
    sendButton.disabled = false;
    sendButton.removeAttribute('aria-disabled');
    sendButton.dataset.uploading = 'false';
    if (!contextGenerating) {
      sendButton.innerHTML = __originalSendIconHTML;
      sendButton.title = '';
      sendButton.setAttribute('aria-label', getChatI18nString('chat_send_message', 'Send message'));
    }
    updateChatSendTooltip(getChatI18nString('chat_send_message', 'Send message'));
    setChatSendTooltipEnabled(false);
  }
  try {
    toggleInputButtons();
  } catch (_) {}
}

window.chatUploadsInProgress = false;

function getChatSettingFallback(key, fallback) {
  try {
    const stored = localStorage.getItem(key);
    if (stored === 'true' || stored === '1') return true;
    if (stored === 'false' || stored === '0') return false;
  } catch (_) {
    // Ignore access issues
  }

  if (typeof window !== 'undefined' && window.chatSetup && Object.prototype.hasOwnProperty.call(window.chatSetup, key)) {
    return Boolean(window.chatSetup[key]);
  }

  return fallback;
}

// Helper so we only read settings once per tick
function resolveChatSetting(key, fallback) {
  if (typeof window !== 'undefined' && typeof window.getChatBooleanSetting === 'function') {
    return window.getChatBooleanSetting(key, fallback);
  }
  return getChatSettingFallback(key, fallback);
}

function applyChatBoxFeatureVisibility() {
  const allowUploads = resolveChatSetting('allow_file_uploads', true);
  const allowChatReferences = true;
  const showCallInput = resolveChatSetting('chat_box_show_call_input', false);
  const thinkingState = renderChatBoxThinkingControl();
  const splitScreenActive = isSplitScreenComposerMode();
  const showAttachmentMenu = allowUploads || allowChatReferences;
  const uploadMenuItems = [
    document.getElementById('chatBoxAddMeetingMenuItem'),
    chatBoxUploadFromComputerButton?.closest('.chatbox-attachment-item, .select-dropdown-item'),
    chatBoxQuickScreenCaptureButton?.closest('.chatbox-attachment-item, .select-dropdown-item'),
    chatBoxConnectionsMenuItem,
    chatBoxAddGoogleDriveMenuItem,
    document.getElementById('chatBoxChooseUploadedFilesMenuItem'),
  ];

  fileButtons.forEach((btn) => {
    const visible = showAttachmentMenu;
    btn.style.display = visible ? 'flex' : 'none';
  });

  if (chatBoxFilesDropdownContainer) {
    const visible = showAttachmentMenu;
    chatBoxFilesDropdownContainer.style.display = visible ? 'flex' : 'none';
    if (!visible) {
      toggleChatBoxFilesDropdown(false);
    }
  }

  uploadMenuItems.forEach((item) => {
    if (!item) return;
    item.style.display = allowUploads ? '' : 'none';
  });

  if (allowUploads) {
    applyChatBoxConnectionsVisibility();
  } else if (chatBoxConnectionsMenuItem) {
    chatBoxConnectionsMenuItem.style.display = 'none';
    closeChatBoxConnectionsPanel();
  }
  if (chatBoxChooseChatReferencesMenuItem) {
    chatBoxChooseChatReferencesMenuItem.style.display = allowChatReferences ? '' : 'none';
  }
  scheduleChatBoxAttachmentPanelHeightSync();

  if (chatBoxThinkingContainer) {
    const visible = Boolean(thinkingState) && !splitScreenActive;
    chatBoxThinkingContainer.hidden = !visible;
    chatBoxThinkingContainer.style.display = visible ? 'flex' : 'none';
    if (!visible) {
      toggleChatBoxThinkingDropdown(false);
    }
  }

  microphoneButtons.forEach((button) => {
    if (!button) return;
    button.dataset.featureEnabled = 'true';
  });
  updateDictationButtonVisibility();

  if (callButton) {
    callButton.dataset.featureEnabled = showCallInput ? 'true' : 'false';
  }

  if (typeof toggleInputButtons === 'function') {
    toggleInputButtons();
  }
  document.querySelectorAll('.js-chat-files-menu').forEach(updateChatFilesMenuElementVisibility);
}

// Initial state check
function toggleInputButtons() {
    if (!callButton || !sendButton) {
        return;
    }

    const uploading = isChatUploading();
    const generating = isCurrentSendContextGenerating();
    const cancelPending = generating && isChatSendCancellationPending();
    const realtimeActive = isRealtimeCallActive();
    const realtimeConnecting = isRealtimeCallConnecting();
    const hasContent = hasSendableChatContent();
    const modelUnavailable = isChatModelSelectionReady() && !hasChatModelForSend();
    const callEnabled = callButton.dataset.featureEnabled === 'true';

    setChatSendCancellationPending(cancelPending);

    if (uploading || generating) {
        callButton.style.display = 'none';
        if (realtimeMuteButton) realtimeMuteButton.style.display = 'none';
        sendButton.style.display = 'flex';
    } else if (realtimeActive || realtimeConnecting) {
        callButton.style.display = callEnabled ? 'flex' : 'none';
        if (realtimeMuteButton) {
          realtimeMuteButton.style.display = (callEnabled && realtimeActive) ? 'flex' : 'none';
        }
        sendButton.style.display = hasContent ? 'flex' : 'none';
    } else {
        callButton.style.display = (!hasContent && callEnabled) ? 'flex' : 'none';
        if (realtimeMuteButton) realtimeMuteButton.style.display = 'none';
        sendButton.style.display = (hasContent || !callEnabled) ? 'flex' : 'none';
    }

    if (!uploading) {
        sendButton.disabled = cancelPending || (!generating && (!hasContent || modelUnavailable));
        sendButton.setAttribute('aria-disabled', sendButton.disabled ? 'true' : 'false');
    }

    updateRealtimeMuteButtonUi();
    updateDictationButtonVisibility();
    applySendButtonMode();
}

// Set initial button state
restoreChatInputDraft();
observeChatInputDraftContextChanges();
toggleInputButtons();
applyChatBoxFeatureVisibility();
void loadChatBoxConnectionsAvailability();
// Expose for other modules (e.g., the sending flow and messageQueue.js)
window.toggleInputButtons = toggleInputButtons;
window.applyChatBoxFeatureVisibility = applyChatBoxFeatureVisibility;
window.writeChatInputDraft = writeChatInputDraft;
window.clearChatInputDraft = clearChatInputDraft;
window.syncChatInputDraftContext = syncChatInputDraftContext;
window.resetChatInputDraftTempContext = resetChatInputDraftTempContext;

document.addEventListener('chatSetupReady', () => {
  try {
    applyChatBoxFeatureVisibility();
  } catch (_) { /* noop */ }
  if (chatInput && chatInput.value) {
    const schedule = typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame
      : (cb) => setTimeout(cb, 0);
    schedule(() => syncChatTextareaHeightWhenVisible('chatSetupReady'));
  }
});

window.addEventListener('storage', (event) => {
  if (!event || !event.key) return;
  if (
    event.key === 'allow_file_uploads'
    || event.key === 'chat_box_show_call_input'
  ) {
    applyChatBoxFeatureVisibility();
  }
});

window.addEventListener('modelSettings:stateChanged', () => {
  renderChatBoxThinkingControl();
  applyChatBoxFeatureVisibility();
});

window.addEventListener('splitScreen:stateChanged', () => {
  applyChatBoxFeatureVisibility();
});

window.addEventListener('modelSelect:changed', () => {
  chatModelSelectionResolved = true;
  updateChatModelAvailabilityUi();
  toggleInputButtons();
  toggleChatBoxThinkingDropdown(false);
  // MCP connectors are authorized against the selected model. Keeping a chip
  // after a model switch is misleading because the backend will correctly
  // discard an ID that the replacement model cannot use. Start the new model
  // with a clean request-scoped allowlist instead.
  clearAllMcpConnectorAttachments();
});

window.addEventListener('pageshow', () => {
  if (!chatInput || chatInput.value === '') return;
  syncChatTextareaHeightWhenVisible('pageshow');
});

function getAttachmentIconName(icon) {
  if (typeof icon === 'string' && icon.trim().length > 0) {
    return icon;
  }
  return 'txt.svg';
}

function getAttachmentExtensionLabel(extension) {
  if (typeof extension === 'string' && extension.trim().length > 0) {
    return extension.trim();
  }
  return 'FILE';
}

// Keep composer previews deliberately narrower than the workspace file
// previewer. A composer chip becomes interactive only when its metadata or
// filename identifies an image format that the shared previewer can render.
const CHAT_COMPOSER_IMAGE_MIME_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/jpg',
  'image/gif',
  'image/bmp',
  'image/svg+xml',
  'image/webp',
]);
const CHAT_COMPOSER_IMAGE_EXTENSION_MIME_TYPES = Object.freeze({
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  bmp: 'image/bmp',
  svg: 'image/svg+xml',
  webp: 'image/webp',
});

/** Resolve a browser-previewable image MIME type from attachment metadata. */
function resolveChatComposerImageMimeType(attachment = {}) {
  const rawType = String(
    attachment.mimeType
    || attachment.fileType
    || attachment.mime_type
    || attachment.file_type
    || ''
  ).split(';', 1)[0].trim().toLowerCase();
  if (CHAT_COMPOSER_IMAGE_MIME_TYPES.has(rawType)) {
    return rawType;
  }

  // A concrete non-image MIME type is authoritative. Filename fallback is
  // reserved for uploads whose provider omitted the type or returned one of
  // the generic binary labels used by object storage services.
  const isGenericType = !rawType || [
    'application/octet-stream',
    'binary/octet-stream',
    'application/x-binary',
    'application/download',
    'application/x-download',
  ].includes(rawType);
  if (!isGenericType) {
    return '';
  }

  const fileName = String(attachment.name || attachment.original_name || '');
  const nameExtension = fileName.includes('.') ? fileName.split('.').pop().toLowerCase() : '';
  const explicitExtension = String(attachment.extension || '').trim().replace(/^\./, '').toLowerCase();
  return CHAT_COMPOSER_IMAGE_EXTENSION_MIME_TYPES[nameExtension]
    || CHAT_COMPOSER_IMAGE_EXTENSION_MIME_TYPES[explicitExtension]
    || '';
}

/** Return true only for image formats supported by the shared image viewer. */
function isChatComposerImageAttachment(attachment = {}) {
  return Boolean(resolveChatComposerImageMimeType(attachment));
}

/** Build the normalized file descriptor expected by FilesPreview.open(). */
function createChatComposerImagePreviewFile(attachment = {}, fileId = '') {
  const normalizedId = String(fileId || attachment.id || attachment.file_id || '').trim();
  const fileName = String(attachment.name || attachment.original_name || normalizedId);
  const rawSize = Number(attachment.fileSize ?? attachment.file_size ?? 0);
  const fileSize = Number.isFinite(rawSize) && rawSize >= 0 ? rawSize : 0;
  const fileType = resolveChatComposerImageMimeType(attachment);
  return {
    id: normalizedId,
    file_id: normalizedId,
    file_name: fileName,
    file_type: fileType,
    mime_type: fileType,
    file_size: fileSize,
    meta: {
      original_filename: fileName,
      file_type: fileType,
      mime_type: fileType,
      file_size: fileSize,
    },
  };
}

/** Open an uploaded composer image in the existing accessible image lightbox. */
async function openChatComposerImagePreview(element) {
  const previewFile = element?.__imagePreviewFile;
  const preview = typeof window !== 'undefined' ? window.FilesPreview : null;
  if (!previewFile?.file_id || !preview || typeof preview.open !== 'function') {
    return;
  }

  if (preview.isOpen && String(preview.activeFileId || '') === previewFile.file_id) {
    preview.close();
    return;
  }

  try {
    await preview.open(previewFile);
  } catch (error) {
    console.error('Failed to open composer image preview', error);
    window.notifyError?.(getChatI18nString('files_preview_open_error', 'Failed to open file preview.'));
  }
}

/** Synchronize preview semantics whenever an upload changes ID or metadata. */
function syncChatComposerImagePreview(element) {
  const previewTarget = element?.__previewTarget;
  if (!element || !previewTarget) return;

  const attachment = element.__attachmentDescriptor || {};
  const fileId = String(element.dataset.fileId || attachment.id || '').trim();
  const isReady = Boolean(fileId)
    && !fileId.startsWith('temp-upload-')
    && !element.classList.contains('is-uploading');
  const isPreviewable = isReady && isChatComposerImageAttachment(attachment);

  element.classList.toggle('inline-files-element-previewable', isPreviewable);
  previewTarget.classList.toggle('inline-files-element-preview-target', isPreviewable);
  element.__imagePreviewFile = isPreviewable
    ? createChatComposerImagePreviewFile(attachment, fileId)
    : null;

  if (!isPreviewable) {
    previewTarget.removeAttribute('role');
    previewTarget.removeAttribute('tabindex');
    previewTarget.removeAttribute('aria-label');
    previewTarget.removeAttribute('title');
    return;
  }

  const fileName = element.__imagePreviewFile.meta.original_filename;
  const label = formatChatI18nString(
    'chat_sr_open_attachment_preview',
    'Open attachment preview: {name}',
    { name: fileName }
  );
  previewTarget.setAttribute('role', 'button');
  previewTarget.setAttribute('tabindex', '0');
  previewTarget.setAttribute('aria-label', label);
  previewTarget.title = label;
}

function clampProgress(value) {
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value));
}

function ensureUploadUI(element) {
  if (!element) return null;
  if (element.__uploadOverlay) {
    return element.__uploadOverlay;
  }

  const overlay = document.createElement('div');
  overlay.className = 'inline-files-upload';

  const bar = document.createElement('div');
  bar.className = 'inline-files-upload-bar';
  const fill = document.createElement('div');
  fill.className = 'inline-files-upload-fill';
  bar.appendChild(fill);

  overlay.appendChild(bar);
  element.appendChild(overlay);

  element.__uploadOverlay = overlay;
  element.__uploadFill = fill;

  return overlay;
}

function teardownUploadUI(element) {
  if (!element || !element.__uploadOverlay) return;
  try {
    element.__uploadOverlay.remove();
  } catch (_) { /* noop */ }
  element.__uploadOverlay = null;
  element.__uploadFill = null;
  delete element.dataset.uploading;
  element.classList.remove('is-uploading');
}

function setAttachmentUploadingState(element, isUploading, { progress } = {}) {
  if (!element) return;
  if (!isUploading) {
    teardownUploadUI(element);
    syncChatComposerImagePreview(element);
    return;
  }

  element.dataset.uploading = 'true';
  element.classList.add('is-uploading');
  const overlay = ensureUploadUI(element);
  if (!overlay) return;

  if (typeof progress === 'number' && element.__uploadFill) {
    element.__uploadFill.style.width = `${clampProgress(progress)}%`;
  }
  syncChatComposerImagePreview(element);
}

function applyAttachmentContent(element, updates = {}) {
  if (!element) return;
  const { name, extension, icon } = updates;
  if (name && element.__nameEl) {
    element.__nameEl.textContent = name;
  }
  if (extension && element.__extensionEl) {
    element.__extensionEl.textContent = getAttachmentExtensionLabel(extension);
  }
  if (icon && element.__iconImg) {
    element.__iconImg.src = `/assets/file_svgs/${getAttachmentIconName(icon)}`;
  }
  element.__attachmentDescriptor = {
    ...(element.__attachmentDescriptor || {}),
    ...updates,
    id: element.dataset.fileId || updates.id || '',
  };
  setChatComposerAttachmentRemoveLabel(
    element.__deleteTarget,
    element.__attachmentDescriptor.name || element.__nameEl?.textContent
  );
  syncChatComposerImagePreview(element);
}

function toggleChatFilesContainer(active) {
  if (!chatBoxFilesContainer) return;
  if (active) {
    chatBoxFilesContainer.classList.add('active');
  } else {
    chatBoxFilesContainer.classList.remove('active');
  }
  scheduleScrollButtonOffsetUpdate();
}

function updateChatFilesContainerVisibility() {
  const hasFiles = chatBoxAttachmentElements.size > 0;
  const hasSkills = selectedSkillIds.size > 0;
  const hasNotes = selectedNoteIds.size > 0;
  const hasPrompts = selectedPromptIds.size > 0;
  const hasMcpConnectors = selectedMcpServerIds.size > 0;
  const hasChatReferences = selectedChatReferenceIds.size > 0;
  const hasReferences = chatBoxReferencePartCount > 0;
  toggleChatFilesContainer(hasFiles || hasSkills || hasNotes || hasPrompts || hasMcpConnectors || hasChatReferences || hasReferences);
  scheduleScrollButtonOffsetUpdate();
}

function setChatDropActive(active) {
  const chatBox = document.getElementById('chatBox');
  if (!chatBox) return;
  if (active) {
    chatDropState.active = true;

  } else {
    chatDropState.active = false;

  }
  if (chatDropOverlay) {
    if (active) {
      chatDropOverlay.setAttribute(CHAT_DROP_OVERLAY_OWNER_ATTR, CHAT_DROP_OVERLAY_OWNER_CHAT);
      chatDropOverlay.classList.add('active');
      chatDropOverlay.setAttribute('aria-hidden', 'false');
    } else {
      const overlayOwner = chatDropOverlay.getAttribute(CHAT_DROP_OVERLAY_OWNER_ATTR);
      if (overlayOwner && overlayOwner !== CHAT_DROP_OVERLAY_OWNER_CHAT) {
        return;
      }
      chatDropOverlay.removeAttribute(CHAT_DROP_OVERLAY_OWNER_ATTR);
      chatDropOverlay.classList.remove('active');
      chatDropOverlay.setAttribute('aria-hidden', 'true');
    }
  }
}

function resetChatDropState() {
  chatDropState.counter = 0;
  setChatDropActive(false);
}

/** Return whether the current account may add files to a chat. */
function canUploadChatFiles() {
  return resolveChatSetting('allow_file_uploads', true);
}

function isSkillImportDropActive() {
  const skillImportOverlay = document.getElementById('skillImportOverlay');
  return Boolean(
    skillImportOverlay
    && !skillImportOverlay.hasAttribute('hidden')
    && skillImportOverlay.getAttribute('aria-hidden') !== 'true',
  );
}

function isFilesViewActive() {
  const workspaceManager = typeof window !== 'undefined' ? window.WorkspaceManager : null;
  if (workspaceManager && typeof workspaceManager.getActiveTab === 'function') {
    const activeTab = workspaceManager.getActiveTab();
    const workspaceVisible = typeof workspaceManager.isVisible === 'function'
      ? workspaceManager.isVisible()
      : true;
    if (workspaceVisible && activeTab === 'files') {
      return true;
    }
  }

  const filesContainer = document.getElementById('filesContainer');
  if (!filesContainer) return false;
  try {
    const style = window.getComputedStyle(filesContainer);
    if (!style || style.display === 'none' || style.visibility === 'hidden') {
      return false;
    }
    return filesContainer.getClientRects().length > 0;
  } catch (_) {
    return false;
  }
}

function isFileDragEvent(event) {
  if (!event || !event.dataTransfer) return false;
  const dataTransfer = event.dataTransfer;
  const files = dataTransfer.files;
  if (files && files.length > 0) {
    return true;
  }

  const items = dataTransfer.items;
  if (items && items.length > 0) {
    for (const item of Array.from(items)) {
      if (item && item.kind === 'file') {
        return true;
      }
    }
  }

  const types = dataTransfer.types;
  if (!types) {
    return false;
  }
  const normalizedTypes = Array.from(types).map((type) => String(type || '').toLowerCase());
  return normalizedTypes.includes('files')
    || normalizedTypes.includes('application/x-moz-file')
    || normalizedTypes.includes('public.file-url');
}

function isInternalAppDragEvent(event) {
  const types = Array.from(event?.dataTransfer?.types || []).map((type) => String(type || '').toLowerCase());
  return types.includes('application/x-file-id')
    || types.includes(String(CHAT_REFERENCE_DRAG_MIME || '').toLowerCase());
}

function isLikelyExternalFileDragEvent(event) {
  if (isInternalAppDragEvent(event)) {
    return false;
  }
  const types = Array.from(event?.dataTransfer?.types || []).map((type) => String(type || '').toLowerCase());
  return types.includes('application/x-moz-file')
    || types.includes('public.file-url')
    || types.includes('files');
}

function extractDroppedFiles(dataTransfer) {
  if (!dataTransfer) {
    return [];
  }
  if (dataTransfer.files && dataTransfer.files.length > 0) {
    return Array.from(dataTransfer.files);
  }
  const collected = [];
  const items = dataTransfer.items;
  if (!items || !items.length) {
    return collected;
  }
  for (const item of Array.from(items)) {
    if (!item || item.kind !== 'file') {
      continue;
    }
    const file = typeof item.getAsFile === 'function' ? item.getAsFile() : null;
    if (file) {
      collected.push(file);
    }
  }
  return collected;
}

function handleChatDragEnter(event) {
  // The skill-import modal owns external file drags while it is open, even
  // before the pointer reaches its inner dropzone.
  if (isSkillImportDropActive()) {
    resetChatDropState();
    return;
  }
  if (isInternalAppDragEvent(event)) return;
  if (!isFileDragEvent(event) && !isLikelyExternalFileDragEvent(event)) return;
  if (isFilesViewActive()) return;
  event.preventDefault();
  if (typeof event.stopPropagation === 'function') {
    event.stopPropagation();
  }
  if (typeof event.stopImmediatePropagation === 'function') {
    event.stopImmediatePropagation();
  }
  if (!canUploadChatFiles()) {
    resetChatDropState();
    return;
  }
  chatDropState.counter += 1;
  setChatDropActive(true);
}

function handleChatDragOver(event) {
  if (isSkillImportDropActive()) {
    resetChatDropState();
    return;
  }
  if (isInternalAppDragEvent(event)) return;
  if (!isFileDragEvent(event) && !isLikelyExternalFileDragEvent(event)) return;
  if (isFilesViewActive()) return;
  event.preventDefault();
  if (typeof event.stopPropagation === 'function') {
    event.stopPropagation();
  }
  if (typeof event.stopImmediatePropagation === 'function') {
    event.stopImmediatePropagation();
  }
  const canUpload = canUploadChatFiles();
  try {
    event.dataTransfer.dropEffect = canUpload ? 'copy' : 'none';
  } catch (_) {}
  if (canUpload && !chatDropState.active) {
    setChatDropActive(true);
  } else if (!canUpload) {
    resetChatDropState();
  }
}

function handleChatDragLeave(event) {
  if (isSkillImportDropActive()) {
    resetChatDropState();
    return;
  }
  if (isInternalAppDragEvent(event)) return;
  if (!chatDropState.active && !isFileDragEvent(event) && !isLikelyExternalFileDragEvent(event)) return;
  if (isFilesViewActive()) return;
  if (typeof event.stopPropagation === 'function') {
    event.stopPropagation();
  }
  if (typeof event.stopImmediatePropagation === 'function') {
    event.stopImmediatePropagation();
  }
  chatDropState.counter = Math.max(0, chatDropState.counter - 1);
  if (chatDropState.counter === 0) {
    resetChatDropState();
  }
}

function handleChatDrop(event) {
  if (isSkillImportDropActive()) {
    resetChatDropState();
    return;
  }
  if (isInternalAppDragEvent(event)) return;
  if (!isFileDragEvent(event) && !isLikelyExternalFileDragEvent(event)) return;
  const dataTransfer = event.dataTransfer;
  const files = extractDroppedFiles(dataTransfer);
  if (files.length && event.cancelable) {
    event.preventDefault();
  }
  if (isFilesViewActive()) {
    resetChatDropState();
    if (typeof event.stopPropagation === 'function') {
      event.stopPropagation();
    }
    if (typeof event.stopImmediatePropagation === 'function') {
      event.stopImmediatePropagation();
    }
    if (files.length && window.FilesManager && typeof window.FilesManager.uploadFiles === 'function') {
      try {
        const result = window.FilesManager.uploadFiles(files);
        if (result && typeof result.catch === 'function') {
          result.catch((error) => { console.error('FilesManager.uploadFiles drop error', error); });
        }
      } catch (error) {
        console.error('FilesManager.uploadFiles drop error', error);
      }
    }
    return;
  }
  if (typeof event.stopPropagation === 'function') {
    event.stopPropagation();
  }
  if (typeof event.stopImmediatePropagation === 'function') {
    event.stopImmediatePropagation();
  }
  resetChatDropState();
  if (!canUploadChatFiles()) {
    if (files.length && typeof notifyError === 'function') {
      notifyError(getChatI18nString('files_upload_disabled', 'File uploads are disabled for your account.'));
    }
    return;
  }
  if (files.length && typeof window.handleChatFileSelection === 'function') {
    try {
      const result = window.handleChatFileSelection(files);
      if (result && typeof result.catch === 'function') {
        result.catch((error) => { console.error('handleChatFileSelection drop error', error); });
      }
    } catch (error) {
      console.error('handleChatFileSelection drop error', error);
    }
  }
}

function bindChatDropZone(element) {
  if (!element) return;
  element.addEventListener('dragenter', handleChatDragEnter);
  element.addEventListener('dragover', handleChatDragOver);
  element.addEventListener('dragleave', handleChatDragLeave);
  element.addEventListener('drop', handleChatDrop);
}

function isChatReferenceDragEvent(event) {
  if (!event?.dataTransfer?.types) {
    return getActiveDraggedChatReferencePayloadFromWindow() !== null
      || resolveActiveDraggedChatReferencePayloadFromDom() !== null;
  }
  const types = Array.from(event.dataTransfer.types);
  return types.includes(CHAT_REFERENCE_DRAG_MIME)
    || getActiveDraggedChatReferencePayloadFromWindow() !== null
    || resolveActiveDraggedChatReferencePayloadFromDom() !== null;
}

function normalizeChatReferenceDragPayload(parsed) {
  const chatId = resolveChatReferenceId(parsed);
  if (!chatId) {
    return null;
  }
  return {
    chat_id: chatId,
    title: parsed?.title || getChatI18nString('chat_reference_untitled', 'Untitled chat'),
    last_updated_at: parsed?.last_updated_at || null,
    snippet: parsed?.snippet || '',
    message_count: Number(parsed?.message_count || 0) || 0,
    estimated_chars: Number(parsed?.estimated_chars || 0) || 0,
  };
}

function getActiveDraggedChatReferencePayloadFromWindow() {
  if (typeof window === 'undefined') {
    return null;
  }
  const payload = window[CHAT_REFERENCE_DRAG_WINDOW_STATE_KEY_CHATBOX];
  return normalizeChatReferenceDragPayload(payload);
}

function resolveActiveDraggedChatReferencePayloadFromDom() {
  if (typeof document === 'undefined') {
    return null;
  }
  const draggingRow = document.querySelector('.sidebar-element.dragging[data-chat-id]');
  if (!draggingRow) {
    return null;
  }
  const chatId = String(draggingRow.getAttribute('data-chat-id') || '').trim();
  if (!chatId) {
    return null;
  }
  const titleNode = draggingRow.querySelector('a.sidebar-element-button > p');
  const untitledChat = getChatI18nString('chat_reference_untitled', 'Untitled chat');
  const title = String(draggingRow.getAttribute('data-chat-title') || titleNode?.textContent || untitledChat).trim() || untitledChat;
  return normalizeChatReferenceDragPayload({
    chat_id: chatId,
    title,
    snippet: '',
    last_updated_at: null,
    message_count: 0,
    estimated_chars: 0,
  });
}

function extractChatReferenceDragPayload(event) {
  if (!isChatReferenceDragEvent(event)) {
    return null;
  }
  try {
    const payload = event.dataTransfer?.getData(CHAT_REFERENCE_DRAG_MIME);
    if (payload) {
      const parsed = JSON.parse(payload);
      const normalized = normalizeChatReferenceDragPayload(parsed);
      if (normalized) {
        return normalized;
      }
    }
  } catch (error) {
    console.error('Failed to parse dragged chat reference payload', error);
  }
  const fromWindow = getActiveDraggedChatReferencePayloadFromWindow();
  if (fromWindow) {
    return fromWindow;
  }
  const fromDom = resolveActiveDraggedChatReferencePayloadFromDom();
  if (fromDom) {
    return fromDom;
  }
  return null;
}

function handleChatReferenceDragOver(event) {
  if (!isChatReferenceDragEvent(event)) return;
  if (isFilesViewActive()) return;
  event.preventDefault();
  event.stopPropagation();
  try {
    event.dataTransfer.dropEffect = 'copy';
  } catch (_) {}
}

function handleChatReferenceDrop(event) {
  if (!isChatReferenceDragEvent(event)) return;
  if (isFilesViewActive()) return;
  event.preventDefault();
  if (typeof event.stopPropagation === 'function') {
    event.stopPropagation();
  }
  if (typeof event.stopImmediatePropagation === 'function') {
    event.stopImmediatePropagation();
  }
  const payload = extractChatReferenceDragPayload(event);
  if (!payload) {
    return;
  }
  addChatReferenceAttachment(payload, { notifyOnDuplicate: true });
}

function bindChatReferenceDropZone(element) {
  if (!element) return;
  element.addEventListener('dragover', handleChatReferenceDragOver);
  element.addEventListener('drop', handleChatReferenceDrop);
}

function createChatAttachmentElement({
  id,
  name,
  icon,
  extension,
  mimeType = '',
  fileType = '',
  fileSize = 0,
  isUploading = false,
  progress = 0,
}) {
  if (!chatBoxFilesContainer) return null;
  const element = document.createElement('div');
  element.className = 'inline-files-element';
  element.dataset.fileId = id;

  // The main region can gain button semantics for images without nesting the
  // separate remove control inside another interactive element.
  const mainElement = document.createElement('div');
  mainElement.className = 'inline-files-element-main';

  const iconWrapper = document.createElement('div');
  iconWrapper.className = 'inline-files-element-icon';
  const iconImg = document.createElement('img');
  iconImg.src = `/assets/file_svgs/${getAttachmentIconName(icon)}`;
  iconImg.alt = getAttachmentExtensionLabel(extension);
  iconImg.width = 28;
  iconImg.height = 28;
  iconWrapper.appendChild(iconImg);

  const content = document.createElement('div');
  content.className = 'inline-files-element-content';

  const topRow = document.createElement('div');
  topRow.className = 'inline-files-element-content-top';
  const nameEl = document.createElement('p');
  nameEl.textContent = name || 'File';
  topRow.appendChild(nameEl);

  const bottomRow = document.createElement('div');
  bottomRow.className = 'inline-files-element-content-bottom';
  const extensionEl = document.createElement('p');
  extensionEl.textContent = getAttachmentExtensionLabel(extension);
  bottomRow.appendChild(extensionEl);

  content.appendChild(topRow);
  content.appendChild(bottomRow);

  const deleteEl = document.createElement('div');
  deleteEl.className = 'inline-files-element-delete';
  deleteEl.setAttribute('role', 'button');
  deleteEl.setAttribute('tabindex', '0');
  deleteEl.innerHTML = Icons.close;
  const requestRemoval = () => {
    if (typeof window.handleChatAttachmentRemoval !== 'function') return;
    const currentId = element.dataset.fileId || id;
    window.handleChatAttachmentRemoval(currentId);
  };
  deleteEl.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    requestRemoval();
  });
  deleteEl.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      event.stopPropagation();
      requestRemoval();
    }
  });

  mainElement.appendChild(iconWrapper);
  mainElement.appendChild(content);
  element.appendChild(mainElement);
  element.appendChild(deleteEl);

  element.__nameEl = nameEl;
  element.__extensionEl = extensionEl;
  element.__iconImg = iconImg;
  element.__previewTarget = mainElement;
  element.__deleteTarget = deleteEl;
  element.__attachmentDescriptor = {
    id,
    name,
    icon,
    extension,
    mimeType,
    fileType,
    fileSize,
  };
  setChatComposerAttachmentRemoveLabel(deleteEl, nameEl.textContent);

  mainElement.addEventListener('click', (event) => {
    if (!element.__imagePreviewFile) return;
    event.preventDefault();
    void openChatComposerImagePreview(element);
  });
  mainElement.addEventListener('keydown', (event) => {
    if (!element.__imagePreviewFile || (event.key !== 'Enter' && event.key !== ' ')) return;
    event.preventDefault();
    void openChatComposerImagePreview(element);
  });

  if (isUploading) {
    setAttachmentUploadingState(element, true, { progress });
  } else {
    syncChatComposerImagePreview(element);
  }

  return element;
}

const chatBoxAttachmentsUI = {
  setUploading(state) {
    const nextState = Boolean(state);
    chatBoxFileButtons.forEach((btn) => {
      if (!btn) return;
      btn.disabled = nextState;
      btn.setAttribute('aria-disabled', nextState ? 'true' : 'false');
    });
    if (chatBoxFileInput) {
      chatBoxFileInput.disabled = nextState;
    }
    setChatSendUploadingState(nextState);
  },
  addAttachment(attachment = {}) {
    const { id } = attachment;
    if (!id || !chatBoxFilesContainer) return;
    if (chatBoxAttachmentElements.has(id)) {
      this.removeAttachment(id);
    }
    const element = createChatAttachmentElement(attachment);
    if (!element) return;
    chatBoxAttachmentElements.set(id, element);
    chatBoxFilesContainer.appendChild(element);
    updateChatFilesContainerVisibility();
    toggleInputButtons();
    persistCurrentChatInputDraft();
  },
  upsertAttachment(attachment) {
    if (!attachment?.id) return;
    if (chatBoxAttachmentElements.has(attachment.id)) {
      this.updateAttachment(attachment.id, attachment);
      return;
    }
    this.addAttachment(attachment);
  },
  updateAttachment(id, updates = {}) {
    const element = chatBoxAttachmentElements.get(id);
    if (!element) return;
    const hasPreviewMetadataUpdate = [
      'name',
      'extension',
      'icon',
      'mimeType',
      'fileType',
      'fileSize',
    ].some((key) => Object.prototype.hasOwnProperty.call(updates, key));
    if (hasPreviewMetadataUpdate) {
      applyAttachmentContent(element, updates);
    }
    const shouldToggleUpload = Object.prototype.hasOwnProperty.call(updates, 'isUploading')
      || Object.prototype.hasOwnProperty.call(updates, 'progress');

    if (shouldToggleUpload) {
      const nextUploading = updates.isUploading ?? element.classList.contains('is-uploading');
      setAttachmentUploadingState(element, nextUploading, {
        progress: updates.progress,
      });
    }
    persistCurrentChatInputDraft();
  },
  replaceAttachmentId(oldId, attachment) {
    if (!oldId || !attachment?.id) return null;
    const element = chatBoxAttachmentElements.get(oldId);
    if (!element) {
      this.upsertAttachment(attachment);
      return attachment.id;
    }

    // The upload API can reuse an existing file ID when the same bytes were
    // uploaded before. If that file is already attached, discard only the
    // temporary chip instead of leaving two DOM elements with the same ID.
    const existingElement = chatBoxAttachmentElements.get(attachment.id);
    if (existingElement && existingElement !== element) {
      element.remove();
      chatBoxAttachmentElements.delete(oldId);
      this.updateAttachment(attachment.id, attachment);
      updateChatFilesContainerVisibility();
      toggleInputButtons();
      persistCurrentChatInputDraft();
      return attachment.id;
    }
    chatBoxAttachmentElements.delete(oldId);
    element.dataset.fileId = attachment.id;
    applyAttachmentContent(element, attachment);
    chatBoxAttachmentElements.set(attachment.id, element);
    if (Object.prototype.hasOwnProperty.call(attachment, 'isUploading')) {
      setAttachmentUploadingState(element, attachment.isUploading, {
        progress: attachment.progress,
      });
    }
    persistCurrentChatInputDraft();
    return attachment.id;
  },
  removeAttachment(id) {
    const element = chatBoxAttachmentElements.get(id);
    if (element) {
      element.remove();
      chatBoxAttachmentElements.delete(id);
    }
    updateChatFilesContainerVisibility();
    toggleInputButtons();
    persistCurrentChatInputDraft();
  },
  clear() {
    chatBoxAttachmentElements.forEach((element) => {
      element.remove();
    });
    chatBoxAttachmentElements.clear();
    updateChatFilesContainerVisibility();
    toggleInputButtons();
    persistCurrentChatInputDraft();
  }
};

if (typeof window !== 'undefined') {
  window.ChatBoxAttachmentsUI = chatBoxAttachmentsUI;
}

// Global generation state helpers so other modules can control the UI
window.isGenerating = false;
window.pendingCancelGeneration = false;
let pendingInterruptedDraftSend = false;
let interruptedDraftDispatchInFlight = false;
window.startGenerationUI = function startGenerationUI() {
  window.isGenerating = true;
  setChatSendCancellationPending(false, 'all');
  resetDoubleEnterTimer();
  window.chatWakeLock?.acquire?.('generation');
  if (!sendButton || !callButton) return;
  // Force show the send button as the STOP button while generating
  callButton.style.display = 'none';
  sendButton.style.display = 'flex';
  // Ensure it's clickable and on top
  try {
    sendButton.disabled = false;
    sendButton.style.pointerEvents = 'auto';
    sendButton.style.zIndex = '1000';
  } catch (_) {}
  applySendButtonMode();
};

window.endGenerationUI = function endGenerationUI() {
  window.isGenerating = false;
  setChatSendCancellationPending(false, 'all');
  resetDoubleEnterTimer();
  window.chatWakeLock?.release?.('generation');
  if (!sendButton || !callButton) return;
  try {
    sendButton.style.pointerEvents = '';
    sendButton.style.zIndex = '';
  } catch (_) { /* no-op */ }
  // Restore normal toggle behavior based on input
  toggleInputButtons();
};

function resetGenerationUIState({ clearActiveAttr = true } = {}) {
  window.currentGenerationId = null;
  window.pendingCancelGeneration = false;
  window.isGenerating = false;
  setChatSendCancellationPending(false, 'all');
  resetDoubleEnterTimer();
  window.chatWakeLock?.release?.('generation');
  if (typeof window.endGenerationUI === 'function') {
    window.endGenerationUI();
  }
  if (clearActiveAttr) {
    const chatContainerEl = document.getElementById('chatContainer');
    if (chatContainerEl) {
      chatContainerEl.removeAttribute('data-active-generation');
    }
  }
};

function clearPendingInterruptedDraftSend() {
  pendingInterruptedDraftSend = false;
}

function dispatchCurrentDraftMessage() {
  if (isChatUploading()) {
    showUploadInProgressWarning();
    return false;
  }

  if (!hasSendableChatContent()) {
    clearPendingInterruptedDraftSend();
    toggleInputButtons();
    return false;
  }

  if (!isRealtimeCallActive() && !hasChatModelForSend()) {
    clearPendingInterruptedDraftSend();
    updateChatModelAvailabilityUi();
    return showChatModelUnavailableFeedback();
  }

  // A terminal stream event hands the queue off in a microtask. During that
  // brief gap the composer is no longer generating, but an older message can
  // still be waiting at the head of the queue. Never let this newer draft take
  // the direct-send path and overtake it.
  if (getQueuedMessageCount() > 0 && tryQueueCurrentInput({ showOverlay: false })) {
    clearPendingInterruptedDraftSend();
    resetDoubleEnterTimer();
    toggleInputButtons();
    void window.messageQueue?.processNext?.();
    return true;
  }

  resetDoubleEnterTimer();
  const message = String(chatInput?.value || '').trim();
  interruptedDraftDispatchInFlight = true;

  if (!isRealtimeCallActive() && window.SplitScreenManager && window.SplitScreenManager.active) {
    window.SplitScreenManager.send(message);
    interruptedDraftDispatchInFlight = false;
  } else {
    Promise.resolve(sendMessage(message, false, null))
      .catch((error) => {
        console.error('Failed to dispatch chat draft message', error);
      })
      .finally(() => {
        interruptedDraftDispatchInFlight = false;
        // The generation terminal hook can run one microtask before this
        // composer-owned promise settles. If queue processing deferred on that
        // cleanup flag, explicitly hand it another FIFO processing chance.
        if (getQueuedMessageCount() > 0) {
          void window.messageQueue?.processNext?.();
        }
      });
  }

  if (chatInput) {
    chatInput.value = '';
    clearChatInputDraft();
    toggleInputButtons();
    chatInput.dispatchEvent(new Event('input', { bubbles: true }));
  } else {
    toggleInputButtons();
  }

  clearPendingInterruptedDraftSend();
  return true;
}

function flushInterruptedDraftSend() {
  if (!pendingInterruptedDraftSend || isCurrentSendContextGenerating()) {
    return false;
  }
  return dispatchCurrentDraftMessage();
}

if (typeof window !== 'undefined') {
  window.flushInterruptedDraftSend = flushInterruptedDraftSend;
  window.clearPendingInterruptedDraftSend = clearPendingInterruptedDraftSend;
  window.isInterruptedDraftDispatchPending = () => pendingInterruptedDraftSend || interruptedDraftDispatchInFlight;
}
