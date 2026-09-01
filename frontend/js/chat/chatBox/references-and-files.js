const selectedReferenceParts = [];

const selectedSkillIds = new Set();
const skillMetadataMap = new Map();

const selectedNoteIds = new Set();
const noteMetadataMap = new Map();

const selectedPromptIds = new Set();
const promptMetadataMap = new Map();
const selectedMcpServerIds = new Set();
const mcpConnectorMetadataMap = new Map();
const CHAT_MENTION_PAGE_LIMIT = 50;
const LARGE_PASTE_CHAR_THRESHOLD = 1500;
let deferNextChatInputExpensiveInputWork = false;

function isChatInputDeferredInputWorkActive() {
  return Boolean(deferNextChatInputExpensiveInputWork);
}

function unwrapMentionPage(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.automations)) return payload.automations;
  return [];
}

const selectedChatReferenceIds = new Set();
const chatReferenceMetadataMap = new Map();
var chatTitleUtils = window.ChatTitleUtils || {};
const cachedChatReferenceCandidates = {
  items: [],
  lastFetched: 0,
  projectId: '',
  limit: 0,
  total: 0,
  hasMore: false,
};
const chatReferenceRequestState = {
  quickpick: 0,
};
const chatReferenceQuickpickState = {
  query: '',
  items: [],
  total: 0,
  hasMore: false,
  loading: false,
};

