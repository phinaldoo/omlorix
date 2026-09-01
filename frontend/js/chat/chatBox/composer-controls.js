// Auto-resize the chat textarea
const textarea = document.querySelector('#chatBoxInput');
let prevTextareaHeight = null;

const adjustHeight = () => {
  if (!textarea) {
    return;
  }

  // Freeze chat area baseline height on first run so we can adjust it precisely
  if (prevTextareaHeight === null) {
    const cs = getComputedStyle(textarea);
    const lineH = parseFloat(cs.lineHeight) || 18; // fallback
    const minH = Math.max(lineH, 16); // ensure at least one line
    prevTextareaHeight = Math.max(textarea.offsetHeight || 0, minH);
  }

  // First, auto-size the textarea within its max-height
  textarea.style.height = 'auto';
  const cs = getComputedStyle(textarea);
  const maxH = parseFloat(cs.maxHeight) || Infinity;
  const lineH = parseFloat(cs.lineHeight) || 18;
  const minH = Math.max(lineH, 16);
  const scrollH = textarea.scrollHeight || 0;

  let newTextareaHeight;
  if (scrollH <= maxH) {
    textarea.style.overflowY = 'hidden';
    newTextareaHeight = Math.max(scrollH, minH);
    textarea.style.height = newTextareaHeight + 'px';
  } else {
    textarea.style.overflowY = 'auto';
    newTextareaHeight = Math.max(maxH, minH);
    textarea.style.height = newTextareaHeight + 'px';
  }

};

const syncChatTextareaHeight = (reason = 'sync') => {
  if (!textarea) return;
  adjustHeight(`${reason}:now`);
  const schedule = typeof requestAnimationFrame === 'function'
    ? requestAnimationFrame
    : (cb) => setTimeout(cb, 0);
  schedule(() => adjustHeight(`${reason}:next`));
};

const SYNC_VISIBILITY_MAX_ATTEMPTS = 10;
const SYNC_VISIBILITY_DELAY_MS = 80;

const syncChatTextareaHeightWhenVisible = (reason = 'sync', attempt = 0) => {
  if (!textarea) return;
  const cs = getComputedStyle(textarea);
  const hiddenByLayout = textarea.offsetParent === null || cs.display === 'none' || cs.visibility === 'hidden';
  const collapsed = (textarea.offsetHeight || textarea.clientHeight || 0) === 0;
  if ((hiddenByLayout || collapsed) && attempt < SYNC_VISIBILITY_MAX_ATTEMPTS) {
    const nextAttempt = attempt + 1;
    const delay = nextAttempt <= 2 && typeof requestAnimationFrame === 'function'
      ? 0
      : SYNC_VISIBILITY_DELAY_MS;
    const schedule = delay === 0 && typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame
      : (cb) => setTimeout(cb, delay);
    schedule(() => syncChatTextareaHeightWhenVisible(reason, nextAttempt));
    return;
  }
  syncChatTextareaHeight(attempt ? `${reason}:retry${attempt}` : reason);
};

if (textarea) {
  textarea.addEventListener('input', () => {
    if (isChatInputDeferredInputWorkActive()) {
      return;
    }
    adjustHeight('input');
  });
  // Initialize sizing once DOM is ready, but ensure we retry if hidden
  syncChatTextareaHeightWhenVisible('init');
}



// when there is nothing in the input, then show the call button
// if there is text in the input, then show the send button

const chatInput = document.getElementById("chatBoxInput");
const callButton = document.getElementById("chatBoxCallButton");
const realtimeMuteButton = document.getElementById('chatBoxRealtimeMuteButton');
const sendButton = document.getElementById("chatBoxSendButton");
const chatStopButton = document.getElementById("chatBoxStopButton");
const fileButtons = [document.getElementById('chatBoxFilesButton')];
let deferredChatInputWorkScheduled = false;
const DEFERRED_CHAT_INPUT_WORK_FALLBACK_MS = 250;

function scheduleDeferredChatInputWork(reason = 'deferred-input') {
  if (deferredChatInputWorkScheduled || !chatInput) {
    return;
  }
  deferredChatInputWorkScheduled = true;

  const runDeferredWork = () => {
    if (!deferredChatInputWorkScheduled) {
      return;
    }
    deferredChatInputWorkScheduled = false;
    if (!chatInput) {
      return;
    }
    adjustHeight(reason);
    writeChatInputDraft(chatInput.value || '');
    void handleSkillMentionInput();
    const scheduleScroll = typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame
      : (callback) => setTimeout(callback, 0);
    scheduleScroll(() => { chatInput.scrollTop = chatInput.scrollHeight; });
  };

  // Give the browser one paint opportunity before the expensive localStorage,
  // layout, and mention-scan work runs for very large pasted text. The fallback
  // timer is independent because requestAnimationFrame can pause in hidden tabs.
  if (typeof requestAnimationFrame === 'function') {
    const fallbackTimer = setTimeout(runDeferredWork, DEFERRED_CHAT_INPUT_WORK_FALLBACK_MS);
    requestAnimationFrame(() => {
      clearTimeout(fallbackTimer);
      setTimeout(runDeferredWork, 0);
    });
  } else {
    setTimeout(runDeferredWork, 0);
  }
}

const REALTIME_MUTE_ICON = Icons.microphoneMute;
const REALTIME_UNMUTE_ICON = Icons.microphoneMute;

function isRealtimeCallActive() {
  return Boolean(window.realtimeCall && typeof window.realtimeCall.isActive === 'function' && window.realtimeCall.isActive());
}

function isRealtimeCallConnecting() {
  if (!window.realtimeCall || typeof window.realtimeCall.getSessionState !== 'function') {
    return false;
  }
  const sessionState = window.realtimeCall.getSessionState();
  return Boolean(sessionState && sessionState.connecting);
}

function getRealtimeMutedState() {
  if (!window.realtimeCall || typeof window.realtimeCall.getSessionState !== 'function') {
    return false;
  }
  const sessionState = window.realtimeCall.getSessionState();
  return Boolean(sessionState && sessionState.muted);
}

function updateRealtimeMuteButtonUi() {
  if (!realtimeMuteButton) return;
  const muted = getRealtimeMutedState();
  realtimeMuteButton.classList.toggle('is-active', muted);
  realtimeMuteButton.innerHTML = muted ? REALTIME_UNMUTE_ICON : REALTIME_MUTE_ICON;
  const label = muted
    ? getChatI18nString('chat_call_unmute_microphone', 'Unmute microphone')
    : getChatI18nString('chat_call_mute_microphone', 'Mute microphone');
  realtimeMuteButton.title = label;
  realtimeMuteButton.setAttribute('aria-label', label);
}

function focusChatInput({ defer = true } = {}) {
  const inputEl = chatInput || document.getElementById('chatBoxInput');
  if (!inputEl || typeof inputEl.focus !== 'function') {
    return;
  }

  const attemptFocus = () => {
    if (inputEl.matches('[disabled], [aria-disabled="true"]')) {
      return;
    }
    try {
      inputEl.focus({ preventScroll: true });
    } catch (_) {
      inputEl.focus();
    }
    if (typeof inputEl.setSelectionRange === 'function') {
      const valueLength = inputEl.value?.length ?? 0;
      inputEl.setSelectionRange(valueLength, valueLength);
    }
  };

  if (document.visibilityState === 'hidden') {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        document.removeEventListener('visibilitychange', handleVisibilityChange);
        requestAnimationFrame(attemptFocus);
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange, { once: true });
    return;
  }

  if (defer) {
    requestAnimationFrame(attemptFocus);
  } else {
    attemptFocus();
  }
}

if (typeof window !== 'undefined') {
  window.focusChatInput = focusChatInput;
  document.addEventListener('chatSetupReady', () => focusChatInput(), { once: true });
}

function hasChatInputValue() {
  return Boolean(chatInput && chatInput.value.trim() !== '');
}

function hasChatAttachmentsSelected() {
  return Boolean(chatBoxFilesContainer && chatBoxFilesContainer.children.length > 0) || chatBoxAttachmentElements.size > 0;
}

function hasSendableChatContent() {
  return hasChatInputValue() || hasChatAttachmentsSelected();
}

let chatModelSelectionResolved = false;

function getChatSelectedModelId() {
  return String(document.getElementById('modelSelect')?.getAttribute('data-model-id') || '').trim();
}

function hasChatModelForSend(modelId = getChatSelectedModelId()) {
  return Boolean(String(modelId || '').trim());
}

function isChatModelSelectionReady() {
  return chatModelSelectionResolved;
}

function getChatModelResolutionAction() {
  let isAdmin = false;
  try {
    const adminFlag = String(localStorage.getItem('is_admin') || '').trim().toLowerCase();
    isAdmin = adminFlag === 'true' || adminFlag === '1' || adminFlag === 'admin';
  } catch (_) {
    isAdmin = false;
  }

  if (isAdmin) {
    return {
      label: getChatI18nString('chat_model_manage_models', 'Manage models'),
      run: () => window.open('/admin/models', '_blank', 'noopener,noreferrer'),
    };
  }
  if (window.BYOK?.isAllowed?.() && typeof window.openUserSettings === 'function') {
    return {
      label: getChatI18nString('byok_model_add_short', 'Add model'),
      run: () => window.openUserSettings('byok'),
    };
  }
  return {
    label: getChatI18nString('workspace_connections_refresh', 'Refresh'),
    run: async () => {
      await window.ModelSelectLoadModels?.({ forceRefresh: true });
      if (!hasChatModelForSend()) {
        await window.openModelSelect?.();
      }
    },
  };
}