function formatFileSizeLabel(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  if (bytes >= 1024 * 1024 * 1024) {
      return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  }
  if (bytes >= 1024 * 1024) {
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (bytes >= 1024) {
      return `${Math.round(bytes / 1024)} KB`;
  }
  return `${Math.round(bytes)} B`;
}

const CHAT_FILE_MIME_EXTENSION_LABELS = {
  'application/pdf': 'PDF',
  'application/msword': 'DOC',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
  'application/vnd.ms-excel': 'XLS',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'XLSX',
  'application/vnd.ms-powerpoint': 'PPT',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'PPTX',
  'text/plain': 'TXT',
  'text/markdown': 'MD',
  'text/csv': 'CSV',
  'application/json': 'JSON',
  'text/html': 'HTML',
  'text/css': 'CSS',
  'application/xml': 'XML',
  'text/xml': 'XML',
  'application/zip': 'ZIP',
};

function getFileExtensionFromName(filename) {
  const value = String(filename || '').trim();
  const dotIndex = value.lastIndexOf('.');
  if (dotIndex > -1 && dotIndex < value.length - 1) {
    return value.slice(dotIndex + 1).toUpperCase();
  }
  return '';
}

function getFileFormatLabelFromMimeType(mimeType) {
  const type = String(mimeType || '').trim().toLowerCase();
  if (!type) return '';
  if (CHAT_FILE_MIME_EXTENSION_LABELS[type]) {
    return CHAT_FILE_MIME_EXTENSION_LABELS[type];
  }
  if (type.includes('/')) {
    const label = type.split('/').pop();
    if (label) {
      return label.toUpperCase();
    }
  }
  return '';
}

function getFileFormatLabel(file) {
  const explicitLabel = String(
    file?.extension
    || file?.format_label
    || file?.meta?.extension
    || file?.meta?.format_label
    || ''
  ).trim();
  if (explicitLabel) {
    return explicitLabel.toUpperCase();
  }

  const filename = String(file?.meta?.original_filename || file?.original_name || file?.name || '');
  const extension = getFileExtensionFromName(filename);
  if (extension) {
    return extension;
  }

  return getFileFormatLabelFromMimeType(file?.file_type || file?.mime_type || file?.meta?.file_type || file?.meta?.mime_type);
}

function getFileTimestampValue(file) {
  const raw = file?.created_at || file?.meta?.created_at;
  if (raw == null) {
    return 0;
  }

  const coerceEpochMs = (value) => {
    if (!Number.isFinite(value)) {
      return 0;
    }
    return value > 1e12 ? value : value * 1000;
  };

  if (typeof raw === 'number') {
    return coerceEpochMs(raw);
  }

  const rawString = String(raw).trim();
  if (!rawString) {
    return 0;
  }

  if (/^\d+$/.test(rawString)) {
    return coerceEpochMs(Number(rawString));
  }

  const isoLike = rawString.includes('T') ? rawString : rawString.replace(' ', 'T');
  const hasTimezone = /([+-]\d{2}:\d{2}|Z)$/i.test(isoLike);
  const normalized = hasTimezone ? isoLike : `${isoLike}Z`;

  let parsed = Date.parse(normalized);
  if (Number.isFinite(parsed)) {
    return parsed;
  }

  parsed = Date.parse(rawString);
  return Number.isFinite(parsed) ? parsed : 0;
}

const CHAT_REFERENCE_DRAG_MIME = 'application/x-omlorix-chat-reference';
const CHAT_REFERENCE_DRAG_WINDOW_STATE_KEY_CHATBOX = '__omlorixActiveChatReferenceDragPayload';
const MAX_CHAT_REFERENCE_ATTACHMENTS = 5;
let chatReferenceUpdatedAtFormatter;

function normalizeChatReferenceId(chatId) {
  if (chatId === null || typeof chatId === 'undefined') {
    return '';
  }
  return String(chatId).trim();
}

function resolveChatReferenceId(chat) {
  return normalizeChatReferenceId(chat?.chat_id ?? chat?.id);
}

function resolveChatReferenceSnippet(chat) {
  return String(chat?.snippet || '').trim();
}

function getChatReferenceTimestampValue(value) {
  if (value == null) {
    return 0;
  }
  if (typeof value === 'number') {
    return value > 1e12 ? value : value * 1000;
  }
  const raw = String(value).trim();
  if (!raw) {
    return 0;
  }
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatChatReferenceUpdatedAtLabel(chat) {
  const timestamp = getChatReferenceTimestampValue(chat?.last_updated_at);
  if (!timestamp) {
    return '';
  }
  if (!chatReferenceUpdatedAtFormatter) {
    try {
      chatReferenceUpdatedAtFormatter = new Intl.DateTimeFormat(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      });
    } catch (_) {
      chatReferenceUpdatedAtFormatter = null;
    }
  }
  const formatted = chatReferenceUpdatedAtFormatter
    ? chatReferenceUpdatedAtFormatter.format(timestamp)
    : new Date(timestamp).toLocaleString();
  return `Updated ${formatted}`;
}

const CHAT_REFERENCE_PICKER_PAGE_LIMIT = 50;
const CHAT_UPLOADED_FILES_PAGE_LIMIT = 50;
const CHAT_UPLOADED_FILES_LOAD_MORE_THRESHOLD = 120;
const CHAT_UPLOADED_FILES_SEARCH_DEBOUNCE_MS = 250;
let quickpickFilesSearchTimer = null;
let quickpickChatsSearchTimer = null;

function getCurrentChatComposerContext() {
  const chatContainerEl = document.getElementById('chatContainer');
  return {
    chatId: String(chatContainerEl?.getAttribute('data-chat-id') || '').trim(),
    projectId: String(chatContainerEl?.getAttribute('data-project-id') || '').trim(),
  };
}

function generateChatInputDraftTempId() {
  const timeSegment = Date.now().toString(36);
  const randomSegment = Math.random().toString(36).slice(2, 8);
  return `temp-${timeSegment}${randomSegment ? `-${randomSegment}` : ''}`;
}

function getChatInputDraftTempId({ createIfMissing = true } = {}) {
  const chatContainerEl = typeof chatContainer !== 'undefined' && chatContainer
    ? chatContainer
    : document.getElementById('chatContainer');
  if (!chatContainerEl) {
    return 'temp-default';
  }

  let tempId = String(chatContainerEl.getAttribute('data-chat-draft-temp-id') || '').trim();
  if (!tempId && createIfMissing) {
    tempId = generateChatInputDraftTempId();
    chatContainerEl.setAttribute('data-chat-draft-temp-id', tempId);
  }
  return tempId;
}

function resetChatInputDraftTempContext() {
  const chatContainerEl = typeof chatContainer !== 'undefined' && chatContainer
    ? chatContainer
    : document.getElementById('chatContainer');
  chatContainerEl?.removeAttribute('data-chat-draft-temp-id');
}

function getCurrentChatInputDraftContext() {
  const { chatId, projectId } = getCurrentChatComposerContext();
  if (chatId) {
    return {
      type: 'chat',
      id: chatId,
      storageKey: `${CHAT_INPUT_STORAGE_KEY_PREFIX}chat:${chatId}`,
    };
  }

  const chatContainerEl = typeof chatContainer !== 'undefined' && chatContainer
    ? chatContainer
    : document.getElementById('chatContainer');
  const isTemporaryChat = chatContainerEl?.getAttribute('data-temp-chat') === 'true';
  if (isTemporaryChat) {
    const tempId = getChatInputDraftTempId({ createIfMissing: true });
    return {
      type: 'temp',
      id: tempId,
      storageKey: `${CHAT_INPUT_STORAGE_KEY_PREFIX}temp:${tempId}`,
    };
  }

  if (projectId) {
    return {
      type: 'project',
      id: projectId,
      storageKey: `${CHAT_INPUT_STORAGE_KEY_PREFIX}project:${projectId}`,
    };
  }

  return {
    type: 'start',
    id: 'default',
    storageKey: `${CHAT_INPUT_STORAGE_KEY_PREFIX}start:default`,
  };
}

function createChatInputDraftStorageEntry(value, updatedAt = Date.now()) {
  const draftValue = String(value || '');
  return JSON.stringify({
    value: draftValue,
    updatedAt,
  });
}

function parseChatInputDraftStorageEntry(stored) {
  if (stored === null || typeof stored === 'undefined') {
    return {
      exists: false,
      value: '',
      updatedAt: Date.now(),
    };
  }

  try {
    const parsed = JSON.parse(stored);
    if (parsed && typeof parsed === 'object') {
      const updatedAt = Number(parsed.updatedAt);
      const value = typeof parsed.value === 'string'
        ? parsed.value
        : '';
      return {
        exists: true,
        value,
        updatedAt: Number.isFinite(updatedAt) && updatedAt > 0 ? updatedAt : Date.now(),
      };
    }
  } catch (_) {}

  return {
    exists: false,
    value: '',
    updatedAt: Date.now(),
  };
}

function isChatInputDraftExpired(entry, now = Date.now()) {
  if (!entry?.exists) {
    return false;
  }
  return now - entry.updatedAt >= CHAT_INPUT_DRAFT_TTL_MS;
}

function removeExpiredChatInputDrafts(now = Date.now()) {
  lastChatInputDraftPruneAt = now;
  try {
    const keys = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (String(key || '').startsWith(CHAT_INPUT_STORAGE_KEY_PREFIX)) {
        keys.push(key);
      }
    }

    keys.forEach((key) => {
      const entry = parseChatInputDraftStorageEntry(localStorage.getItem(key));
      if (isChatInputDraftExpired(entry, now)) {
        localStorage.removeItem(key);
      }
    });
  } catch (_) {
    // Ignore access issues (e.g., privacy mode).
  }
}

function maybeRemoveExpiredChatInputDrafts(now = Date.now()) {
  if (now - lastChatInputDraftPruneAt < CHAT_INPUT_DRAFT_PRUNE_INTERVAL_MS) {
    return;
  }
  lastChatInputDraftPruneAt = now;
  removeExpiredChatInputDrafts(now);
}

function persistChatInputDraftValue(value, storageKey, options = {}) {
  void options;
  if (!storageKey) {
    return;
  }
  try {
    if (String(value || '') === '') {
      localStorage.removeItem(storageKey);
    } else {
      localStorage.setItem(storageKey, createChatInputDraftStorageEntry(value, Date.now()));
    }
    maybeRemoveExpiredChatInputDrafts();
  } catch (_) {
    // Ignore access issues (e.g., privacy mode)
  }
}

function getUploadedFilesSortOptions(sortValue = 'timestamp') {
  if (sortValue === 'size') {
    return { sortField: 'size', sortDirection: 'desc' };
  }
  if (sortValue === 'format') {
    return { sortField: 'type', sortDirection: 'asc' };
  }
  return { sortField: 'created_at', sortDirection: 'desc' };
}

function buildUploadedFilesQueryOptions({ surface = 'quickpick', offset = 0 } = {}) {
  void surface;
  const search = chatBoxFilesQuickpickSearch?.value || '';

  return {
    search,
    folderId: 'all',
    limit: CHAT_UPLOADED_FILES_PAGE_LIMIT,
    offset,
    ...getUploadedFilesSortOptions('timestamp'),
  };
}

function getUploadedFilesQueryKey(options = {}) {
  return JSON.stringify({
    search: String(options.search || '').trim(),
    folderId: String(options.folderId || 'all'),
    sortField: String(options.sortField || 'created_at'),
    sortDirection: String(options.sortDirection || 'desc'),
    limit: Number(options.limit || CHAT_UPLOADED_FILES_PAGE_LIMIT),
  });
}

function normalizeUploadedFilesPage(payload, fallbackOptions = {}) {
  if (Array.isArray(payload)) {
    const limit = Number(fallbackOptions.limit || payload.length || CHAT_UPLOADED_FILES_PAGE_LIMIT);
    const offset = Number(fallbackOptions.offset || 0);
    return {
      items: payload,
      total: offset + payload.length,
      limit,
      offset,
      hasMore: payload.length >= limit,
    };
  }

  const items = Array.isArray(payload?.items) ? payload.items : [];
  const limit = Number(payload?.limit ?? fallbackOptions.limit ?? CHAT_UPLOADED_FILES_PAGE_LIMIT);
  const offset = Number(payload?.offset ?? fallbackOptions.offset ?? 0);
  return {
    items,
    total: Number(payload?.total ?? offset + items.length),
    limit,
    offset,
    hasMore: Boolean(payload?.has_more),
  };
}

async function fetchUploadedFilesPage(options = {}) {
  if (typeof window.fetchFilesPage === 'function') {
    return normalizeUploadedFilesPage(await window.fetchFilesPage(options), options);
  }

  const params = new URLSearchParams();
  const trimmedSearch = String(options.search || '').trim();
  if (trimmedSearch) {
    params.set('search', trimmedSearch);
  }
  const folderId = String(options.folderId || 'all').trim();
  if (folderId && folderId !== 'all') {
    params.set('folder_id', folderId);
  }
  params.set('sort_field', options.sortField || 'created_at');
  params.set('sort_direction', options.sortDirection || 'desc');
  params.set('limit', String(options.limit || CHAT_UPLOADED_FILES_PAGE_LIMIT));
  params.set('offset', String(options.offset || 0));

  const response = await window.authedFetch(`/api/v1/files/workspace?${params.toString()}`, { method: 'GET' });
  if (!response.ok) {
    throw new Error(`Failed to fetch files (${response.status})`);
  }
  return normalizeUploadedFilesPage(await response.json(), options);
}

function mergeUploadedFiles(existing, incoming) {
  const merged = Array.isArray(existing) ? [...existing] : [];
  (Array.isArray(incoming) ? incoming : []).forEach((file) => {
    const fileId = String(file?.file_id ?? file?.id ?? '').trim();
    if (!fileId) return;
    const index = merged.findIndex((entry) => String(entry?.file_id ?? entry?.id ?? '') === fileId);
    if (index >= 0) {
      merged.splice(index, 1, file);
    } else {
      merged.push(file);
    }
  });
  return merged;
}

function getSupportedMimeTypesForCurrentModel() {
  const payload = typeof window !== 'undefined' ? window.modelSupportedFileFormats : null;
  const formats = payload && Array.isArray(payload.supported_file_formats)
    ? payload.supported_file_formats
    : [];
  const supported = new Set();
  formats.forEach((entry) => {
    if (!entry || typeof entry !== 'object') return;
    const fileFormats = Array.isArray(entry.file_formats) ? entry.file_formats : [];
    fileFormats.forEach((mime) => {
      if (typeof mime === 'string' && mime.trim()) {
        supported.add(mime.split(';', 1)[0].trim().toLowerCase());
      }
    });
  });
  return supported;
}

function isFileSupportedForCurrentModel(fileLike) {
  const supported = getSupportedMimeTypesForCurrentModel();
  if (!supported.size) {
    return true;
  }
  // MIME parameters such as charset do not change the underlying media type
  // and are removed by the backend capability checks as well.
  const mime = String(fileLike?.type || fileLike?.file_type || fileLike?.mime_type || fileLike?.meta?.mime_type || '')
    .split(';', 1)[0]
    .trim()
    .toLowerCase();
  if (!mime) {
    return true;
  }
  return supported.has(mime);
}

if (typeof window !== 'undefined') {
  window.isChatFileSupportedForCurrentModel = isFileSupportedForCurrentModel;
}

function updateChatBoxUploadAcceptAttribute() {
  if (!chatBoxFileInput) return;
  chatBoxFileInput.removeAttribute('accept');
}

function compareFilesByName(a, b) {
  const aName = String(a?.meta?.original_filename || a?.name || '').toLowerCase();
  const bName = String(b?.meta?.original_filename || b?.name || '').toLowerCase();
  return aName.localeCompare(bName);
}

function compareFilesByTimestamp(a, b) {
  const diff = getFileTimestampValue(b) - getFileTimestampValue(a);
  if (diff !== 0) return diff;
  return compareFilesByName(a, b);
}