function updateChatModelAvailabilityUi() {
  const status = document.getElementById('chatModelUnavailable');
  const actionButton = document.getElementById('chatModelUnavailableAction');
  const unavailable = chatModelSelectionResolved && !hasChatModelForSend();
  if (status) {
    status.hidden = !unavailable;
  }
  if (actionButton && unavailable) {
    const action = getChatModelResolutionAction();
    actionButton.textContent = action.label;
    actionButton.onclick = () => Promise.resolve(action.run()).catch((error) => {
      console.error('Failed to open model access resolution', error);
    });
  }
}

function showChatModelUnavailableFeedback() {
  const isResolved = isChatModelSelectionReady();
  const message = isResolved
    ? getChatI18nString(
      'chat_model_unavailable_message',
      'No model is available for your account. Ask an administrator for access, or add your own model if your account allows it.',
    )
    : getChatI18nString('chat_models_loading', 'Models are still loading. Please wait.');
  if (!isResolved) {
    notifyWarning?.(message);
    return false;
  }
  const action = getChatModelResolutionAction();
  notifyWarning?.(message, {
    duration: 8000,
    actionLabel: action.label,
    onAction: action.run,
  });
  return false;
}

if (typeof window !== 'undefined') {
  window.hasChatModelForSend = hasChatModelForSend;
  window.isChatModelSelectionReady = isChatModelSelectionReady;
  window.showChatModelUnavailableFeedback = showChatModelUnavailableFeedback;
}

function hasQueueableChatContent() {
  if (hasChatInputValue()) {
    return true;
  }

  const chatBoxFilesEl = document.getElementById('chatBoxFiles');
  if (chatBoxFilesEl && chatBoxFilesEl.children.length > 0) {
    return true;
  }

  const snapshot = typeof captureChatComposerStateSnapshot === 'function'
    ? captureChatComposerStateSnapshot()
    : null;
  if (!snapshot || typeof snapshot !== 'object') {
    return false;
  }

  const hasSnapshotAttachments = Array.isArray(snapshot.uploadedFiles) && snapshot.uploadedFiles.length > 0;
  const hasSnapshotReferences = Array.isArray(snapshot.chatReferences) && snapshot.chatReferences.length > 0;
  const hasSnapshotReferenceParts = Array.isArray(snapshot.referenceParts) && snapshot.referenceParts.length > 0;
  return hasSnapshotAttachments || hasSnapshotReferences || hasSnapshotReferenceParts;
}

function getQueuedMessageCount() {
  if (!window.messageQueue || typeof window.messageQueue.length !== 'function') {
    return 0;
  }
  const count = Number(window.messageQueue.length());
  return Number.isFinite(count) ? count : 0;
}

function tryQueueCurrentInput({ showOverlay = false } = {}) {
  if (!hasQueueableChatContent()) {
    return false;
  }
  if (!hasChatModelForSend()) {
    showChatModelUnavailableFeedback();
    return true;
  }
  if (window.messageQueue && typeof window.messageQueue.queueInput === 'function') {
    return window.messageQueue.queueInput({ showOverlay });
  }
  return false;
}
const chatBoxFilesDropdownContainer = document.getElementById('chatBoxFilesDropdownContainer');
const chatBoxFilesDropdownMenu = document.getElementById('chatBoxFilesDropdown');
const chatBoxThinkingContainer = document.getElementById('chatBoxThinkingContainer');
const chatBoxThinkingButton = document.getElementById('chatBoxThinkingButton');
const chatBoxThinkingLabel = document.getElementById('chatBoxThinkingLabel');
const chatBoxThinkingDropdownMenu = document.getElementById('chatBoxThinkingDropdown');
const chatBoxAddMeetingButton = document.getElementById('chatBoxAddMeetingButton');
const chatBoxUploadFromComputerButton = document.getElementById('chatBoxUploadFromComputerButton');
const chatBoxQuickScreenCaptureButton = document.getElementById('chatBoxQuickScreenCaptureButton');
const chatBoxAddGoogleDriveButton = document.getElementById('chatBoxAddGoogleDriveButton');
const chatBoxAddGoogleDriveMenuItem = document.getElementById('chatBoxAddGoogleDriveMenuItem')
  || chatBoxAddGoogleDriveButton?.closest('.select-dropdown-item')
  || null;
const CHATBOX_FILES_MENU_ICON_MAP = new Map([
  ['chatBoxAddMeetingButton', 'chatFilesAddMeeting'],
  ['chatBoxUploadFromComputerButton', 'share'],
  ['chatBoxQuickScreenCaptureButton', 'chatFilesScreenCapture'],
  ['chatBoxOpenConnectionsButton', 'connections'],
  ['chatBoxAddGoogleDriveButton', 'google_drive'],
  ['chatBoxChooseChatReferencesButton', 'chatFilesChooseChats'],
  ['chatBoxChooseUploadedFilesButton', 'chatFilesChooseUploaded'],
]);
const CHATBOX_FILES_MENU_ACTIONS = [
  {
    action: 'computer',
    labelKey: 'chat_files_upload_files',
    fallback: 'Upload files',
    iconName: 'share',
  },
  {
    action: 'meeting',
    labelKey: 'chat_files_add_meeting',
    fallback: 'Add meeting',
    iconName: 'chatFilesAddMeeting',
  },
  {
    action: 'screenCapture',
    labelKey: 'chat_files_quick_screen_capture',
    fallback: 'Quick screenshot',
    iconName: 'chatFilesScreenCapture',
  },
  {
    action: 'connections',
    labelKey: 'workspace_tab_connections',
    fallback: 'Connections',
    iconName: 'connections',
    children: [
      {
        action: 'googleDrive',
        labelKey: 'chat_files_add_google_drive',
        fallback: 'Add from Google Drive',
        iconName: 'google_drive',
        provider: 'google_drive',
      },
    ],
  },
  {
    action: 'chatReferences',
    labelKey: 'chat_files_choose_chats',
    fallback: 'Choose chats',
    iconName: 'chatFilesChooseChats',
  },
  {
    action: 'uploadedFiles',
    labelKey: 'chat_files_choose_uploaded',
    fallback: 'Choose uploaded files',
    iconName: 'chatFilesChooseUploaded',
  },
];

function createChatFilesMenuIcon(iconName) {
  const icon = document.createElement('span');
  icon.className = 'chatbox-files-menu-icon';
  icon.setAttribute('aria-hidden', 'true');
  const iconMarkup = typeof Icons === 'object' && Icons ? Icons[iconName] : '';
  if (typeof iconMarkup === 'string' && iconMarkup.trim()) {
    icon.innerHTML = iconMarkup;
  }
  return icon;
}

function createChatFilesMenuButton(item) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'select-dropdown-button';
  button.dataset.chatFilesAction = item.action;
  button.appendChild(createChatFilesMenuIcon(item.iconName));

  const label = document.createElement('span');
  label.textContent = getChatI18nString(item.labelKey, item.fallback);
  button.appendChild(label);

  return button;
}

function createChatFilesPanelHeader({
  titleKey = 'workspace_tab_connections',
  titleFallback = 'Connections',
  subtitleKey = '',
  subtitleFallback = '',
} = {}) {
  const header = document.createElement('header');
  header.className = 'select-dropdown-panel-header';
  header.dataset.dropdownPanelHeader = '';

  const backButton = document.createElement('button');
  backButton.type = 'button';
  backButton.className = 'select-dropdown-panel-back';
  backButton.dataset.dropdownPanelBack = '';
  backButton.dataset.i18nAttr = 'aria-label:chat_files_back_aria';
  backButton.setAttribute('aria-label', getChatI18nString('chat_files_back_aria', 'Back to add to chat'));
  backButton.innerHTML = '<span aria-hidden="true"></span>';

  const heading = document.createElement('div');
  heading.className = 'select-dropdown-panel-heading';
  const title = document.createElement('strong');
  title.dataset.i18n = titleKey;
  title.textContent = getChatI18nString(titleKey, titleFallback);
  heading.appendChild(title);
  if (subtitleKey) {
    const subtitle = document.createElement('span');
    subtitle.dataset.i18n = subtitleKey;
    subtitle.textContent = getChatI18nString(subtitleKey, subtitleFallback);
    heading.appendChild(subtitle);
  }
  header.append(backButton, heading);
  return header;
}