async function loadUploadedFilesForChatBox({ forceRefresh = false, append = false, surface = 'quickpick' } = {}) {
  const now = Date.now();
  const options = buildUploadedFilesQueryOptions({
    surface,
    offset: append ? cachedUploadedFiles.list.length : 0,
  });
  const queryKey = getUploadedFilesQueryKey(options);
  const isFresh = !append
    && !forceRefresh
    && cachedUploadedFiles.list.length
    && cachedUploadedFiles.queryKey === queryKey
    && now - cachedUploadedFiles.lastFetched < 30000;

  const renderWithCurrentList = () => {
    renderQuickpickList(chatBoxFilesQuickpickSearch?.value || '');
  };

  const applyFiles = (files, page = null) => {
    cachedUploadedFiles.error = '';
    chatBoxDisplayedUploadedFiles = Array.isArray(files) ? files : [];
    indexChatUploadedFiles(chatBoxDisplayedUploadedFiles);
    if (page) {
      cachedUploadedFiles.total = page.total;
      cachedUploadedFiles.hasMore = page.hasMore;
    }
    renderWithCurrentList();
  };

  if (isFresh) {
    applyFiles(cachedUploadedFiles.list);
    return;
  }

  if (cachedUploadedFiles.loading && append) {
    return;
  }
  const requestId = cachedUploadedFiles.requestId + 1;
  cachedUploadedFiles.requestId = requestId;
  cachedUploadedFiles.loading = true;
  if (surface === 'quickpick') {
    chatBoxFilesQuickpickScroll?.setAttribute('aria-busy', 'true');
  }
  if (!append) {
    cachedUploadedFiles.list = [];
    chatBoxDisplayedUploadedFiles = [];
    cachedUploadedFiles.hasMore = false;
    cachedUploadedFiles.queryKey = queryKey;
    cachedUploadedFiles.error = '';
  }
  renderWithCurrentList();

  try {
    const page = await fetchUploadedFilesPage(options);
    if (cachedUploadedFiles.requestId !== requestId) {
      return;
    }

    cachedUploadedFiles.list = append
      ? mergeUploadedFiles(cachedUploadedFiles.list, page.items)
      : page.items;
    cachedUploadedFiles.lastFetched = now;
    cachedUploadedFiles.queryKey = queryKey;
    applyFiles(cachedUploadedFiles.list, page);
  } catch (error) {
    console.error('Failed to load uploaded files', error);
    const message = getChatI18nString('files_error_load', 'Failed to load files');
    cachedUploadedFiles.error = message;
    if (append) {
      cachedUploadedFiles.hasMore = false;
    }
  } finally {
    if (cachedUploadedFiles.requestId === requestId) {
      cachedUploadedFiles.loading = false;
      if (surface === 'quickpick') {
        chatBoxFilesQuickpickScroll?.setAttribute('aria-busy', 'false');
      }
      renderWithCurrentList();
      if (surface === 'quickpick') {
        requestAnimationFrame(() => maybeLoadMoreUploadedFiles('quickpick'));
      }
    }
  }
}

function shouldLoadMoreUploadedFiles(scroller) {
  if (!scroller || cachedUploadedFiles.loading || cachedUploadedFiles.error || !cachedUploadedFiles.hasMore) {
    return false;
  }
  if (scroller.clientHeight <= 0) {
    return false;
  }
  return scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - CHAT_UPLOADED_FILES_LOAD_MORE_THRESHOLD;
}

function getUploadedFilesScrollContainer(surface = 'quickpick') {
  void surface;
  return chatBoxFilesQuickpickScroll;
}

function maybeLoadMoreUploadedFiles(surface = 'quickpick') {
  const scroller = getUploadedFilesScrollContainer(surface);
  if (shouldLoadMoreUploadedFiles(scroller)) {
    void loadUploadedFilesForChatBox({ append: true, surface });
  }
}

function applySendButtonMode() {
  if (!sendButton) return;
  const isGeneratingNow = isCurrentSendContextGenerating();
  if (!isGeneratingNow && isChatSendCancellationPending()) {
    setChatSendCancellationPending(false);
  }
  const hasQueueableInput = hasQueueableChatContent();
  applyChatStopButtonMode({ isGeneratingNow, hasQueueableInput });

  const isUploadingState = sendButton.dataset.uploading === 'true';
  if (isUploadingState) {
    return;
  }

  const defaultCopy = getChatI18nString(CHAT_SEND_TOOLTIP_KEY, 'Send message');
  const modelUnavailable = isChatModelSelectionReady() && !hasChatModelForSend();
  const stopCopy = getChatI18nString(CHAT_STOP_TOOLTIP_KEY, 'Stop response');
  const stoppingCopy = getChatI18nString(CHAT_STOPPING_TOOLTIP_KEY, 'Stopping response...');
  const isCancelPending = isGeneratingNow && isChatSendCancellationPending();
  const queueCount = getQueuedMessageCount();
  const queueCopyTemplate = queueCount > 0
    ? getChatI18nString(CHAT_QUEUE_TOOLTIP_KEY, 'Queue message ({count} queued)')
    : getChatI18nString(CHAT_QUEUE_MESSAGE_TOOLTIP_KEY, 'Queue message');
  const queueCopy = queueCount > 0
    ? queueCopyTemplate
      .replace(/\{count\}/g, String(queueCount))
      .replace(/%s/g, String(queueCount))
    : queueCopyTemplate;

  if (isCancelPending) {
    sendButton.dataset.mode = 'stopping';
    sendButton.innerHTML = chatSendStoppingSpinnerMarkup;
    sendButton.title = stoppingCopy;
    sendButton.setAttribute('aria-label', stoppingCopy);
    updateChatSendTooltip(stoppingCopy);
    setChatSendTooltipEnabled(true);
    return;
  }

  if (!isGeneratingNow && modelUnavailable) {
    const unavailableCopy = getChatI18nString('model_select_no_models_available', 'No models available');
    sendButton.dataset.mode = 'send';
    sendButton.innerHTML = __originalSendIconHTML;
    sendButton.title = unavailableCopy;
    sendButton.setAttribute('aria-label', unavailableCopy);
    updateChatSendTooltip(unavailableCopy);
    setChatSendTooltipEnabled(true);
    return;
  }

  if ((isGeneratingNow || queueCount > 0) && hasQueueableInput) {
    sendButton.dataset.mode = 'queue';
    sendButton.innerHTML = chatSendQueueIconMarkup;
    sendButton.title = queueCopy;
    sendButton.setAttribute('aria-label', queueCopy);
    updateChatSendTooltip(queueCopy);
    setChatSendTooltipEnabled(true);
    return;
  }

  if (isGeneratingNow) {
    sendButton.dataset.mode = 'stop';
    if (typeof Icons !== 'undefined' && Icons.stop) {
      sendButton.innerHTML = Icons.stop;
    }
    sendButton.title = stopCopy;
    sendButton.setAttribute('aria-label', stopCopy);
    updateChatSendTooltip(stopCopy);
    setChatSendTooltipEnabled(true);
    return;
  }

  sendButton.dataset.mode = 'send';
  sendButton.innerHTML = __originalSendIconHTML;
  sendButton.title = '';
  sendButton.setAttribute('aria-label', defaultCopy);
  updateChatSendTooltip(defaultCopy);
  setChatSendTooltipEnabled(false);
}