function createChatFilesQuickpickPanel({
  name,
  titleKey,
  titleFallback,
  searchPlaceholderKey,
  searchPlaceholderFallback,
  searchAriaKey,
  searchAriaFallback,
  emptyKey,
  emptyFallback,
}) {
  const panel = document.createElement('div');
  panel.className = 'select-dropdown-panel';
  panel.dataset.dropdownPanel = name;
  panel.dataset.dropdownPanelHeight = '420';
  panel.setAttribute('aria-hidden', 'true');
  panel.inert = true;
  panel.appendChild(createChatFilesPanelHeader({ titleKey, titleFallback }));

  const searchWrap = document.createElement('div');
  searchWrap.className = 'chatbox-attachment-search-wrap';
  const searchIcon = document.createElement('span');
  searchIcon.className = 'chatbox-attachment-search-icon';
  searchIcon.setAttribute('aria-hidden', 'true');
  const searchInput = document.createElement('input');
  searchInput.type = 'search';
  searchInput.dataset.chatFilesSearch = name;
  searchInput.placeholder = getChatI18nString(searchPlaceholderKey, searchPlaceholderFallback);
  searchInput.setAttribute('aria-label', getChatI18nString(searchAriaKey, searchAriaFallback));
  searchWrap.append(searchIcon, searchInput);

  const scroll = document.createElement('div');
  scroll.className = 'select-dropdown-panel-scroll';
  scroll.dataset.chatFilesScroll = name;
  const list = document.createElement('div');
  list.className = 'chatbox-files-quickpick__list';
  list.dataset.chatFilesList = name;
  const empty = document.createElement('div');
  empty.className = 'chatbox-files-quickpick__empty';
  empty.dataset.chatFilesEmpty = name;
  empty.dataset.i18n = emptyKey;
  empty.textContent = getChatI18nString(emptyKey, emptyFallback);
  empty.hidden = true;
  scroll.append(list, empty);
  panel.append(searchWrap, scroll);
  return panel;
}

function createChatFilesMenuElement({ actions = {}, onPanelHeightChange = null, onNavigate = null } = {}) {
  const dropdown = document.createElement('div');
  dropdown.className = 'select-dropdown select-dropdown-panel-menu chatbox-attachment-menu js-chat-files-menu';
  dropdown.setAttribute('role', 'dialog');
  dropdown.setAttribute('aria-label', getChatI18nString('chat_files_menu_aria', 'Add to chat'));
  dropdown.setAttribute('aria-hidden', 'true');

  const mainPanel = document.createElement('div');
  mainPanel.className = 'select-dropdown-panel is-active';
  mainPanel.dataset.dropdownPanel = 'main';
  const mainContent = document.createElement('div');
  mainContent.className = 'select-dropdown-panel-scroll select-dropdown-panel-content';
  mainContent.dataset.dropdownPanelContent = '';
  mainPanel.appendChild(mainContent);

  const connectionsPanel = document.createElement('div');
  connectionsPanel.className = 'select-dropdown-panel';
  connectionsPanel.dataset.dropdownPanel = 'connections';
  connectionsPanel.setAttribute('aria-hidden', 'true');
  connectionsPanel.inert = true;
  connectionsPanel.appendChild(createChatFilesPanelHeader({
    titleKey: 'workspace_tab_connections',
    titleFallback: 'Connections',
    subtitleKey: 'chat_files_connections_subtitle',
    subtitleFallback: 'Choose where to browse',
  }));
  const connectionsContent = document.createElement('div');
  connectionsContent.className = 'select-dropdown-panel-scroll select-dropdown-panel-content';
  connectionsContent.dataset.dropdownPanelContent = '';
  connectionsPanel.appendChild(connectionsContent);

  CHATBOX_FILES_MENU_ACTIONS.forEach((item, index) => {
    if (index === 0 || item.action === 'connections') {
      if (item.action === 'connections') {
        const divider = document.createElement('div');
        divider.className = 'chatbox-attachment-divider';
        mainContent.appendChild(divider);
      }
      const sectionLabel = document.createElement('div');
      sectionLabel.className = 'chatbox-attachment-section-label';
      sectionLabel.dataset.i18n = item.action === 'connections' ? 'chat_files_browse' : 'chat_files_create_upload';
      sectionLabel.textContent = getChatI18nString(
        item.action === 'connections' ? 'chat_files_browse' : 'chat_files_create_upload',
        item.action === 'connections' ? 'Browse' : 'Create & upload',
      );
      mainContent.appendChild(sectionLabel);
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'select-dropdown-item';
    wrapper.dataset.chatFilesActionItem = item.action;

    if (item.children) {
      const button = createChatFilesMenuButton(item);
      button.dataset.dropdownOpenPanel = 'connections';
      button.setAttribute('aria-expanded', 'false');
      wrapper.appendChild(button);
      item.children.forEach((child) => {
        const childWrapper = document.createElement('div');
        childWrapper.className = 'select-dropdown-item';
        childWrapper.dataset.chatFilesProviderItem = child.provider;
        const childButton = createChatFilesMenuButton(child);
        const providerState = document.createElement('span');
        providerState.className = 'chatbox-attachment-provider-state';
        providerState.dataset.i18n = 'workspace_connections_status_connected';
        providerState.textContent = getChatI18nString('workspace_connections_status_connected', 'Connected');
        childButton.appendChild(providerState);
        childWrapper.appendChild(childButton);
        connectionsContent.appendChild(childWrapper);
      });
    } else {
      const button = createChatFilesMenuButton(item);
      if (item.action === 'chatReferences') {
        button.dataset.dropdownOpenPanel = 'chats';
        button.setAttribute('aria-expanded', 'false');
      } else if (item.action === 'uploadedFiles') {
        button.dataset.dropdownOpenPanel = 'files';
        button.setAttribute('aria-expanded', 'false');
      }
      wrapper.appendChild(button);
    }

    mainContent.appendChild(wrapper);
  });
  const chatsPanel = createChatFilesQuickpickPanel({
    name: 'chats',
    titleKey: 'chat_files_choose_chats',
    titleFallback: 'Choose chats',
    searchPlaceholderKey: 'chat_files_quickpick_search_chats_placeholder',
    searchPlaceholderFallback: 'Search chats…',
    searchAriaKey: 'chat_files_quickpick_search_chats_aria',
    searchAriaFallback: 'Quick search chats',
    emptyKey: 'chat_files_quickpick_empty_chats',
    emptyFallback: 'No chats found',
  });
  const filesPanel = createChatFilesQuickpickPanel({
    name: 'files',
    titleKey: 'chat_files_choose_uploaded',
    titleFallback: 'Choose uploaded files',
    searchPlaceholderKey: 'chat_files_quickpick_search_files_placeholder',
    searchPlaceholderFallback: 'Search files…',
    searchAriaKey: 'chat_files_quickpick_search_files_aria',
    searchAriaFallback: 'Quick search uploaded files',
    emptyKey: 'chat_files_quickpick_empty_files',
    emptyFallback: 'No files found',
  });
  dropdown.append(mainPanel, chatsPanel, filesPanel, connectionsPanel);

  dropdown.addEventListener('click', (event) => {
    const button = event.target?.closest?.('[data-chat-files-action]');
    if (!button || !dropdown.contains(button)) {
      return;
    }
    const action = button.dataset.chatFilesAction;
    const handler = actions[action];
    if (typeof handler !== 'function') {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    handler(event);
  });

  updateChatFilesMenuElementVisibility(dropdown);
  window.createDropdownPanelNavigator?.({
    dropdown,
    getFocusTarget: (panelName, panel) => (
      panelName === 'files' || panelName === 'chats'
        ? panel.querySelector('[data-chat-files-search]')
        : panel.querySelector('button')
    ),
    onNavigate,
    onHeightChange: () => onPanelHeightChange?.(dropdown),
  });
  return dropdown;
}

function updateChatFilesMenuElementVisibility(root) {
  if (!root) {
    return;
  }
  const allowUploads = resolveChatSetting('allow_file_uploads', true);
  const allowChatReferences = true;
  const uploadVisible = allowUploads;
  const setActionVisible = (action, visible) => {
    root.querySelectorAll(`[data-chat-files-action-item="${action}"]`).forEach((item) => {
      item.style.display = visible ? '' : 'none';
    });
  };
  ['meeting', 'computer', 'screenCapture', 'uploadedFiles'].forEach((action) => setActionVisible(action, uploadVisible));
  setActionVisible('chatReferences', allowChatReferences);

  const providerVisibility = {
    google_drive: uploadVisible && Boolean(chatBoxConnectionsAvailabilityState.google_drive),
  };
  Object.entries(providerVisibility).forEach(([provider, visible]) => {
    root.querySelectorAll(`[data-chat-files-provider-item="${provider}"]`).forEach((item) => {
      item.style.display = visible ? '' : 'none';
    });
  });
  const showConnections = Object.values(providerVisibility).some(Boolean);
  setActionVisible('connections', showConnections);
  if (!showConnections) {
    const navigator = window.getDropdownPanelNavigator?.(root);
    if (navigator?.activePanel === 'connections') {
      navigator.reset({ focus: false });
    }
  }
  window.getDropdownPanelNavigator?.(root)?.syncHeight();
}

function applyChatBoxFilesMenuIcons() {
  if (typeof Icons !== 'object' || !Icons) {
    return;
  }

  CHATBOX_FILES_MENU_ICON_MAP.forEach((iconName, buttonId) => {
    const buttonEl = document.getElementById(buttonId);
    const iconContainer = buttonEl?.querySelector('.chatbox-files-menu-icon');
    const iconMarkup = Icons[iconName];
    if (!iconContainer || typeof iconMarkup !== 'string' || !iconMarkup.trim()) {
      return;
    }
    iconContainer.innerHTML = iconMarkup;
  });

}

applyChatBoxFilesMenuIcons();
const microphoneButtons = [document.getElementById('chatBoxVoiceButton')];
const chatBoxConnectionsAvailabilityState = {
  loaded: false,
  loading: false,
  google_drive: false,
};

function translateChatBoxThinkingText(key, fallback) {
  if (typeof window !== 'undefined' && typeof window.t === 'function') {
    const translated = window.t(key);
    if (translated && translated !== key) {
      return translated;
    }
  }
  return fallback;
}

function toggleChatBoxThinkingDropdown(open) {
  if (!chatBoxThinkingDropdownController) return;
  const shouldOpen = typeof open === 'boolean'
    ? open
    : !chatBoxThinkingDropdownController.isOpen();
  chatBoxThinkingDropdownController[shouldOpen ? 'open' : 'close']({ reason: 'api' });
}

function isSplitScreenComposerMode() {
  if (typeof window !== 'undefined' && window.SplitScreenManager?.active === true) {
    return true;
  }
  return Boolean(document.body?.classList?.contains('split-screen-active'));
}

function renderChatBoxThinkingControl() {
  if (chatBoxThinkingButton) {
    // Keep the thinking icon as a direct SVG child of the button. The marker
    // lets repeated renders replace or remove the icon without duplicating it.
    const existingIcon = Array.from(chatBoxThinkingButton.children)
      .find((child) => child.matches('svg[data-chat-box-thinking-icon]'));
    const thinkingIconMarkup = (typeof Icons === 'object' && Icons?.thinking)
      ? Icons.thinking
      : '';

    if (existingIcon) {
      if (thinkingIconMarkup) {
        existingIcon.outerHTML = thinkingIconMarkup;
        const updatedIcon = chatBoxThinkingButton.firstElementChild;
        updatedIcon?.setAttribute('data-chat-box-thinking-icon', '');
        updatedIcon?.setAttribute('aria-hidden', 'true');
      } else {
        existingIcon.remove();
      }
    } else if (thinkingIconMarkup) {
      chatBoxThinkingButton.insertAdjacentHTML('afterbegin', thinkingIconMarkup);
      const insertedIcon = chatBoxThinkingButton.firstElementChild;
      insertedIcon?.setAttribute('data-chat-box-thinking-icon', '');
      insertedIcon?.setAttribute('aria-hidden', 'true');
    }
  }
  if (!chatBoxThinkingButton || !chatBoxThinkingLabel || !chatBoxThinkingDropdownMenu) {
    return null;
  }

  const thinkingState = typeof window.getQuickThinkingControlState === 'function'
    ? window.getQuickThinkingControlState()
    : null;
  const supported = Boolean(thinkingState && Array.isArray(thinkingState.options) && thinkingState.options.length);

  chatBoxThinkingButton.disabled = !supported;
  chatBoxThinkingButton.setAttribute(
    'title',
    supported
      ? `${thinkingState.label || translateChatBoxThinkingText('chatbox_thinking_button_label', 'Thinking')}: ${thinkingState.currentLabel || ''}`.trim()
      : translateChatBoxThinkingText('chatbox_thinking_button_label', 'Thinking')
  );
  chatBoxThinkingButton.setAttribute(
    'aria-label',
    supported
      ? `${thinkingState.label || translateChatBoxThinkingText('chatbox_thinking_button_label', 'Thinking')}: ${thinkingState.currentLabel || ''}`.trim()
      : translateChatBoxThinkingText('chatbox_thinking_button_label', 'Thinking')
  );

  if (!supported) {
    chatBoxThinkingLabel.textContent = translateChatBoxThinkingText('chatbox_thinking_button_label', 'Thinking');
    chatBoxThinkingDropdownMenu.innerHTML = '';
    toggleChatBoxThinkingDropdown(false);
    return null;
  }

  chatBoxThinkingLabel.textContent = thinkingState.currentLabel || thinkingState.label || translateChatBoxThinkingText('chatbox_thinking_button_label', 'Thinking');
  chatBoxThinkingDropdownMenu.innerHTML = '';

  thinkingState.options.forEach((option) => {
    const item = document.createElement('div');
    item.className = 'select-dropdown-item';

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'select-dropdown-button';
    button.setAttribute('role', 'menuitemradio');
    const isSelected = option.value === thinkingState.currentValue;
    button.setAttribute('aria-checked', isSelected ? 'true' : 'false');
    const label = document.createElement('span');
    label.textContent = option.label;
    button.appendChild(label);

    if (isSelected && typeof Icons === 'object' && Icons?.check) {
      button.insertAdjacentHTML('beforeend', Icons.check);
    }
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (typeof window.applyQuickThinkingControlValue === 'function') {
        window.applyQuickThinkingControlValue(option.value);
      }
      toggleChatBoxThinkingDropdown(false);
      focusChatInput();
    });

    item.appendChild(button);
    chatBoxThinkingDropdownMenu.appendChild(item);
  });

  return thinkingState;
}