function setChatReferenceSelectionUi(chatId, selected) {
  if (!chatId) return;
  document.querySelectorAll(`[data-chat-reference-id="${chatId}"]`).forEach((element) => {
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

function createChatFilesQuickpickCheckbox(checked = false) {
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.className = 'form-checkbox';
  checkbox.checked = Boolean(checked);
  return checkbox;
}

function createChatReferenceIcon(className = 'inline-chat-reference-element-icon') {
  const iconEl = document.createElement('span');
  iconEl.className = className;
  iconEl.innerHTML = Icons.chatFilesChooseChats;
  return iconEl;
}

function buildChatReferenceMetaLine(chat) {
  const parts = [];
  const messageCount = Number(chat?.message_count || 0);
  if (messageCount > 0) {
    parts.push(messageCount === 1
      ? formatChatI18nString('chat_reference_message_count_one', '{count} message', { count: messageCount })
      : formatChatI18nString('chat_reference_message_count_other', '{count} messages', { count: messageCount }));
  }
  const updated = formatChatReferenceUpdatedAtLabel(chat);
  if (updated) {
    parts.push(updated);
  }
  return parts.join(' · ');
}

function createChatReferenceAttachmentElement(chat) {
  const chatId = resolveChatReferenceId(chat);
  if (!chatId) return null;

  const element = document.createElement('div');
  element.className = 'inline-files-element inline-chat-reference-element';
  element.dataset.chatReferenceId = chatId;

  const iconEl = createChatReferenceIcon('inline-skill-element-icon inline-chat-reference-element-icon');

  const contentEl = document.createElement('div');
  contentEl.className = 'inline-files-element-content';

  const topRow = document.createElement('div');
  topRow.className = 'inline-files-element-content-top';
  const titleEl = document.createElement('p');
  const fallbackChatTitle = getChatI18nString('chat_reference_untitled', 'Untitled chat');
  const inlineChatTitle = chatTitleUtils.getChatDisplayTitle?.(chat, fallbackChatTitle) || fallbackChatTitle;
  titleEl.className = 'chat-title-with-badge';
  if (typeof chatTitleUtils.setChatTitleElement === 'function') {
    chatTitleUtils.setChatTitleElement(titleEl, chat, { fallbackTitle: fallbackChatTitle });
  } else {
    titleEl.textContent = inlineChatTitle;
    titleEl.title = inlineChatTitle;
  }
  topRow.appendChild(titleEl);

  const bottomRow = document.createElement('div');
  bottomRow.className = 'inline-files-element-content-bottom';
  const typeMeta = document.createElement('p');
  typeMeta.textContent = getChatI18nString('chat_attachment_type_chat', 'CHAT');
  bottomRow.appendChild(typeMeta);

  const detailMeta = document.createElement('p');
  detailMeta.className = 'inline-note-snippet';
  detailMeta.textContent = resolveChatReferenceSnippet(chat) || buildChatReferenceMetaLine(chat) || getChatI18nString('chat_reference_attached_context', 'Attached chat context');
  bottomRow.appendChild(detailMeta);

  contentEl.appendChild(topRow);
  contentEl.appendChild(bottomRow);

  const deleteEl = document.createElement('div');
  deleteEl.className = 'inline-files-element-delete';
  deleteEl.setAttribute('role', 'button');
  deleteEl.setAttribute('tabindex', '0');
  deleteEl.setAttribute('aria-label', getChatI18nString('chat_reference_remove_chat_aria', 'Remove chat reference'));
  deleteEl.innerHTML = Icons.close;
  const removeChatReference = () => removeChatReferenceAttachment(chatId);
  deleteEl.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    removeChatReference();
  });
  deleteEl.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      removeChatReference();
    }
  });

  element.appendChild(iconEl);
  element.appendChild(contentEl);
  element.appendChild(deleteEl);
  return element;
}

function addChatReferenceAttachment(chat, { notifyOnDuplicate = false } = {}) {
  const chatId = resolveChatReferenceId(chat);
  if (!chatId) return false;

  const { chatId: currentChatId } = getCurrentChatComposerContext();
  if (currentChatId && chatId === currentChatId) {
    if (typeof notifyWarning === 'function') {
      notifyWarning(getChatI18nString('chat_reference_current_chat_error', 'You cannot attach the current chat as a reference.'));
    }
    return false;
  }

  if (selectedChatReferenceIds.has(chatId)) {
    if (notifyOnDuplicate && typeof notifyWarning === 'function') {
      notifyWarning(getChatI18nString('chat_reference_duplicate_error', 'This chat is already attached.'));
    }
    return false;
  }

  if (selectedChatReferenceIds.size >= MAX_CHAT_REFERENCE_ATTACHMENTS) {
    if (typeof notifyWarning === 'function') {
      notifyWarning(getChatI18nString('chat_reference_attach_limit', 'You can attach up to {count} chats.').replace('{count}', String(MAX_CHAT_REFERENCE_ATTACHMENTS)));
    }
    return false;
  }

  const normalizedChat = {
    chat_id: chatId,
    title: chatTitleUtils.getChatDisplayTitle?.(chat, getChatI18nString('chat_reference_untitled', 'Untitled chat')) || getChatI18nString('chat_reference_untitled', 'Untitled chat'),
    last_updated_at: chat?.last_updated_at || null,
    snippet: resolveChatReferenceSnippet(chat),
    message_count: Number(chat?.message_count || 0) || 0,
    estimated_chars: Number(chat?.estimated_chars || 0) || 0,
    source: chatTitleUtils.isAutomationChat?.(chat) ? 'automation' : chatTitleUtils.getChatSource?.(chat) || '',
  };
  selectedChatReferenceIds.add(chatId);
  chatReferenceMetadataMap.set(chatId, normalizedChat);

  const container = document.getElementById('chatBoxFiles');
  if (container) {
    const existing = container.querySelector(`.inline-chat-reference-element[data-chat-reference-id="${chatId}"]`);
    if (!existing) {
      const element = createChatReferenceAttachmentElement(normalizedChat);
      if (element) {
        container.appendChild(element);
      }
    }
  }

  setChatReferenceSelectionUi(chatId, true);
  updateChatFilesContainerVisibility();
  toggleInputButtons();
  persistCurrentChatInputDraft();
  return true;
}

function removeChatReferenceAttachment(chatId) {
  const normalizedId = normalizeChatReferenceId(chatId);
  if (!normalizedId) return;
  selectedChatReferenceIds.delete(normalizedId);
  chatReferenceMetadataMap.delete(normalizedId);

  const container = document.getElementById('chatBoxFiles');
  if (container) {
    const element = container.querySelector(`.inline-chat-reference-element[data-chat-reference-id="${normalizedId}"]`);
    element?.remove();
  }

  setChatReferenceSelectionUi(normalizedId, false);
  updateChatFilesContainerVisibility();
  toggleInputButtons();
  persistCurrentChatInputDraft();
}

function clearAllChatReferenceAttachments() {
  const container = document.getElementById('chatBoxFiles');
  if (container) {
    container.querySelectorAll('.inline-chat-reference-element').forEach((element) => element.remove());
  }
  selectedChatReferenceIds.clear();
  chatReferenceMetadataMap.clear();
  document.querySelectorAll('[data-chat-reference-id]').forEach((element) => {
    element.classList.remove('is-selected');
    const checkbox = element.querySelector('input[type="checkbox"]');
    if (checkbox) {
      checkbox.checked = false;
    }
  });
  updateChatFilesContainerVisibility();
  toggleInputButtons();
  persistCurrentChatInputDraft();
}

function getSelectedChatReferenceIds() {
  return Array.from(selectedChatReferenceIds);
}

function getSelectedChatReferencePayload() {
    return Array.from(selectedChatReferenceIds).map((chatId) => {
        const untitledChat = getChatI18nString('chat_reference_untitled', 'Untitled chat');
        const chat = chatReferenceMetadataMap.get(chatId) || { chat_id: chatId, title: untitledChat };
        return {
            chat_id: chatId,
            title: chatTitleUtils.getChatDisplayTitle?.(chat, untitledChat) || untitledChat,
            last_updated_at: chat.last_updated_at || null,
            snippet: chat.snippet || '',
            message_count: Number(chat.message_count || 0) || 0,
            estimated_chars: Number(chat.estimated_chars || 0) || 0,
        };
  });
}

async function fetchChatReferenceCandidates({ query = '', offset = 0, limit = 20, forceRefresh = false } = {}) {
  const normalizedQuery = String(query || '').trim();
  const requestedOffset = Math.max(0, Number(offset) || 0);
  const { projectId } = getCurrentChatComposerContext();
  const now = Date.now();
  const requestedLimit = Math.max(1, Math.min(Number(limit) || 20, 50));
  if (
    !forceRefresh &&
    !normalizedQuery &&
    requestedOffset === 0 &&
    cachedChatReferenceCandidates.items.length &&
    cachedChatReferenceCandidates.projectId === projectId &&
    cachedChatReferenceCandidates.limit >= requestedLimit &&
    now - cachedChatReferenceCandidates.lastFetched < 30000
  ) {
    return {
      items: cachedChatReferenceCandidates.items,
      total_count: cachedChatReferenceCandidates.total || cachedChatReferenceCandidates.items.length,
      has_more: cachedChatReferenceCandidates.hasMore,
      offset: 0,
      limit: cachedChatReferenceCandidates.limit,
    };
  }

  const params = new URLSearchParams();
  params.set('limit', String(requestedLimit));
  params.set('offset', String(requestedOffset));
  if (normalizedQuery) {
    params.set('q', normalizedQuery);
  }
  if (projectId) {
    params.set('project_id', projectId);
  }

  const response = await window.authedFetch(`/api/v1/chats/references?${params.toString()}`, {
    method: 'GET',
  });
  if (!response.ok) {
    throw new Error(`Failed to load chats (${response.status})`);
  }
  const result = await response.json().catch(() => ({}));
  const items = Array.isArray(result?.items) ? result.items : [];
  if (!normalizedQuery && requestedOffset === 0) {
    cachedChatReferenceCandidates.items = items;
    cachedChatReferenceCandidates.lastFetched = now;
    cachedChatReferenceCandidates.projectId = projectId;
    cachedChatReferenceCandidates.limit = requestedLimit;
    cachedChatReferenceCandidates.total = Number(result?.total_count || items.length) || items.length;
    cachedChatReferenceCandidates.hasMore = Boolean(result?.has_more);
  }
  return {
    items,
    total_count: Number(result?.total_count || items.length) || items.length,
    has_more: Boolean(result?.has_more),
    offset: Number(result?.offset ?? requestedOffset) || 0,
    limit: Number(result?.limit ?? requestedLimit) || requestedLimit,
  };
}

/** Merge paginated chat results without duplicating a row after refreshes. */
function mergeChatReferenceCandidates(existing, incoming) {
  const byId = new Map();
  [...(Array.isArray(existing) ? existing : []), ...(Array.isArray(incoming) ? incoming : [])].forEach((chat) => {
    const chatId = resolveChatReferenceId(chat);
    if (chatId) byId.set(chatId, chat);
  });
  return Array.from(byId.values());
}

function buildChatReferenceQuickpickItem(chat) {
  const chatId = resolveChatReferenceId(chat);
  if (!chatId) return null;
  const isSelected = selectedChatReferenceIds.has(chatId);
  const item = document.createElement('label');
  item.className = 'chatbox-files-quickpick__item' + (isSelected ? ' is-selected' : '');
  item.dataset.chatReferenceId = chatId;

  const iconWrapper = document.createElement('span');
  iconWrapper.className = 'chatbox-files-quickpick__item-icon';
  iconWrapper.appendChild(createChatReferenceIcon('inline-chat-reference-element-icon'));

  const nameSpan = document.createElement('span');
  nameSpan.className = 'chatbox-files-quickpick__item-name chat-title-with-badge';
  const quickpickFallbackTitle = getChatI18nString('chat_reference_untitled', 'Untitled chat');
  if (typeof chatTitleUtils.setChatTitleElement === 'function') {
    chatTitleUtils.setChatTitleElement(nameSpan, chat, { fallbackTitle: quickpickFallbackTitle });
  } else {
    nameSpan.textContent = chatTitleUtils.getChatDisplayTitle?.(chat, quickpickFallbackTitle) || quickpickFallbackTitle;
  }
  nameSpan.title = resolveChatReferenceSnippet(chat) || buildChatReferenceMetaLine(chat) || nameSpan.textContent;

  const checkbox = createChatFilesQuickpickCheckbox(isSelected);
  item.append(iconWrapper, nameSpan, checkbox);
  checkbox.addEventListener('change', (event) => {
    event.stopPropagation();
    if (checkbox.checked) {
      if (!addChatReferenceAttachment(chat)) {
        checkbox.checked = false;
      }
    } else {
      removeChatReferenceAttachment(chatId);
    }
  });
  return item;
}