// -------------------
// Dictation / Voice Input Feature
// -------------------
const dictationState = {
  isRecording: false,
  isTranscribing: false,
  mediaRecorder: null,
  audioChunks: [],
  stream: null,
  abortController: null,
  audioContext: null,
  analyserNode: null,
  analyserData: null,
  mediaStreamSource: null,
  visualizerRafId: null,
  visualizerBars: [],
  visualizerLevels: [],
  lastAudioLevel: 0,
  discardRecordingOnStop: false,
  sessionId: 0,
  sessionCounter: 0,
  recordingStartedAt: null,
  usesLiveTranscription: false,
  // Live transcript events replace this stable selection instead of
  // repeatedly appending deltas. This keeps any text before and after the
  // cursor intact while the provider revises its partial result.
  liveBase: null,
};

const DICTATION_MIC_ICON = Icons.microphone;
const DICTATION_STOP_ICON = `<span class="dictation-stop-icon"></span>`;
const DICTATION_BAR_COUNT = 84;
const DICTATION_BASE_LEVEL = 0.08;

const chatBoxTop = document.getElementById('chatBoxTop');
const dictationVisualizer = document.getElementById('chatBoxDictationVisualizer');
const dictationBarsContainer = document.getElementById('chatBoxDictationBars');

function getDictationVoiceButton() {
  return document.getElementById('chatBoxVoiceButton');
}

function beginDictationSession() {
  const sessionId = dictationState.sessionCounter + 1;
  dictationState.sessionCounter = sessionId;
  dictationState.sessionId = sessionId;
  return sessionId;
}

function isActiveDictationSession(sessionId) {
  return Boolean(sessionId) && dictationState.sessionId === sessionId;
}

function clearDictationSession(sessionId) {
  if (isActiveDictationSession(sessionId)) {
    dictationState.sessionId = 0;
  }
}

function ensureDictationBars() {
  if (!dictationBarsContainer) return;
  if (dictationState.visualizerBars.length > 0) return;

  const fragment = document.createDocumentFragment();
  const initialLevels = [];

  for (let index = 0; index < DICTATION_BAR_COUNT; index += 1) {
    const bar = document.createElement('span');
    bar.className = 'chat-box-dictation-bar';
    bar.style.setProperty('--dictation-bar-level', String(DICTATION_BASE_LEVEL));
    fragment.appendChild(bar);
    initialLevels.push(DICTATION_BASE_LEVEL);
  }

  dictationBarsContainer.style.setProperty('--dictation-bar-count', String(DICTATION_BAR_COUNT));
  dictationBarsContainer.appendChild(fragment);
  dictationState.visualizerBars = Array.from(dictationBarsContainer.children);
  dictationState.visualizerLevels = initialLevels;
}

function paintDictationBars(level = 0) {
  ensureDictationBars();
  if (dictationState.visualizerBars.length === 0) return;

  const isTranscribingOnly = dictationState.isTranscribing && !dictationState.isRecording;
  const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
  const pulse = now / 250;
  const middle = (dictationState.visualizerBars.length - 1) / 2;

  dictationState.visualizerBars.forEach((bar, index) => {
    const previous = dictationState.visualizerLevels[index] ?? DICTATION_BASE_LEVEL;
    const distance = middle <= 0 ? 0 : Math.abs(index - middle) / middle;
    const centerGain = Math.max(0.35, 1 - distance * 0.6);
    const ripple = (Math.sin(pulse + index * 0.3) + 1) * 0.5;
    const shimmer = (Math.sin(pulse * 0.6 + index * 0.17) + 1) * 0.5;

    let target;
    if (isTranscribingOnly) {
      target = 0.18 + ripple * 0.2 + (1 - distance) * 0.06;
    } else {
      const ambient = DICTATION_BASE_LEVEL + ripple * 0.04;
      const speechLift = level * (0.52 + shimmer * 1.08) * centerGain;
      target = ambient + speechLift;
    }

    const smoothing = isTranscribingOnly ? 0.2 : (0.14 + Math.min(level, 1) * 0.15);
    const next = previous + (target - previous) * smoothing;
    const clamped = Math.max(0.06, Math.min(1, next));

    dictationState.visualizerLevels[index] = clamped;
    bar.style.setProperty('--dictation-bar-level', clamped.toFixed(3));
  });
}