function sortChatReferenceQuickpickItems(items) {
  if (!Array.isArray(items) || !items.length) {
    return [];
  }
  const sorted = [...items];
  sorted.sort((a, b) => {
    const diff = getChatReferenceTimestampValue(b?.last_updated_at) - getChatReferenceTimestampValue(a?.last_updated_at);
    if (diff !== 0) {
      return diff;
    }
    const aTitle = String(a?.title || '').toLowerCase();
    const bTitle = String(b?.title || '').toLowerCase();
    return aTitle.localeCompare(bTitle);
  });
  return sorted;
}

function renderChatReferenceQuickpickResults(items, emptyMessage = getChatI18nString('chat_files_quickpick_empty_chats', 'No chats found')) {
  if (!chatBoxChatReferencesQuickpickList) return;
  const { chatId: currentChatId } = getCurrentChatComposerContext();
  const rawItems = Array.isArray(items) ? items : [];
  const matchedItems = sortChatReferenceQuickpickItems(rawItems.filter((item) => {
    const itemId = resolveChatReferenceId(item);
    return itemId && itemId !== currentChatId;
  }));
  chatBoxChatReferencesQuickpickList.innerHTML = '';
  if (!matchedItems.length) {
    if (chatBoxChatReferencesQuickpickEmpty) {
      chatBoxChatReferencesQuickpickEmpty.hidden = false;
      chatBoxChatReferencesQuickpickEmpty.textContent = emptyMessage;
    }
    return;
  }
  if (chatBoxChatReferencesQuickpickEmpty) {
    chatBoxChatReferencesQuickpickEmpty.hidden = true;
  }
  const frag = document.createDocumentFragment();
  matchedItems.forEach((chat) => {
    const item = buildChatReferenceQuickpickItem(chat);
    if (item) {
      frag.appendChild(item);
    }
  });
  chatBoxChatReferencesQuickpickList.appendChild(frag);
}

async function loadChatReferenceQuickpick({ query = '', forceRefresh = false, append = false } = {}) {
  const normalizedQuery = String(query || '').trim();
  if (append && (chatReferenceQuickpickState.loading || !chatReferenceQuickpickState.hasMore)) return;

  const queryChanged = normalizedQuery !== chatReferenceQuickpickState.query;
  if (!append && (forceRefresh || queryChanged)) {
    chatReferenceQuickpickState.items = [];
    chatReferenceQuickpickState.total = 0;
    chatReferenceQuickpickState.hasMore = false;
  }
  chatReferenceQuickpickState.query = normalizedQuery;
  chatReferenceQuickpickState.loading = true;
  const requestId = ++chatReferenceRequestState.quickpick;
  chatBoxChatReferencesQuickpickScroll?.setAttribute('aria-busy', 'true');
  if (!append && !chatReferenceQuickpickState.items.length) {
    chatBoxChatReferencesQuickpickList.replaceChildren();
    if (chatBoxChatReferencesQuickpickEmpty) {
      chatBoxChatReferencesQuickpickEmpty.hidden = false;
      chatBoxChatReferencesQuickpickEmpty.textContent = getChatI18nString('command_palette_loading_chats', 'Searching chats…');
    }
  }
  try {
    const result = await fetchChatReferenceCandidates({
      query: normalizedQuery,
      offset: append ? chatReferenceQuickpickState.items.length : 0,
      forceRefresh,
      limit: CHAT_REFERENCE_PICKER_PAGE_LIMIT,
    });
    if (requestId !== chatReferenceRequestState.quickpick) {
      return;
    }
    chatReferenceQuickpickState.items = append
      ? mergeChatReferenceCandidates(chatReferenceQuickpickState.items, result.items)
      : result.items;
    chatReferenceQuickpickState.total = result.total_count;
    chatReferenceQuickpickState.hasMore = result.has_more;
    renderChatReferenceQuickpickResults(
      chatReferenceQuickpickState.items,
      normalizedQuery
        ? getChatI18nString('chat_files_quickpick_no_matching_chats', 'No matching chats')
        : getChatI18nString('chat_files_quickpick_empty_chats', 'No chats found'),
    );
  } catch (error) {
    if (requestId !== chatReferenceRequestState.quickpick) {
      return;
    }
    console.error('Failed to load chat reference quickpick', error);
    chatReferenceQuickpickState.hasMore = false;
    if (!chatReferenceQuickpickState.items.length) {
      renderChatReferenceQuickpickResults([], getChatI18nString('chat_files_quickpick_failed_chats', 'Failed to load chats'));
    }
  } finally {
    if (requestId === chatReferenceRequestState.quickpick) {
      chatReferenceQuickpickState.loading = false;
      chatBoxChatReferencesQuickpickScroll?.setAttribute('aria-busy', 'false');
      requestAnimationFrame(maybeLoadMoreChatReferenceQuickpick);
    }
  }
}

/** Fetch another chat page when the embedded result region nears its end. */
function maybeLoadMoreChatReferenceQuickpick() {
  const scroller = chatBoxChatReferencesQuickpickScroll;
  if (!scroller
      || chatReferenceQuickpickState.loading
      || !chatReferenceQuickpickState.hasMore
      || scroller.clientHeight <= 0) {
    return;
  }
  if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - CHAT_UPLOADED_FILES_LOAD_MORE_THRESHOLD) {
    void loadChatReferenceQuickpick({ query: chatReferenceQuickpickState.query, append: true });
  }
}

function mergeImportedGoogleDriveFiles(importedFiles) {
  const items = Array.isArray(importedFiles) ? importedFiles : [];
  items.forEach((file) => upsertChatUploadedFileCache(file));
  chatBoxDisplayedUploadedFiles = Array.isArray(cachedUploadedFiles.list) ? cachedUploadedFiles.list : [];
  indexChatUploadedFiles(cachedUploadedFiles.list);
  if (isQuickpickAvailable()) {
    renderQuickpickList(chatBoxFilesQuickpickSearch?.value || '');
  }
}

let googleDriveImportPromise = null;

async function importGoogleDriveFileIds(fileIds, { attachmentTarget = null } = {}) {
  // Google Picker returns opaque Drive ids. Normalize and deduplicate them
  // before sending them to the existing server-side validation/import path.
  const normalizedIds = [...new Set(
    (Array.isArray(fileIds) ? fileIds : [])
      .map((value) => String(value || '').trim())
      .filter(Boolean)
  )];
  if (!normalizedIds.length) {
    return;
  }

  notifyInfo?.(getChatI18nString(
    'chat_files_google_drive_importing_selected',
    'Importing selected Google Drive files...'
  ));

  const response = await window.authedFetch('/api/v1/files/google-drive/import', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ file_ids: normalizedIds }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.message || `Failed to import Google Drive files (${response.status})`);
  }

  const imported = Array.isArray(payload?.imported) ? payload.imported : [];
  const errors = Array.isArray(payload?.errors) ? payload.errors : [];
  mergeImportedGoogleDriveFiles(imported);
  try {
    window.getCachedFilesList?.({ forceRefresh: true })?.catch?.(() => {});
  } catch (_) {}

  // Attach successful imports immediately. No Omlorix Drive browser or second
  // selection state is involved after the native Picker confirms the files.
  const unsupportedImports = imported.filter((file) => !isFileSupportedForCurrentModel(file));
  attachImportedFilesToComposer(imported, attachmentTarget);

  if (imported.length) {
    notifySuccess?.(formatChatI18nString(
      imported.length === 1
        ? 'chat_files_google_drive_import_success_one'
        : 'chat_files_google_drive_import_success_other',
      imported.length === 1
        ? 'Imported {count} Google Drive file.'
        : 'Imported {count} Google Drive files.',
      { count: imported.length }
    ));
  }
  if (unsupportedImports.length) {
    const label = unsupportedImports.map((file) => file?.meta?.original_filename || file?.file_name || file?.id || 'file').slice(0, 6).join(', ');
    const suffix = unsupportedImports.length > 6 ? '…' : '';
    notifyWarning?.(formatChatI18nString(
      'chat_files_google_drive_unsupported',
      'Some imported files are not natively supported by the current model and were attached as file references: {files}{suffix}',
      { files: label, suffix }
    ));
  }
  if (errors.length) {
    const summary = errors.slice(0, 3).map((item) => item?.message || 'Import failed').join(' · ');
    const message = formatChatI18nString(
      'chat_files_google_drive_partial_failure',
      'Some Google Drive files could not be imported: {summary}',
      { summary }
    );
    if (imported.length) {
      notifyWarning?.(message);
    } else {
      notifyError?.(message);
    }
  }
}

async function importGoogleDriveFilesIntoChat({ attachmentTarget = null } = {}) {
  const nativePicker = window.GoogleDrivePicker;
  if (!nativePicker || typeof nativePicker.open !== 'function') {
    notifyError?.(getChatI18nString(
      'chat_files_google_picker_unavailable',
      'Google Picker is unavailable. Reload the page or contact an administrator.'
    ));
    return;
  }

  let selection;
  try {
    selection = await nativePicker.open();
  } catch (error) {
    const errorCode = String(error?.code || '').trim();
    if (errorCode === 'drive_not_connected' || errorCode === 'drive_reauthorization_required') {
      notifyError?.(getChatI18nString(
        'chat_files_reconnect_google_drive_desc',
        'Reconnect Google Drive to browse and import files.'
      ));
      return;
    }
    const pickerErrorTranslations = {
      picker_not_configured: [
        'chat_files_google_picker_not_configured',
        'Google Picker is not configured on this server. Contact an administrator.',
      ],
      picker_api_timeout: [
        'chat_files_google_picker_load_timeout',
        'Google Picker took too long to load. Reload the page and try again.',
      ],
      picker_script_timeout: [
        'chat_files_google_picker_load_timeout',
        'Google Picker took too long to load. Reload the page and try again.',
      ],
      picker_api_unavailable: [
        'chat_files_google_picker_unavailable',
        'Google Picker is unavailable. Reload the page or contact an administrator.',
      ],
      picker_script_failed: [
        'chat_files_google_picker_unavailable',
        'Google Picker is unavailable. Reload the page or contact an administrator.',
      ],
      picker_api_failed: [
        'chat_files_google_picker_unavailable',
        'Google Picker is unavailable. Reload the page or contact an administrator.',
      ],
      picker_session_failed: [
        'chat_files_google_picker_unavailable',
        'Google Picker is unavailable. Reload the page or contact an administrator.',
      ],
      picker_empty_selection: [
        'chat_files_google_picker_unavailable',
        'Google Picker is unavailable. Reload the page or contact an administrator.',
      ],
    };
    const mappedTranslation = pickerErrorTranslations[errorCode];
    notifyError?.(
      (mappedTranslation && getChatI18nString(...mappedTranslation))
      || getChatI18nString(
        'chat_files_google_picker_unavailable',
        'Google Picker is unavailable. Reload the page or contact an administrator.'
      )
      || error?.message
    );
    return;
  }

  const fileIds = Array.isArray(selection?.fileIds) ? selection.fileIds : [];
  if (!fileIds.length) {
    return;
  }

  // Guard the server import separately from Picker visibility. Once the
  // native dialog closes, its result goes straight to the import endpoint.
  if (googleDriveImportPromise) {
    notifyInfo?.(getChatI18nString(
      'chat_files_google_drive_importing_selected',
      'Importing selected Google Drive files...'
    ));
    return googleDriveImportPromise;
  }
  googleDriveImportPromise = importGoogleDriveFileIds(fileIds, { attachmentTarget })
    .catch((error) => {
      notifyError?.(error?.message || getChatI18nString(
        'chat_files_google_drive_import_failed',
        'Failed to import Google Drive files.'
      ));
    })
    .finally(() => {
      googleDriveImportPromise = null;
    });
  return googleDriveImportPromise;
}