function cancelDictationVisualizerLoop() {
  if (dictationState.visualizerRafId !== null) {
    cancelAnimationFrame(dictationState.visualizerRafId);
    dictationState.visualizerRafId = null;
  }
}

function readDictationAudioLevel() {
  if (!dictationState.analyserNode || !dictationState.analyserData) {
    return 0;
  }

  dictationState.analyserNode.getByteTimeDomainData(dictationState.analyserData);
  let sumSquares = 0;
  for (let index = 0; index < dictationState.analyserData.length; index += 1) {
    const centered = (dictationState.analyserData[index] - 128) / 128;
    sumSquares += centered * centered;
  }

  const rms = Math.sqrt(sumSquares / dictationState.analyserData.length);
  const noiseFloor = 0.006;
  if (rms <= noiseFloor) {
    return 0;
  }

  const normalized = Math.min(1, (rms - noiseFloor) * 16);
  const boosted = Math.pow(normalized, 0.52);
  return Math.min(1, boosted);
}

function startDictationVisualizerLoop() {
  if (dictationState.visualizerRafId !== null) return;

  const tick = () => {
    if (!dictationState.isRecording && !dictationState.isTranscribing) {
      dictationState.visualizerRafId = null;
      paintDictationBars(0);
      return;
    }

    const level = dictationState.isRecording
      ? readDictationAudioLevel()
      : Math.max(DICTATION_BASE_LEVEL, dictationState.lastAudioLevel * 0.92);

    dictationState.lastAudioLevel = level;
    paintDictationBars(level);
    dictationState.visualizerRafId = requestAnimationFrame(tick);
  };

  dictationState.visualizerRafId = requestAnimationFrame(tick);
}

function stopDictationAudioTracking() {
  const source = dictationState.mediaStreamSource;
  const analyser = dictationState.analyserNode;
  const context = dictationState.audioContext;

  dictationState.mediaStreamSource = null;
  dictationState.analyserNode = null;
  dictationState.analyserData = null;
  dictationState.audioContext = null;

  try {
    source?.disconnect();
  } catch (_) {}
  try {
    analyser?.disconnect();
  } catch (_) {}
  if (context && typeof context.close === 'function') {
    context.close().catch(() => {});
  }
}

async function startDictationAudioTracking(stream, sessionId) {
  stopDictationAudioTracking();

  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (typeof AudioContextCtor !== 'function') {
    return;
  }

  try {
    const audioContext = new AudioContextCtor();
    if (audioContext.state === 'suspended' && typeof audioContext.resume === 'function') {
      await audioContext.resume().catch(() => {});
    }

    const source = audioContext.createMediaStreamSource(stream);
    const analyserNode = audioContext.createAnalyser();
    analyserNode.fftSize = 1024;
    analyserNode.smoothingTimeConstant = 0.65;

    source.connect(analyserNode);

    if (!isActiveDictationSession(sessionId)) {
      try {
        source.disconnect();
      } catch (_) {}
      try {
        analyserNode.disconnect();
      } catch (_) {}
      audioContext.close().catch(() => {});
      return;
    }

    dictationState.audioContext = audioContext;
    dictationState.mediaStreamSource = source;
    dictationState.analyserNode = analyserNode;
    dictationState.analyserData = new Uint8Array(analyserNode.fftSize);
  } catch (error) {
    console.warn('Unable to initialize dictation visualizer audio tracking.', error);
    stopDictationAudioTracking();
  }
}

function setDictationButtonState(state) {
  const voiceButton = getDictationVoiceButton();
  if (!voiceButton) return;

  voiceButton.classList.remove('is-recording', 'is-transcribing');
  
  switch (state) {
    case 'recording':
      voiceButton.classList.add('is-recording');
      voiceButton.innerHTML = DICTATION_STOP_ICON;
      voiceButton.title = getChatI18nString('chat_voice_stop_recording', 'Stop recording');
      voiceButton.setAttribute('aria-label', getChatI18nString('chat_voice_stop_recording', 'Stop recording'));
      break;
    case 'transcribing':
      voiceButton.classList.add('is-transcribing');
      voiceButton.innerHTML = DICTATION_MIC_ICON;
      voiceButton.title = getChatI18nString('chat_voice_cancel_transcription', 'Cancel transcription');
      voiceButton.setAttribute('aria-label', getChatI18nString('chat_voice_cancel_transcription', 'Cancel transcription'));
      break;
    case 'idle':
    default:
      voiceButton.innerHTML = DICTATION_MIC_ICON;
      voiceButton.title = getChatI18nString('chat_voice_input', 'Voice input');
      voiceButton.setAttribute('aria-label', getChatI18nString('chat_voice_input', 'Voice input'));
      break;
  }
}

function setInputTranscribingState(isTranscribing) {
  const input = document.getElementById('chatBoxInput');
  if (!input) return;

  if (!chatBoxTop || !dictationVisualizer) {
    if (isTranscribing) {
      input.classList.add('is-transcribing');
      if (!input.dataset.originalPlaceholder) {
        input.dataset.originalPlaceholder = input.placeholder;
      }
      input.placeholder = getChatI18nString('chat_dictation_transcribing_placeholder', 'Transcribing audio...');
    } else {
      input.classList.remove('is-transcribing');
      if (input.dataset.originalPlaceholder) {
        input.placeholder = input.dataset.originalPlaceholder;
        delete input.dataset.originalPlaceholder;
      }
    }
    return;
  }

  if (dictationState.usesLiveTranscription) {
    // Live dictation writes directly into the normal composer. Do not replace
    // it with the recording/transcribing visualizer used by file dictation.
    chatBoxTop.classList.remove(
      'dictation-active',
      'dictation-transcribing',
    );
    dictationVisualizer.hidden = true;
    input.hidden = false;
    input.classList.remove('is-transcribing');
    input.removeAttribute('aria-hidden');
    input.removeAttribute('tabindex');
    syncChatTextareaHeightWhenVisible('live-dictation-update');
    return;
  }

  const state = dictationState.isRecording
    ? 'recording'
    : (isTranscribing ? 'transcribing' : 'idle');
  const isActive = state !== 'idle';

  chatBoxTop.classList.toggle('dictation-active', isActive);
  chatBoxTop.classList.toggle('dictation-transcribing', state === 'transcribing');

  dictationVisualizer.hidden = !isActive;
  input.hidden = isActive;
  input.classList.toggle('is-transcribing', isTranscribing);

  if (isActive) {
    input.setAttribute('aria-hidden', 'true');
    input.setAttribute('tabindex', '-1');
  } else {
    input.removeAttribute('aria-hidden');
    input.removeAttribute('tabindex');
    syncChatTextareaHeightWhenVisible('dictation-finished');
  }
}

async function startDictationRecording() {
  if (
    dictationState.isRecording ||
    dictationState.isTranscribing ||
    isRealtimeCallActive() ||
    isRealtimeCallConnecting()
  ) {
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (isRealtimeCallActive() || isRealtimeCallConnecting()) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    const sessionId = beginDictationSession();
    dictationState.usesLiveTranscription = false;
    dictationState.stream = stream;
    dictationState.audioChunks = [];
    dictationState.lastAudioLevel = 0;
    dictationState.discardRecordingOnStop = false;
    await startDictationAudioTracking(stream, sessionId);
    if (!isActiveDictationSession(sessionId)) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    // Try to use webm format first (better compression), fall back to alternatives
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : MediaRecorder.isTypeSupported('audio/mp4')
          ? 'audio/mp4'
          : '';

    const recorderOptions = mimeType ? { mimeType } : {};
    const mediaRecorder = new MediaRecorder(stream, recorderOptions);
    dictationState.mediaRecorder = mediaRecorder;

    mediaRecorder.ondataavailable = (event) => {
      if (!isActiveDictationSession(sessionId)) {
        return;
      }
      if (event.data.size > 0) {
        dictationState.audioChunks.push(event.data);
      }
    };

    mediaRecorder.onstop = async () => {
      // Stop all tracks
      stream.getTracks().forEach(track => track.stop());
      if (!isActiveDictationSession(sessionId)) {
        return;
      }
      dictationState.stream = null;
      if (dictationState.mediaRecorder === mediaRecorder) {
        dictationState.mediaRecorder = null;
      }
      stopDictationAudioTracking();

      const discardRecordingOnStop = dictationState.discardRecordingOnStop;
      const audioChunks = Array.isArray(dictationState.audioChunks) ? [...dictationState.audioChunks] : [];
      const recordedDurationSeconds = dictationState.recordingStartedAt == null
        ? null
        : Math.max((performance.now() - dictationState.recordingStartedAt) / 1000, 0.001);
      dictationState.recordingStartedAt = null;
      dictationState.audioChunks = [];
      dictationState.discardRecordingOnStop = false;

      if (discardRecordingOnStop) {
        setDictationButtonState('idle');
        setInputTranscribingState(false);
        clearDictationSession(sessionId);
        return;
      }

      if (audioChunks.length === 0) {
        setDictationButtonState('idle');
        setInputTranscribingState(false);
        clearDictationSession(sessionId);
        return;
      }

      // Create audio blob
      const audioBlob = new Blob(audioChunks, {
        type: mediaRecorder.mimeType || 'audio/webm' 
      });

      // Start transcription
      await transcribeAudio(audioBlob, sessionId, recordedDurationSeconds);
    };

    mediaRecorder.onerror = (event) => {
      console.error('MediaRecorder error:', event.error);
      stream.getTracks().forEach((track) => track.stop());
      if (!isActiveDictationSession(sessionId)) {
        return;
      }
      dictationState.isRecording = false;
      dictationState.discardRecordingOnStop = true;
      if (dictationState.mediaRecorder === mediaRecorder) {
        dictationState.mediaRecorder = null;
      }
      dictationState.stream = null;
      stopDictationAudioTracking();
      setDictationButtonState('idle');
      setInputTranscribingState(false);
      clearDictationSession(sessionId);
      if (typeof notifyError === 'function') {
        notifyError(getChatI18nString('chat_dictation_recording_failed', 'Recording failed. Please try again.'));
      }
    };

    // Start recording
    dictationState.recordingStartedAt = performance.now();
    mediaRecorder.start(1000); // Collect data every second
    dictationState.isRecording = true;
    setDictationButtonState('recording');
    setInputTranscribingState(false);
    startDictationVisualizerLoop();

  } catch (error) {
    console.error('Failed to start recording:', error);
    dictationState.isRecording = false;
    dictationState.mediaRecorder = null;
    clearDictationSession(dictationState.sessionId);
    if (dictationState.stream) {
      dictationState.stream.getTracks().forEach((track) => track.stop());
      dictationState.stream = null;
    }
    stopDictationAudioTracking();
    setDictationButtonState('idle');
    setInputTranscribingState(false);
    if (error.name === 'NotAllowedError') {
      if (typeof notifyError === 'function') {
        notifyError(getChatI18nString('chat_dictation_microphone_access_denied', 'Microphone access denied. Please allow microphone access to use voice input.'));
      }
    } else if (error.name === 'NotFoundError') {
      if (typeof notifyError === 'function') {
        notifyError(getChatI18nString('chat_dictation_no_microphone_found', 'No microphone found. Please connect a microphone to use voice input.'));
      }
    } else {
      if (typeof notifyError === 'function') {
        notifyError(getChatI18nString('chat_dictation_could_not_start_recording', 'Could not start recording. Please try again.'));
      }
    }
  }
}

function resetLiveDictationUi(sessionId) {
  if (!isActiveDictationSession(sessionId)) return;
  const input = document.getElementById('chatBoxInput');
  dictationState.isRecording = false;
  dictationState.isTranscribing = false;
  dictationState.usesLiveTranscription = false;
  dictationState.liveBase = null;
  dictationState.stream = null;
  dictationState.lastAudioLevel = 0;
  if (input) {
    input.readOnly = false;
  }
  stopDictationAudioTracking();
  cancelDictationVisualizerLoop();
  paintDictationBars(0);
  setDictationButtonState('idle');
  setInputTranscribingState(false);
  clearDictationSession(sessionId);
}

function applyChatBoxLiveTranscript(text) {
  const input = document.getElementById('chatBoxInput');
  const base = dictationState.liveBase;
  if (!input || !base) {
    return;
  }

  const transcript = String(text || '').trim();
  let insertText = transcript;
  if (
    insertText
    && base.start > 0
    && base.value[base.start - 1]
    && !/\s/.test(base.value[base.start - 1])
  ) {
    insertText = ` ${insertText}`;
  }

  input.value = `${base.value.slice(0, base.start)}${insertText}${base.value.slice(base.end)}`;
  const cursor = base.start + insertText.length;
  input.setSelectionRange(cursor, cursor);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

async function startLiveDictationRecording() {
  if (
    dictationState.isRecording
    || dictationState.isTranscribing
    || isRealtimeCallActive()
    || isRealtimeCallConnecting()
  ) {
    return;
  }
  const sessionId = beginDictationSession();
  const input = document.getElementById('chatBoxInput');
  dictationState.liveBase = {
    value: input?.value || '',
    start: input?.selectionStart || 0,
    end: input?.selectionEnd || 0,
  };
  dictationState.usesLiveTranscription = true;
  if (input) {
    // A stable selection is required while partial results are revised. The
    // textarea remains visible and readable, but typing resumes after stop,
    // cancellation, or any error.
    input.readOnly = true;
  }
  let errorHandled = false;
  let liveCaptureStarted = false;

  const handleError = (error) => {
    // An obsolete live session can still fail after a replacement session has
    // taken ownership. Always release the shared textarea before applying the
    // session-scoped early return so stale callbacks cannot leave it locked.
    if (input) {
      input.readOnly = false;
    }
    if (!isActiveDictationSession(sessionId) || errorHandled) return;
    errorHandled = true;
    const shouldFallbackToFile = Boolean(
      !liveCaptureStarted
      && isCompletedFileTranscriptionSupported()
      && window.LiveTranscription?.shouldFallbackToFile?.(error)
    );
    console.error('Live transcription error:', error);
    applyChatBoxLiveTranscript('');
    resetLiveDictationUi(sessionId);
    if (shouldFallbackToFile) {
      // Live start failed before any PCM was accepted. Start a fresh
      // completed-file recording so the configured fallback is real rather
      // than only applying to browsers without live API support.
      void startDictationRecording();
      return;
    }
    if (error?.isDictationRateLimit && typeof notifyWarning === 'function') {
      notifyWarning(getChatI18nString(
        'us_rate_limits_feature_exceeded_notice',
        'Minute limit reached. Try again after the reset time.',
      ));
    } else if (error?.isDictationInProgress && typeof notifyWarning === 'function') {
      notifyWarning(getChatI18nString(
        'chat_live_transcription_in_progress',
        'Another live transcription is already active. Stop it or wait a moment, then try again.',
      ));
    } else if (error?.isProviderRateLimit && typeof notifyWarning === 'function') {
      notifyWarning(getChatI18nString(
        'chat_live_transcription_provider_rate_limited',
        'The transcription provider is temporarily limiting requests. Please try again shortly.',
      ));
    } else if (typeof notifyError === 'function') {
      if (error?.name === 'NotAllowedError') {
        notifyError(getChatI18nString(
          'chat_dictation_microphone_access_denied',
          'Microphone access denied. Please allow microphone access to use voice input.',
        ));
      } else if (error?.name === 'NotFoundError') {
        notifyError(getChatI18nString(
          'chat_dictation_no_microphone_found',
          'No microphone found. Please connect a microphone to use voice input.',
        ));
      } else {
        notifyError(getChatI18nString(
          'chat_live_transcription_failed',
          'Live transcription failed. Please try again.',
        ));
      }
    }
  };

  try {
    const { stream } = await window.LiveTranscription.start({
      onPartial: (text) => {
        if (!isActiveDictationSession(sessionId) || !text) return;
        applyChatBoxLiveTranscript(text);
      },
      onFinal: (text) => {
        if (!isActiveDictationSession(sessionId)) return;
        const transcript = String(text || '').trim();
        if (transcript) {
          applyChatBoxLiveTranscript(transcript);
          notifySuccess?.(getChatI18nString(
            'chat_dictation_transcription_complete',
            'Transcription complete.',
          ));
        } else {
          notifyWarning?.(getChatI18nString(
            'chat_dictation_no_speech_detected',
            'No speech detected in the recording.',
          ));
        }
        resetLiveDictationUi(sessionId);
      },
      onError: handleError,
      onLimit: () => {
        notifyWarning?.(getChatI18nString(
          'us_rate_limits_feature_exceeded_notice',
          'Minute limit reached. Try again after the reset time.',
        ));
      },
      onStateChange: (state) => {
        if (!isActiveDictationSession(sessionId)) return;
        if (state === 'transcribing') {
          dictationState.isRecording = false;
          dictationState.isTranscribing = true;
          setDictationButtonState('transcribing');
          setInputTranscribingState(true);
        }
      },
    });
    if (!isActiveDictationSession(sessionId)) {
      window.LiveTranscription.cancel();
      return;
    }
    dictationState.stream = stream;
    liveCaptureStarted = true;
    dictationState.isRecording = true;
    dictationState.isTranscribing = false;
    setDictationButtonState('recording');
    setInputTranscribingState(false);
  } catch (error) {
    handleError(error);
  }
}

function stopDictationRecording() {
  if (!dictationState.isRecording) return;

  dictationState.isRecording = false;
  dictationState.discardRecordingOnStop = false;
  stopDictationAudioTracking();

  if (dictationState.usesLiveTranscription) {
    dictationState.isTranscribing = true;
    setDictationButtonState('transcribing');
    setInputTranscribingState(true);
    window.LiveTranscription.stop().catch(() => {
      // The controller invokes the registered error callback with translated
      // UI handling, so this promise rejection needs no second notification.
    });
    return;
  }

  if (dictationState.mediaRecorder && dictationState.mediaRecorder.state !== 'inactive') {
    dictationState.mediaRecorder.stop();
  }

  // Set to transcribing state (will be set to idle after transcription)
  setDictationButtonState('transcribing');
  setInputTranscribingState(true);
  startDictationVisualizerLoop();
}

function cancelTranscription({ notify = true } = {}) {
  const wasRecording = dictationState.isRecording;
  const sessionId = dictationState.sessionId;

  if (dictationState.usesLiveTranscription) {
    window.LiveTranscription?.cancel?.();
    applyChatBoxLiveTranscript('');
    resetLiveDictationUi(sessionId);
    if (notify && typeof notifyInfo === 'function') {
      notifyInfo(getChatI18nString(
        'chat_meeting_transcription_cancelled',
        'Transcription cancelled.',
      ));
    }
    return;
  }

  if (dictationState.isRecording) {
    dictationState.discardRecordingOnStop = true;
    if (dictationState.mediaRecorder && dictationState.mediaRecorder.state !== 'inactive') {
      dictationState.mediaRecorder.stop();
    }
    if (dictationState.stream) {
      dictationState.stream.getTracks().forEach((track) => track.stop());
      dictationState.stream = null;
    }
    dictationState.isRecording = false;
  }

  if (dictationState.abortController) {
    dictationState.abortController.abort();
    dictationState.abortController = null;
  }

  dictationState.mediaRecorder = null;
  dictationState.audioChunks = [];
  dictationState.lastAudioLevel = 0;
  dictationState.discardRecordingOnStop = wasRecording;
  dictationState.isTranscribing = false;
  clearDictationSession(sessionId);
  stopDictationAudioTracking();
  cancelDictationVisualizerLoop();
  paintDictationBars(0);
  setDictationButtonState('idle');
  setInputTranscribingState(false);

  if (notify && typeof notifyInfo === 'function') {
    notifyInfo(getChatI18nString('chat_meeting_transcription_cancelled', 'Transcription cancelled.'));
  }
}

async function transcribeAudio(audioBlob, sessionId = dictationState.sessionId, durationSeconds = null) {
  if (dictationState.isTranscribing || !isActiveDictationSession(sessionId)) return;

  dictationState.isTranscribing = true;
  const abortController = new AbortController();
  dictationState.abortController = abortController;
  setInputTranscribingState(true);
  startDictationVisualizerLoop();

  try {
    // Determine file extension from mime type
    let extension = 'webm';
    const mimeType = audioBlob.type;
    if (mimeType.includes('mp4') || mimeType.includes('m4a')) {
      extension = 'mp4';
    } else if (mimeType.includes('webm')) {
      extension = 'webm';
    } else if (mimeType.includes('mp3') || mimeType.includes('mpeg')) {
      extension = 'mp3';
    } else if (mimeType.includes('wav')) {
      extension = 'wav';
    }

    const formData = new FormData();
    formData.append('audio', audioBlob, `recording.${extension}`);
    if (Number.isFinite(Number(durationSeconds)) && Number(durationSeconds) > 0) {
      formData.append('duration_seconds', String(Number(durationSeconds)));
    }

    const response = await window.authedFetch('/api/v1/llm/transcribe', {
      method: 'POST',
      body: formData,
      signal: abortController.signal,
    });

    if (!isActiveDictationSession(sessionId)) {
      return;
    }

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      const errorMessage = formatChatTranscriptionErrorMessage(
        errorData,
        getChatI18nString('chat_dictation_transcription_failed', 'Transcription failed. Please try again.'),
        response.status,
      );
      const requestError = new Error(errorMessage);
      const limitClassification = (
        typeof omlorixClassifyTranscriptionLimit === 'function'
          ? omlorixClassifyTranscriptionLimit(errorData)
          : {
              isDictationInProgress: (
                errorData?.detail?.code === 'user_dictation_in_progress'
                || (
                  errorData?.detail?.code === 'user_dictation_rate_limited'
                  && errorData?.detail?.reason === 'active_reservation'
                )
              ),
              isDictationRateLimit: (
                errorData?.detail?.code === 'user_dictation_rate_limited'
                && errorData?.detail?.reason !== 'active_reservation'
              ),
            }
      );
      requestError.isDictationInProgress = limitClassification.isDictationInProgress;
      requestError.isDictationRateLimit = limitClassification.isDictationRateLimit;
      if (requestError.isDictationInProgress) {
        requestError.message = getChatI18nString(
          'chat_live_transcription_in_progress',
          'Another live transcription is already active. Stop it or wait a moment, then try again.',
        );
      } else if (requestError.isDictationRateLimit) {
        requestError.message = getChatI18nString(
          'us_rate_limits_feature_exceeded_notice',
          'Minute limit reached. Try again after the reset time.',
        );
      }
      throw requestError;
    }

    const data = await response.json();
    if (!isActiveDictationSession(sessionId)) {
      return;
    }
    const transcribedText = data.text || '';

    if (transcribedText.trim()) {
      insertTextAtCursor(transcribedText);
      if (typeof notifySuccess === 'function') {
        notifySuccess(getChatI18nString('chat_dictation_transcription_complete', 'Transcription complete.'));
      }
    } else {
      if (typeof notifyWarning === 'function') {
        notifyWarning(getChatI18nString('chat_dictation_no_speech_detected', 'No speech detected in the recording.'));
      }
    }

  } catch (error) {
    if (error.name === 'AbortError') {
      // Transcription was cancelled - already handled
      return;
    }
    if (!isActiveDictationSession(sessionId)) {
      return;
    }
	    console.error('Transcription error:', error);
	    if (error?.isDictationRateLimit && typeof notifyWarning === 'function') {
	      notifyWarning(error.message);
	    } else if (error?.isDictationInProgress && typeof notifyWarning === 'function') {
	      notifyWarning(error.message);
	    } else if (typeof notifyError === 'function') {
	      const transcriptionError = getChatI18nString('chat_dictation_transcription_failed', 'Transcription failed. Please try again.');
	      const errorMessage = String(error?.message || '').trim();
	      notifyError(errorMessage ? `${transcriptionError} (${errorMessage})` : transcriptionError);
	    }
  } finally {
    if (dictationState.abortController === abortController) {
      dictationState.abortController = null;
    }
    if (isActiveDictationSession(sessionId)) {
      dictationState.isTranscribing = false;
      dictationState.lastAudioLevel = 0;
      cancelDictationVisualizerLoop();
      paintDictationBars(0);
      setDictationButtonState('idle');
      setInputTranscribingState(false);
      clearDictationSession(sessionId);
    }
  }
}

function insertTextAtCursor(text) {
  const input = document.getElementById('chatBoxInput');
  if (!input) return;

  const start = input.selectionStart || 0;
  const end = input.selectionEnd || 0;
  const currentValue = input.value;

  // Add space before if needed (not at start, and previous char is not whitespace)
  let insertText = text;
  if (start > 0 && currentValue[start - 1] && !/\s/.test(currentValue[start - 1])) {
    insertText = ' ' + insertText;
  }

  // Insert text at cursor position
  const newValue = currentValue.slice(0, start) + insertText + currentValue.slice(end);
  input.value = newValue;

  // Update cursor position
  const newCursorPos = start + insertText.length;
  input.setSelectionRange(newCursorPos, newCursorPos);

  // Trigger input event to update UI
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();

  // Update draft
  if (typeof writeChatInputDraft === 'function') {
    writeChatInputDraft(input.value);
  }
}

function isQuickScreenCaptureSupported() {
  return Boolean(
    typeof window !== 'undefined' &&
    window.isSecureContext !== false &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getDisplayMedia === 'function' &&
    typeof window.File === 'function'
  );
}

function updateQuickScreenCaptureButtonVisibility() {
  if (!chatBoxQuickScreenCaptureButton) return;
  const container = chatBoxQuickScreenCaptureButton.closest('.chatbox-attachment-item, .select-dropdown-item');
  const supported = isQuickScreenCaptureSupported();
  if (container) {
    container.style.display = supported ? '' : 'none';
  } else {
    chatBoxQuickScreenCaptureButton.style.display = supported ? 'flex' : 'none';
  }
}

function buildScreenCaptureFilename() {
  const now = new Date();
  const part = (value) => String(value).padStart(2, '0');
  const stamp = `${now.getFullYear()}${part(now.getMonth() + 1)}${part(now.getDate())}-${part(now.getHours())}${part(now.getMinutes())}${part(now.getSeconds())}`;
  return `screen-capture-${stamp}.png`;
}

async function captureScreenAsFile() {
  if (!isQuickScreenCaptureSupported()) {
    const message = getChatI18nString('chat_screen_capture_not_supported', 'Screen capture is not supported in this browser.');
    throw new Error(message);
  }

  let stream = null;
  let video = null;
  const stopCapture = () => {
    if (video) {
      try {
        video.pause();
      } catch (_) {}
      try {
        video.srcObject = null;
      } catch (_) {}
      video = null;
    }
    if (stream) {
      stream.getTracks().forEach((track) => {
        try {
          track.stop();
        } catch (_) {}
      });
      stream = null;
    }
  };

  try {
    stream = await navigator.mediaDevices.getDisplayMedia({
      video: {
        cursor: 'always',
      },
      audio: false,
    });

    const [videoTrack] = stream.getVideoTracks();
    if (!videoTrack) {
      throw new Error(getChatI18nString('chat_screen_capture_no_video_track', 'No video track available.'));
    }

    video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;

    await new Promise((resolve, reject) => {
      let settled = false;
      let readyTimeoutId = null;
      const cleanup = () => {
        video.removeEventListener('loadeddata', onReady);
        video.removeEventListener('error', onError);
        videoTrack.removeEventListener?.('ended', onEnded);
        if (readyTimeoutId !== null) {
          clearTimeout(readyTimeoutId);
          readyTimeoutId = null;
        }
      };
      const settle = (callback, value) => {
        if (settled) return;
        settled = true;
        cleanup();
        callback(value);
      };
      const onReady = () => {
        settle(resolve);
      };
      const onError = (cause = null) => {
        const error = cause && typeof cause.message === 'string'
          ? cause
          : new Error(getChatI18nString('chat_screen_capture_read_failed', 'Failed to read captured stream.'));
        settle(reject, error);
      };
      const onEnded = () => {
        const error = new Error(getChatI18nString('chat_screen_capture_cancelled', 'Screen capture cancelled.'));
        error.name = 'AbortError';
        onError(error);
      };

      try {
        video.addEventListener('loadeddata', onReady, { once: true });
        video.addEventListener('error', onError, { once: true });
        videoTrack.addEventListener?.('ended', onEnded, { once: true });
        readyTimeoutId = setTimeout(onError, 10000);
        video.srcObject = stream;

        const playPromise = video.play();
        if (playPromise && typeof playPromise.catch === 'function') {
          void playPromise.catch(onError);
        }
      } catch (error) {
        onError(error);
      }

      if (!settled) {
        if (videoTrack.readyState === 'ended') {
          onEnded();
        } else if (video.readyState >= 2) {
          onReady();
        }
      }
    });

    const settings = typeof videoTrack.getSettings === 'function' ? videoTrack.getSettings() : {};
    const width = Math.max(1, Number(settings.width) || video.videoWidth || 0);
    const height = Math.max(1, Number(settings.height) || video.videoHeight || 0);
    if (!width || !height) {
      throw new Error(getChatI18nString('chat_screen_capture_dimensions_failed', 'Unable to determine capture dimensions.'));
    }

    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) {
      throw new Error(getChatI18nString('chat_screen_capture_canvas_failed', 'Unable to prepare capture canvas.'));
    }
    context.drawImage(video, 0, 0, width, height);

    // The canvas owns the captured pixels now; release display access before
    // asynchronous PNG encoding so background work cannot extend screen sharing.
    stopCapture();

    const blob = await new Promise((resolve, reject) => {
      canvas.toBlob((result) => {
        if (result) {
          resolve(result);
        } else {
          reject(new Error(getChatI18nString('chat_screen_capture_file_failed', 'Failed to create screenshot file.')));
        }
      }, 'image/png');
    });

    return new File([blob], buildScreenCaptureFilename(), {
      type: 'image/png',
      lastModified: Date.now(),
    });
  } finally {
    stopCapture();
  }
}

async function handleQuickScreenCaptureClick({ attachmentTarget = null } = {}) {
  toggleChatBoxFilesDropdown(false);

  const uploadHandler = attachmentTarget && typeof attachmentTarget.handleFileSelection === 'function'
    ? attachmentTarget.handleFileSelection
    : window.handleChatFileSelection;

  if (typeof uploadHandler !== 'function') {
    notifyError?.(getChatI18nString('chat_file_uploads_unavailable', 'File uploads are currently unavailable.'));
    return;
  }

  try {
    const screenshotFile = await captureScreenAsFile();

    if (!isFileSupportedForCurrentModel(screenshotFile)) {
      const warningCopy = getChatI18nString('chat_screen_capture_format_not_supported', 'Screen capture was created, but this model does not support PNG files.');
      notifyWarning?.(warningCopy);
    }

    await uploadHandler([screenshotFile]);
  } catch (error) {
    const name = String(error?.name || '');
    const message = String(error?.message || '').toLowerCase();
    const cancelled = name === 'AbortError' || name === 'NotAllowedError' || message.includes('cancel') || message.includes('permission denied');
    if (cancelled) {
      const infoCopy = getChatI18nString('chat_screen_capture_cancelled', 'Screen capture cancelled.');
      notifyInfo?.(infoCopy);
      return;
    }

    const fallback = getChatI18nString('chat_screen_capture_failed', 'Unable to capture your screen right now. Please try again.');
    notifyError?.(error?.message || fallback);
  }
}

function handleDictationButtonClick() {
  if (isRealtimeCallActive() || isRealtimeCallConnecting()) {
    return;
  }

  if (dictationState.isTranscribing) {
    cancelTranscription();
    return;
  }

  if (dictationState.isRecording) {
    stopDictationRecording();
    return;
  }

  const canUseLive = Boolean(
    window.LiveTranscription?.isReady?.()
    && window.LiveTranscription?.isSupported?.()
  );
  if (canUseLive) {
    startLiveDictationRecording();
    return;
  }
  if (isCompletedFileTranscriptionReady()) {
    startDictationRecording();
  }
}

// Initialize dictation button listener
function initDictationFeature() {
  const voiceButton = getDictationVoiceButton();
  if (!voiceButton || voiceButton.dataset.dictationFeatureBound === 'true') return;

  ensureDictationBars();
  paintDictationBars(0);

  voiceButton.addEventListener('click', (e) => {
    e.preventDefault();
    handleDictationButtonClick();
  });
  voiceButton.dataset.dictationFeatureBound = 'true';
}

if (realtimeMuteButton) {
  realtimeMuteButton.addEventListener('click', (event) => {
    event.preventDefault();
    if (!window.realtimeCall || typeof window.realtimeCall.toggleMute !== 'function') {
      notifyError?.(getChatI18nString('chat_realtime_controller_unavailable', 'Realtime call controller is not initialized.'));
      return;
    }
    if (!isRealtimeCallActive()) {
      return;
    }
    window.realtimeCall.toggleMute();
    updateRealtimeMuteButtonUi();
  });
}

function isCompletedFileTranscriptionReady() {
  if (typeof window.chatSetup?.file_transcription_ready === 'boolean') {
    return window.chatSetup.file_transcription_ready;
  }
  try {
    const stored = localStorage.getItem('file_transcription_ready');
    // Preserve startup behavior while the first setup request is still in
    // flight; the setup-ready event immediately reapplies the authoritative
    // backend value.
    return stored === null ? true : stored === 'true';
  } catch (_) {
    return true;
  }
}

function isCompletedFileTranscriptionSupported() {
  return Boolean(
    isCompletedFileTranscriptionReady()
    && navigator.mediaDevices
    && navigator.mediaDevices.getUserMedia
    && window.MediaRecorder
  );
}

// Check whether at least one configured dictation transport is usable.
function isDictationSupported() {
  return isCompletedFileTranscriptionSupported() || Boolean(
    window.LiveTranscription?.isReady?.()
    && window.LiveTranscription?.isSupported?.()
  );
}

function updateDictationButtonVisibility() {
  const realtimeInProgress = isRealtimeCallActive() || isRealtimeCallConnecting();
  const supported = isDictationSupported();

  microphoneButtons.forEach((button) => {
    if (!button) return;
    const featureEnabled = button.dataset.featureEnabled === 'true';
    const visible = featureEnabled && supported && !realtimeInProgress;
    button.style.display = visible ? 'flex' : 'none';
    button.disabled = !supported || realtimeInProgress;
  });
}

function handleRealtimeDictationStateChange() {
  if (
    (isRealtimeCallActive() || isRealtimeCallConnecting()) &&
    (dictationState.isRecording || dictationState.isTranscribing)
  ) {
    cancelTranscription({ notify: false });
  }
  toggleInputButtons();
}

// Bind behavior independently of the cached capability snapshot. The async
// setup response may enable dictation after this script executes.
initDictationFeature();
if (!isDictationSupported()) {
  // Hide voice button if not supported
  const voiceButton = getDictationVoiceButton();
  if (voiceButton) {
    voiceButton.style.display = 'none';
  }
}

updateQuickScreenCaptureButtonVisibility();

// Expose for external use
if (typeof window !== 'undefined') {
  window.dictationState = dictationState;
  window.startDictationRecording = startDictationRecording;
  window.stopDictationRecording = stopDictationRecording;
  window.cancelTranscription = cancelTranscription;
  window.handleDictationButtonClick = handleDictationButtonClick;
}
