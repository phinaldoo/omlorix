// Message Queue System
// Allows users to queue messages while a generation is in progress

const messageQueue = {
  items: [], // Array of { id, message, timestamp }
  isProcessing: false,
  isPaused: false,
  isPanelOpen: false,
};

// DOM Elements (initialized on DOMContentLoaded)
let queueOverlay = null;
let queueList = null;
let queueMeta = null;
let queueChip = null;
let queueChipToggle = null;
let queueChipCount = null;
let queuePauseButton = null;
let queueSendNextButton = null;
let queueClearButton = null;
let queueInputDropZone = null;
let queueLiveRegion = null;
let pendingQueueFocus = null;
let queueTitleText = null;
let queueEmptyState = null;

// Queue dispatches are owned by their concrete generation IDs. UI helpers such
// as endGenerationUI() are intentionally not used as completion signals: they
// are also called by navigation, split-screen teardown, cancellation feedback,
// and failed preflights. Binding terminal events to this record prevents any of
// those unrelated paths from releasing the current queued send.
let activeQueueDispatch = null;
let queueHandoffScheduled = false;

const QUEUE_ITEM_DRAG_MIME = 'application/x-omlorix-queue-item';
const QUEUE_ITEM_DRAG_TEXT_MIME = 'text/x-omlorix-queue-item';

const queueDragState = {
  sourceIndex: null,
  sourceId: null,
};

function setQueueOverlayVisibility(shouldShow) {
  if (!queueOverlay) return;
  const show = Boolean(shouldShow);
  queueOverlay.style.display = show ? 'flex' : 'none';
  queueOverlay.classList.toggle('open', show);
  queueOverlay.setAttribute('aria-hidden', show ? 'false' : 'true');
}


// Icons
const getQueueIconMarkup = (name) => (typeof Icons !== 'undefined' && Icons?.[name]) || '';
const QUEUE_ICON_SVG = getQueueIconMarkup('queue');
const TRASH_ICON_SVG = getQueueIconMarkup('trash');
const MOVE_UP_ICON_SVG = getQueueIconMarkup('arrow_top');
const MOVE_DOWN_ICON_SVG = getQueueIconMarkup('arrow_down');
const PAUSE_ICON_SVG = getQueueIconMarkup('pause');
const PLAY_ICON_SVG = getQueueIconMarkup('play');
const SEND_NEXT_ICON_SVG = getQueueIconMarkup('send_now');

function getQueueTranslation(key, fallback) {
  if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
    return window.getTranslation(key, fallback);
  }
  return fallback;
}

function formatQueueTranslation(key, fallback, vars) {
  if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
    return window.formatTranslation(key, fallback, vars);
  }
  return String(fallback || key).replace(/\{(\w+)\}/g, (_, token) => {
    const value = vars && Object.prototype.hasOwnProperty.call(vars, token) ? vars[token] : '';
    return value == null ? '' : String(value);
  });
}

function announceQueueMessage(message) {
  if (!queueLiveRegion || !message) {
    return;
  }
  queueLiveRegion.textContent = '';
  const updateAnnouncement = () => {
    if (queueLiveRegion) {
      queueLiveRegion.textContent = message;
    }
  };
  if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
    window.requestAnimationFrame(updateAnnouncement);
    return;
  }
  if (typeof setTimeout === 'function') {
    setTimeout(updateAnnouncement, 0);
    return;
  }
  updateAnnouncement();
}

function setPendingQueueItemFocus(itemId, control = 'edit') {
  pendingQueueFocus = {
    type: 'item',
    itemId: String(itemId || ''),
    control: String(control || 'edit'),
  };
}

function setPendingComposerFocus() {
  pendingQueueFocus = {
    type: 'composer',
  };
}

function focusQueueComposer() {
  const input = ensureQueueInputDropZone();
  if (!input) {
    return;
  }
  if (typeof window.focusChatInput === 'function') {
    window.focusChatInput();
    return;
  }
  if (typeof input.focus === 'function') {
    input.focus();
  }
}

function restorePendingQueueFocus() {
  if (!pendingQueueFocus) {
    return;
  }

  if (pendingQueueFocus.type === 'composer') {
    pendingQueueFocus = null;
    focusQueueComposer();
    return;
  }

  if (!queueList) {
    return;
  }

  const queueItemElements = Array.from(queueList.querySelectorAll('.message-queue-item'));
  const itemElement = queueItemElements.find((element) => element.dataset.queueId === pendingQueueFocus.itemId);
  if (!itemElement) {
    pendingQueueFocus = null;
    return;
  }

  const preferredTargets = [
    pendingQueueFocus.control,
    'edit',
    'move-down',
    'move-up',
    'delete',
  ];

  for (const target of preferredTargets) {
    const control = itemElement.querySelector(`[data-queue-focus-target="${target}"]`);
    if (control && !control.disabled && typeof control.focus === 'function') {
      pendingQueueFocus = null;
      control.focus();
      return;
    }
  }

  pendingQueueFocus = null;
}

function generateQueueItemId() {
  return 'q_' + Date.now() + '_' + Math.random().toString(36).slice(2, 9);
}

function formatQueueTimestamp(timestamp) {
  const value = Number(timestamp);
  if (!Number.isFinite(value)) {
    return '';
  }
  try {
    const formatter = new Intl.DateTimeFormat(undefined, {
      hour: '2-digit',
      minute: '2-digit',
    });
    return formatter.format(value);
  } catch (_) {
    return new Date(value).toLocaleTimeString();
  }
}

function createEmptyComposerSnapshot() {
  return {
    message: '',
    uploadedFiles: [],
    uploadedFileIds: [],
    skills: [],
    notes: [],
    prompts: [],
    chatReferences: [],
    referenceParts: [],
  };
}

function normalizeComposerSnapshot(rawSnapshot, fallbackMessage = '') {
  const hasSnapshot = rawSnapshot && typeof rawSnapshot === 'object';
  const snapshot = hasSnapshot
    ? { ...rawSnapshot }
    : createEmptyComposerSnapshot();

  const resolvedMessage = hasSnapshot && typeof snapshot.message === 'string'
    ? snapshot.message
    : String(fallbackMessage || '');

  return {
    ...createEmptyComposerSnapshot(),
    ...snapshot,
    message: resolvedMessage,
    uploadedFiles: Array.isArray(snapshot.uploadedFiles) ? snapshot.uploadedFiles : [],
    uploadedFileIds: Array.isArray(snapshot.uploadedFileIds) ? snapshot.uploadedFileIds : [],
    skills: Array.isArray(snapshot.skills) ? snapshot.skills : [],
    notes: Array.isArray(snapshot.notes) ? snapshot.notes : [],
    prompts: Array.isArray(snapshot.prompts) ? snapshot.prompts : [],
    chatReferences: Array.isArray(snapshot.chatReferences) ? snapshot.chatReferences : [],
    referenceParts: Array.isArray(snapshot.referenceParts) ? snapshot.referenceParts : [],
  };
}

function captureComposerStateForQueue(rawMessage) {
  if (typeof window.captureChatComposerStateSnapshot === 'function') {
    try {
      const snapshot = window.captureChatComposerStateSnapshot();
      return normalizeComposerSnapshot(snapshot, rawMessage);
    } catch (error) {
      console.error('Failed to capture composer state for queued message', error);
    }
  }
  return normalizeComposerSnapshot(null, rawMessage);
}

function applyComposerStateSnapshot(snapshot, options = {}) {
  const fallbackMessage = typeof options.fallbackMessage === 'string'
    ? options.fallbackMessage
    : '';
  const normalizedSnapshot = normalizeComposerSnapshot(snapshot, fallbackMessage);
  const restoreOptions = { ...(options || {}) };
  if (Object.prototype.hasOwnProperty.call(restoreOptions, 'fallbackMessage')) {
    delete restoreOptions.fallbackMessage;
  }
  if (typeof window.applyChatComposerStateSnapshot === 'function') {
    try {
      return window.applyChatComposerStateSnapshot(normalizedSnapshot, restoreOptions) !== false;
    } catch (error) {
      console.error('Failed to apply chat composer snapshot', error);
    }
  }

  return false;
}

function resolveQueueItemMessage(item) {
  if (!item || typeof item !== 'object') {
    return '';
  }
  const fromSnapshot = typeof item.composerState?.message === 'string'
    ? item.composerState.message
    : '';
  const candidate = fromSnapshot || item.message || '';
  return String(candidate).trim();
}

function queueItemHasNonTextContent(item) {
  const snapshot = item?.composerState;
  if (!snapshot || typeof snapshot !== 'object') {
    return false;
  }

  return [
    'uploadedFiles',
    'uploadedFileIds',
    'skills',
    'notes',
    'prompts',
    'chatReferences',
    'referenceParts',
  ].some((key) => Array.isArray(snapshot[key]) && snapshot[key].length > 0);
}

function isQueueItemSendable(item) {
  return Boolean(resolveQueueItemMessage(item) || queueItemHasNonTextContent(item));
}

function getQueueItemDisplayMessage(item) {
  const message = resolveQueueItemMessage(item);
  if (message) {
    return message;
  }
  return getQueueTranslation('chat_queue_item_no_text', 'No text (attachments/context only)');
}

function findQueueItemIndex(itemId) {
  const normalizedId = String(itemId || '').trim();
  if (!normalizedId) {
    return -1;
  }
  return messageQueue.items.findIndex((item) => item.id === normalizedId);
}

function isQueuePaused() {
  return messageQueue.isPaused === true;
}

// A queue is considered active only while there are pending items left to send.
function hasActiveQueue() {
  return getQueueLength() > 0;
}

function isQueuePanelOpen() {
  return messageQueue.isPanelOpen === true && hasActiveQueue();
}

function setQueuePanelOpen(shouldOpen, { announce = false } = {}) {
  const nextValue = Boolean(shouldOpen) && hasActiveQueue();
  if (messageQueue.isPanelOpen === nextValue) {
    return nextValue;
  }
  messageQueue.isPanelOpen = nextValue;
  setQueueOverlayVisibility(nextValue);
  updateQueueChip();
  if (announce) {
    announceQueueMessage(getQueueTranslation(
      nextValue ? 'chat_queue_panel_opened' : 'chat_queue_panel_closed',
      nextValue ? 'Queue list opened.' : 'Queue list hidden.',
    ));
  }
  return nextValue;
}

function setQueuePaused(shouldPause, { announce = false } = {}) {
  const nextValue = Boolean(shouldPause);
  if (messageQueue.isPaused === nextValue) {
    return nextValue;
  }
  messageQueue.isPaused = nextValue;
  updateQueueUI();
  if (announce) {
    announceQueueMessage(getQueueTranslation(
      nextValue ? 'chat_queue_paused_notice' : 'chat_queue_resumed_notice',
      nextValue ? 'Queue paused.' : 'Queue resumed.',
    ));
  }
  if (!nextValue && !isQueueEmpty() && !isQueueProcessingBlocked()) {
    void processNextQueuedMessage();
  }
  return nextValue;
}

function addToQueue(message, { composerState = null } = {}) {
  const normalizedMessage = String(message || '').trim();
  const normalizedComposerState = normalizeComposerSnapshot(composerState, message);
  const pendingItem = {
    message: normalizedMessage,
    composerState: normalizedComposerState,
  };
  if (!isQueueItemSendable(pendingItem)) {
    return null;
  }

  const item = {
    id: generateQueueItemId(),
    message: normalizedMessage,
    timestamp: Date.now(),
    composerState: normalizedComposerState,
    dispatchContext: captureQueueDispatchContext(),
  };

  messageQueue.items.push(item);
  updateQueueUI();
  return item;
}

function removeFromQueue(itemId) {
  const index = messageQueue.items.findIndex(item => item.id === itemId);
  if (index !== -1) {
    messageQueue.items.splice(index, 1);
    updateQueueUI();
    return true;
  }
  return false;
}

function getNextQueueItem() {
  if (messageQueue.items.length === 0) {
    return null;
  }
  const item = messageQueue.items.shift();
  updateQueueUI();
  return item;
}

function peekNextQueueItem() {
  return messageQueue.items.length > 0 ? messageQueue.items[0] : null;
}

function getQueueLength() {
  return messageQueue.items.length;
}

function reorderMessageQueueItems(fromIndex, toIndex) {
  const items = messageQueue.items;
  const length = items.length;
  if (!Number.isInteger(fromIndex) || !Number.isInteger(toIndex)) {
    return false;
  }
  if (fromIndex < 0 || fromIndex >= length) {
    return false;
  }
  const clampedTarget = Math.max(0, Math.min(toIndex, length));
  if (fromIndex === clampedTarget || fromIndex === clampedTarget - 1) {
    return false;
  }
  const [movedItem] = items.splice(fromIndex, 1);
  const normalizedTarget = fromIndex < clampedTarget ? clampedTarget - 1 : clampedTarget;
  items.splice(normalizedTarget, 0, movedItem);
  updateQueueUI();
  return true;
}

function clearQueue({ announce = false } = {}) {
  messageQueue.items = [];
  updateQueueUI();
  if (announce) {
    announceQueueMessage(getQueueTranslation('chat_queue_cleared', 'Queue cleared.'));
  }
}

function isQueueEmpty() {
  return messageQueue.items.length === 0;
}

function isQueueProcessingBlocked() {
  return Boolean(
    window.isGenerating
    || (typeof window.isInterruptedDraftDispatchPending === 'function' && window.isInterruptedDraftDispatchPending())
  );
}

/**
 * Capture the destination and model state that gave this queue item meaning.
 *
 * A queue is allowed to outlive the response that was active when the item was
 * added, but it must never silently follow navigation into another chat or
 * switch from a split panel to the hidden main transcript.
 */
function captureQueueDispatchContext() {
  const split = window.SplitScreenManager;
  if (split?.active === true) {
    return {
      surface: 'split',
      target: String(split.sendTarget || 'both'),
      leftChatId: String(split.leftChatId || ''),
      rightChatId: String(split.rightChatId || ''),
      leftModelId: String(split.leftModelId || ''),
      rightModelId: String(split.rightModelId || ''),
    };
  }

  const chatContainer = document.getElementById('chatContainer');
  const modelSelect = document.getElementById('modelSelect');
  let customModelSettings = {};
  try {
    customModelSettings = typeof window.getCurrentModelSettingValues === 'function'
      ? window.getCurrentModelSettingValues()
      : {};
  } catch (_) {}

  return {
    surface: 'chat',
    chatId: String(chatContainer?.getAttribute('data-chat-id') || ''),
    originGenerationId: String(chatContainer?.getAttribute('data-active-generation') || ''),
    modelId: String(modelSelect?.getAttribute('data-model-id') || ''),
    customModelSettings: customModelSettings && typeof customModelSettings === 'object'
      ? { ...customModelSettings }
      : {},
    subagentTargets: Array.isArray(window.SubagentTargets?.getSelection?.())
      ? window.SubagentTargets.getSelection().map((target) => ({ ...target }))
      : null,
  };
}

function queueDispatchContextIsCurrent(item) {
  const context = item?.dispatchContext;
  if (!context || typeof context !== 'object') {
    return true;
  }

  const split = window.SplitScreenManager;
  if (context.surface === 'split') {
    return Boolean(
      split?.active === true
      && String(split.leftChatId || '') === String(context.leftChatId || '')
      && String(split.rightChatId || '') === String(context.rightChatId || '')
      && String(split.leftModelId || '') === String(context.leftModelId || '')
      && String(split.rightModelId || '') === String(context.rightModelId || '')
    );
  }

  if (split?.active === true) {
    return false;
  }
  const currentChatId = String(
    document.getElementById('chatContainer')?.getAttribute('data-chat-id') || ''
  );
  return currentChatId === String(context.chatId || '');
}

function beginQueueDispatch(item) {
  const splitTarget = String(item?.dispatchContext?.target || 'both');
  const dispatch = {
    token: Symbol('queue-dispatch'),
    itemId: String(item?.id || ''),
    accepted: false,
    acceptedCount: 0,
    expectedGenerationCount: item?.dispatchContext?.surface === 'split' && splitTarget === 'both' ? 2 : 1,
    sendSettled: false,
    generationIds: new Set(),
    acceptedTargets: new Set(),
  };
  activeQueueDispatch = dispatch;
  messageQueue.isProcessing = true;
  updateQueueUI();
  return dispatch;
}

function releaseQueueDispatch(dispatchToken = null) {
  if (dispatchToken !== null && activeQueueDispatch?.token !== dispatchToken) {
    return false;
  }
  if (activeQueueDispatch === null && !messageQueue.isProcessing) {
    return false;
  }

  activeQueueDispatch = null;
  messageQueue.isProcessing = false;
  updateQueueUI();
  return true;
}

function scheduleNextQueuedMessage() {
  if (queueHandoffScheduled || isQueueEmpty()) {
    return;
  }

  const attemptHandoff = () => {
    queueHandoffScheduled = false;

    if (isQueueEmpty() || isQueuePaused()) {
      return;
    }

    // An active queue dispatch owns the next completion signal. Do not poll
    // while it is generating; its completion hook will schedule the next item.
    if (activeQueueDispatch !== null || messageQueue.isProcessing) {
      return;
    }

    if (isQueueProcessingBlocked()) {
      return;
    }

    void processNextQueuedMessage();
  };

  // Completion is already persisted by the backend before its terminal event
  // is published. A microtask lets the current stream handler unwind without
  // introducing a visible timer gap between queued turns.
  queueHandoffScheduled = true;
  Promise.resolve().then(attemptHandoff);
}

function markQueueDispatchAccepted(dispatchToken, generationId, target = '') {
  if (activeQueueDispatch?.token !== dispatchToken) {
    return false;
  }
  const normalizedGenerationId = String(generationId || '').trim();
  if (normalizedGenerationId && activeQueueDispatch.generationIds.has(normalizedGenerationId)) {
    return true;
  }
  activeQueueDispatch.accepted = true;
  activeQueueDispatch.acceptedCount += 1;
  if (normalizedGenerationId) {
    activeQueueDispatch.generationIds.add(normalizedGenerationId);
  }
  const normalizedTarget = String(target || '').trim().toLowerCase();
  if (normalizedTarget === 'left' || normalizedTarget === 'right') {
    activeQueueDispatch.acceptedTargets.add(normalizedTarget);
  }
  // A split dispatch can produce one accepted request per panel. Keep the
  // original queue item recoverable until every requested panel has accepted
  // it; otherwise a failure on the second panel would silently discard that
  // panel's copy after the first panel accepted successfully.
  if (activeQueueDispatch.acceptedCount >= activeQueueDispatch.expectedGenerationCount) {
    removeFromQueue(activeQueueDispatch.itemId);
  }
  return true;
}

function narrowPartialSplitDispatchForRetry(dispatch) {
  if (
    !dispatch
    || dispatch.acceptedCount <= 0
    || dispatch.acceptedCount >= dispatch.expectedGenerationCount
  ) {
    return false;
  }

  const queuedItem = messageQueue.items.find(item => String(item?.id || '') === dispatch.itemId);
  if (
    queuedItem?.dispatchContext?.surface !== 'split'
    || queuedItem.dispatchContext.target !== 'both'
  ) {
    return false;
  }

  const unacceptedTargets = ['left', 'right'].filter(
    target => !dispatch.acceptedTargets.has(target)
  );
  const splitRetryTarget = unacceptedTargets.length === 1
    ? unacceptedTargets[0]
    : String(window.SplitScreenManager?.sendTarget || '');
  if (splitRetryTarget !== 'left' && splitRetryTarget !== 'right') {
    return false;
  }

  queuedItem.dispatchContext.target = splitRetryTarget;
  updateQueueUI();
  return true;
}

function handleQueueGenerationTerminal(detail = {}) {
  const generationId = String(detail?.generationId || '').trim();
  const status = String(detail?.status || 'finished').trim().toLowerCase();
  const dispatch = activeQueueDispatch;

  // Queue items created during a new chat's first turn start without a durable
  // chat ID. Promote only items tied to this exact originating generation.
  if (!dispatch && generationId) {
    const currentChatId = String(
      document.getElementById('chatContainer')?.getAttribute('data-chat-id') || ''
    );
    if (currentChatId) {
      messageQueue.items.forEach((item) => {
        const context = item?.dispatchContext;
        if (
          context?.surface === 'chat'
          && !context.chatId
          && String(context.originGenerationId || '') === generationId
        ) {
          context.chatId = currentChatId;
        }
      });
    }
  }

  if (dispatch) {
    // A queue request that is still in preflight cannot be completed by an
    // unrelated generation ending elsewhere in the application.
    if (!dispatch.accepted || !generationId || !dispatch.generationIds.has(generationId)) {
      return false;
    }
    dispatch.generationIds.delete(generationId);
    if (dispatch.generationIds.size > 0) {
      return true;
    }
    if (
      dispatch.acceptedCount < dispatch.expectedGenerationCount
      && !dispatch.sendSettled
    ) {
      return true;
    }
    narrowPartialSplitDispatchForRetry(dispatch);
    releaseQueueDispatch(dispatch.token);
  }

  const completedAllTargets = !dispatch
    || dispatch.acceptedCount >= dispatch.expectedGenerationCount;
  if (status === 'finished' && completedAllTargets) {
    scheduleNextQueuedMessage();
  } else if (!isQueueEmpty()) {
    // Stop/error/interruption is a deliberate break in the chain. Keep later
    // messages intact and require an explicit resume so one failure cannot
    // cascade through the rest of the queue.
    setQueuePaused(true, { announce: false });
  }
  return true;
}

// Process next message in queue
async function processNextQueuedMessage(options = {}) {
  const force = Boolean(options && options.force === true);
  if (activeQueueDispatch !== null || messageQueue.isProcessing) {
    return;
  }

  if (!force && isQueuePaused()) {
    return;
  }

  if (isQueueProcessingBlocked()) {
    return;
  }

  const nextItem = peekNextQueueItem();
  if (!nextItem) {
    return;
  }

  const queuedMessage = resolveQueueItemMessage(nextItem);
  if (!isQueueItemSendable(nextItem)) {
    removeFromQueue(nextItem.id);
    scheduleNextQueuedMessage();
    return;
  }

  if (!queueDispatchContextIsCurrent(nextItem)) {
    // Keep the item recoverable instead of sending it into whichever chat or
    // split panel happens to be visible now.
    setQueuePaused(true, { announce: true });
    return;
  }

  // Preserve anything the user has typed or attached since creating the queue
  // item. The queued payload is installed only long enough for the dispatcher
  // to synchronously capture its private attachment/entity state.
  const liveComposerState = captureComposerStateForQueue(
    document.getElementById('chatBoxInput')?.value || ''
  );

  const restored = applyComposerStateSnapshot(nextItem.composerState, {
    focusInput: false,
    dispatchInputEvent: true,
    includeMessage: true,
    fallbackMessage: queuedMessage,
  });

  if (!restored) {
    try {
      const chatInput = document.getElementById('chatBoxInput');
      if (chatInput) {
        chatInput.value = queuedMessage;
        if (typeof window.writeChatInputDraft === 'function') {
          window.writeChatInputDraft(queuedMessage);
        }
        chatInput.dispatchEvent(new Event('input', { bubbles: true }));
      }
    } catch (_) {}
  }
  
  const dispatch = beginQueueDispatch(nextItem);
  let sendPromise = null;
  let liveComposerRestored = false;
  try {
    const dispatchOptions = {
      preserveComposerAfterDispatch: true,
      restoreDraftOnFailure: false,
      modelId: nextItem.dispatchContext?.modelId || null,
      customModelSettings: nextItem.dispatchContext?.customModelSettings || null,
      subagentTargets: Array.isArray(nextItem.dispatchContext?.subagentTargets)
        ? nextItem.dispatchContext.subagentTargets.map((target) => ({ ...target }))
        : null,
      onRequestAccepted: (generationId, target) => {
        markQueueDispatchAccepted(dispatch.token, generationId, target);
      },
    };

    if (nextItem.dispatchContext?.surface === 'split') {
      const split = window.SplitScreenManager;
      if (!split || typeof split.send !== 'function') {
        throw new Error('Split-screen queue dispatcher is unavailable.');
      }
      sendPromise = split.send(queuedMessage, {
        target: nextItem.dispatchContext.target,
        preserveComposerAfterDispatch: true,
        restoreDraftOnFailure: false,
        onRequestAccepted: dispatchOptions.onRequestAccepted,
      });
    } else if (typeof sendMessage === 'function') {
      sendPromise = sendMessage(queuedMessage, false, null, dispatchOptions);
    } else {
      throw new Error('Chat queue dispatcher is unavailable.');
    }

    // Both normal and split dispatchers capture their request context before
    // their first await. Restore the live draft immediately so sent queue text
    // never remains in the composer during the response.
    liveComposerRestored = applyComposerStateSnapshot(liveComposerState, {
      focusInput: false,
      dispatchInputEvent: true,
      includeMessage: true,
    });

    await sendPromise;
  } catch (error) {
    console.error('Failed to send queued message:', error);
  } finally {
    // A synchronous dispatcher failure happens before the immediate restore.
    // Never reapply the old live snapshot after an async stream, because the
    // user may have typed a newer draft while that response was generating.
    if (!liveComposerRestored) {
      applyComposerStateSnapshot(liveComposerState, {
        focusInput: false,
        dispatchInputEvent: true,
        includeMessage: true,
      });
    }

    if (activeQueueDispatch?.token === dispatch.token) {
      dispatch.sendSettled = true;
      if (dispatch.accepted) {
        // Once a request is accepted, only its generation-scoped terminal hook
        // may release it. The request promise can settle before cancellation is
        // acknowledged, and treating that transport close as completion would
        // allow two backend generations to overlap.
        if (
          dispatch.generationIds.size === 0
          && dispatch.acceptedCount >= dispatch.expectedGenerationCount
        ) {
          releaseQueueDispatch(dispatch.token);
          scheduleNextQueuedMessage();
        } else if (dispatch.generationIds.size === 0) {
          // One half of a two-panel fan-out failed before acceptance. Retain
          // the queue item, but narrow its retry destination to the failed
          // panel so the accepted side never receives a duplicate turn.
          narrowPartialSplitDispatchForRetry(dispatch);
          releaseQueueDispatch(dispatch.token);
          if (!isQueueEmpty()) setQueuePaused(true, { announce: false });
        }
      } else {
        // The server never accepted the item. Keep it at the head of the queue
        // and stop automatic processing so a transient failure cannot drain all
        // following messages.
        releaseQueueDispatch(dispatch.token);
        setQueuePaused(true, { announce: false });
      }
    }
  }
}

// Create queue overlay element
function createQueueOverlay() {
  if (queueOverlay) return queueOverlay;
  
  const overlay = document.createElement('div');
  overlay.className = 'message-queue-overlay';
  overlay.id = 'messageQueueOverlay';
  overlay.style.display = 'none';
  overlay.classList.remove('open');
  overlay.setAttribute('aria-hidden', 'true');
  
  const header = document.createElement('div');
  header.className = 'message-queue-header';
  
  const title = document.createElement('div');
  title.className = 'message-queue-title';

  const titleIcon = document.createElement('span');
  titleIcon.className = 'message-queue-title-icon';
  titleIcon.innerHTML = QUEUE_ICON_SVG;
  titleIcon.setAttribute('aria-hidden', 'true');

  const titleText = document.createElement('span');
  titleText.textContent = getQueueTranslation('chat_queue_title', 'Queued Messages');
  queueTitleText = titleText;
  title.appendChild(titleIcon);
  title.appendChild(titleText);

  const meta = document.createElement('span');
  meta.className = 'message-queue-meta';
  meta.id = 'messageQueueMeta';
  meta.textContent = getQueueTranslation('chat_queue_status_idle', 'Idle');
  queueMeta = meta;

  // Keep the heading copy together so long translations can truncate without
  // pushing the queue controls out of the panel on narrow composers.
  const heading = document.createElement('div');
  heading.className = 'message-queue-heading';
  heading.appendChild(title);
  heading.appendChild(meta);

  // Management actions belong in the expanded panel. The collapsed composer
  // control should only summarize the queue, keeping the primary send area calm.
  const headerActions = document.createElement('div');
  headerActions.className = 'message-queue-header-actions';

  const pauseButton = createQueueHeaderActionButton({
    id: 'chatQueuePauseButton',
    className: 'message-queue-header-action--pause',
    label: getQueueTranslation('chat_queue_pause', 'Pause queue'),
    icon: PAUSE_ICON_SVG,
    onClick: () => {
      setQueuePaused(!isQueuePaused(), { announce: true });
    },
  });
  queuePauseButton = pauseButton;

  const sendNextButton = createQueueHeaderActionButton({
    id: 'chatQueueSendNextButton',
    className: 'message-queue-header-action--send-next',
    label: getQueueTranslation('chat_queue_send_next', 'Send next now'),
    icon: SEND_NEXT_ICON_SVG,
    onClick: () => {
      announceQueueMessage(getQueueTranslation('chat_queue_sending_next', 'Sending next queued message.'));
      void processNextQueuedMessage({ force: true });
    },
  });
  queueSendNextButton = sendNextButton;

  const clearButton = createQueueHeaderActionButton({
    id: 'chatQueueClearButton',
    className: 'message-queue-header-action--clear',
    label: getQueueTranslation('chat_queue_clear', 'Clear queue'),
    icon: TRASH_ICON_SVG,
    onClick: () => {
      clearQueue({ announce: true });
    },
  });
  queueClearButton = clearButton;

  headerActions.appendChild(pauseButton);
  headerActions.appendChild(sendNextButton);
  headerActions.appendChild(clearButton);

  header.appendChild(heading);
  header.appendChild(headerActions);
  
  const list = document.createElement('div');
  list.className = 'message-queue-list';
  list.id = 'messageQueueList';
  list.setAttribute('role', 'list');
  list.setAttribute('aria-label', getQueueTranslation('chat_queue_list_aria', 'Queued messages'));
  queueList = list;
  list.addEventListener('dragover', (event) => {
    if (!isQueueItemDragEvent(event)) return;
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'move';
    }
  });
  list.addEventListener('drop', (event) => {
    if (!isQueueItemDragEvent(event)) return;
    event.preventDefault();
    clearQueueDropIndicators();
    const draggedId = getQueueDragEventItemId(event) || queueDragState.sourceId;
    const fromIndex = Number.isInteger(queueDragState.sourceIndex)
      ? queueDragState.sourceIndex
      : findQueueItemIndex(draggedId);
    if (!Number.isInteger(fromIndex) || fromIndex < 0) {
      resetQueueDragState();
      return;
    }
    const toIndex = messageQueue.items.length;
    reorderMessageQueueItems(fromIndex, toIndex);
    resetQueueDragState();
  });
  
  const emptyState = document.createElement('div');
  emptyState.className = 'message-queue-empty';
  emptyState.id = 'messageQueueEmpty';
  emptyState.textContent = getQueueTranslation('chat_queue_empty', 'No messages in queue');
  queueEmptyState = emptyState;

  const liveRegion = document.createElement('div');
  liveRegion.className = 'sr-only';
  liveRegion.id = 'messageQueueLiveRegion';
  liveRegion.setAttribute('aria-live', 'polite');
  liveRegion.setAttribute('aria-atomic', 'true');
  queueLiveRegion = liveRegion;
  
  overlay.appendChild(header);
  overlay.appendChild(list);
  overlay.appendChild(emptyState);
  overlay.appendChild(liveRegion);
  
  queueOverlay = overlay;
  return overlay;
}

function createQueueHeaderActionButton({
  id,
  className = '',
  label,
  icon,
  onClick,
}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.id = id;
  button.className = `message-queue-header-action ${className}`.trim();
  button.setAttribute('aria-label', label);
  button.title = label;
  button.innerHTML = icon;
  button.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (button.disabled || typeof onClick !== 'function') {
      return;
    }
    onClick();
  });
  return button;
}

function createQueueChip() {
  if (queueChip) {
    return queueChip;
  }

  const chip = document.createElement('div');
  chip.className = 'chat-queue-chip';
  chip.id = 'chatQueueChip';
  chip.hidden = true;

  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'chat-queue-chip__toggle';
  toggle.id = 'chatQueueChipToggle';
  toggle.setAttribute('aria-controls', 'messageQueueOverlay');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.addEventListener('click', () => {
    setQueuePanelOpen(!isQueuePanelOpen(), { announce: true });
  });
  queueChipToggle = toggle;

  const icon = document.createElement('span');
  icon.className = 'chat-queue-chip__icon';
  icon.setAttribute('aria-hidden', 'true');
  icon.innerHTML = QUEUE_ICON_SVG;

  const label = document.createElement('span');
  label.className = 'chat-queue-chip__label';
  label.textContent = getQueueTranslation('chat_queue_chip_label', 'Queue');

  const count = document.createElement('span');
  count.className = 'chat-queue-chip__count';
  count.id = 'chatQueueChipCount';
  count.textContent = '0';
  queueChipCount = count;

  toggle.appendChild(icon);
  toggle.appendChild(label);
  toggle.appendChild(count);

  chip.appendChild(toggle);

  queueChip = chip;
  return chip;
}

// Initialize queue UI
function initializeQueueUI() {
  const chatBox = document.getElementById('chatBox');
  if (!chatBox) {
    console.warn('Queue UI: Required elements not found');
    return;
  }
  
  // Create and insert queue overlay above chatBox
  const overlay = createQueueOverlay();
  chatBox.insertBefore(overlay, chatBox.firstChild);

  // Keep the queue summary next to the composer attachment controls.
  const composerActions = chatBox.querySelector('.chat-box-bottom-div');
  if (composerActions) {
    const chip = createQueueChip();
    const sendButtonWrapper = document.getElementById('chatBoxSendButton')?.parentNode || null;
    if (sendButtonWrapper && sendButtonWrapper.parentNode === composerActions) {
      composerActions.insertBefore(chip, sendButtonWrapper);
    } else {
      composerActions.appendChild(chip);
    }
  }

  updateQueueUI();
}

function queueMessageFromInput({ showOverlay = false } = {}) {
  const chatInput = document.getElementById('chatBoxInput');
  if (!chatInput) return false;

  const composerSnapshot = captureComposerStateForQueue(chatInput.value);
  const message = resolveQueueItemMessage({ message: chatInput.value, composerState: composerSnapshot });
  const item = addToQueue(message, { composerState: composerSnapshot });
  if (!item) {
    return false;
  }

  applyComposerStateSnapshot(createEmptyComposerSnapshot(), {
    includeMessage: true,
    focusInput: false,
    dispatchInputEvent: true,
  });

  if (showOverlay) {
    setQueuePanelOpen(true);
  }

  return true;
}

function getQueueDragEventItemId(event) {
  if (!event?.dataTransfer) {
    return '';
  }
  try {
    const mimeValue = event.dataTransfer.getData(QUEUE_ITEM_DRAG_MIME)
      || event.dataTransfer.getData(QUEUE_ITEM_DRAG_TEXT_MIME)
      || event.dataTransfer.getData('text/plain');
    return String(mimeValue || '').trim();
  } catch (_) {
    return '';
  }
}

function isQueueItemDragEvent(event) {
  if (queueDragState.sourceId) {
    return true;
  }
  const types = Array.from(event?.dataTransfer?.types || []).map((type) => String(type || '').toLowerCase());
  const hasKnownType = types.includes(String(QUEUE_ITEM_DRAG_MIME).toLowerCase())
    || types.includes(String(QUEUE_ITEM_DRAG_TEXT_MIME).toLowerCase());
  if (hasKnownType) {
    return true;
  }
  if (types.includes('text/plain')) {
    const draggedId = getQueueDragEventItemId(event);
    return Boolean(draggedId && findQueueItemIndex(draggedId) !== -1);
  }
  return false;
}

function resolveQueueItemFromDragEvent(event) {
  const fallbackId = queueDragState.sourceId ? String(queueDragState.sourceId) : '';
  const draggedId = getQueueDragEventItemId(event) || fallbackId;
  if (!draggedId) {
    return null;
  }
  const index = findQueueItemIndex(draggedId);
  if (index === -1) {
    return null;
  }
  return messageQueue.items[index] || null;
}

function ensureQueueInputDropZone() {
  if (queueInputDropZone) {
    return queueInputDropZone;
  }
  const input = document.getElementById('chatBoxInput');
  if (!input) {
    return null;
  }
  queueInputDropZone = input;
  return input;
}

function restoreQueueItemToComposer(item) {
  if (!item || typeof item !== 'object') {
    return false;
  }
  if (findQueueItemIndex(item.id) === -1) {
    return false;
  }

  const fallbackMessage = resolveQueueItemMessage(item);

  const restored = applyComposerStateSnapshot(item.composerState, {
    includeMessage: true,
    focusInput: true,
    dispatchInputEvent: true,
    fallbackMessage,
  });
  if (restored) {
    removeFromQueue(item.id);
    return true;
  }

  const chatInput = ensureQueueInputDropZone();
  if (!chatInput) {
    return false;
  }
  chatInput.value = fallbackMessage;
  try {
    if (typeof window.writeChatInputDraft === 'function') {
      window.writeChatInputDraft(fallbackMessage);
    }
  } catch (_) {}
  chatInput.dispatchEvent(new Event('input', { bubbles: true }));
  if (typeof window.focusChatInput === 'function') {
    window.focusChatInput();
  } else {
    chatInput.focus();
  }
  removeFromQueue(item.id);
  return true;
}

function handleQueueDropToInput(event) {
  if (!isQueueItemDragEvent(event)) {
    return;
  }
  event.preventDefault();
  if (typeof event.stopPropagation === 'function') {
    event.stopPropagation();
  }
  const item = resolveQueueItemFromDragEvent(event);
  if (!item) {
    resetQueueDragState();
    return;
  }
  restoreQueueItemToComposer(item);
  clearQueueDropIndicators();
  resetQueueDragState();
}

function bindQueueInputDropZone() {
  const input = ensureQueueInputDropZone();
  if (!input || input.dataset.queueDropBound === 'true') {
    return;
  }

  input.addEventListener('dragover', (event) => {
    if (!isQueueItemDragEvent(event)) {
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'move';
    }
  });

  input.addEventListener('drop', handleQueueDropToInput);
  input.dataset.queueDropBound = 'true';
}

// Update queue UI state
function updateQueueUI() {
  if (!hasActiveQueue()) {
    messageQueue.isPanelOpen = false;
    messageQueue.isPaused = false;
  }
  setQueueOverlayVisibility(isQueuePanelOpen());
  updateQueueOverlayContent();
  updateQueueChip();
  updateQueueMeta();
  try {
    window.toggleInputButtons?.();
  } catch (_) {}
}

function updateQueueMeta() {
  if (!queueMeta) return;
  const count = getQueueLength();
  if (!hasActiveQueue()) {
    queueMeta.textContent = getQueueTranslation('chat_queue_status_idle', 'Idle');
    return;
  }
  if (isQueuePaused()) {
    queueMeta.textContent = getQueueTranslation('chat_queue_status_paused', 'Paused');
    return;
  }
  queueMeta.textContent = formatQueueTranslation('chat_queue_status_waiting', '{count} waiting', { count });
}

function updateQueueChip() {
  if (!queueChip || !queueChipToggle || !queueChipCount || !queuePauseButton || !queueSendNextButton || !queueClearButton) {
    return;
  }

  const count = getQueueLength();
  const hasItems = hasActiveQueue();
  queueChip.hidden = !hasItems;
  queueChip.classList.toggle('is-paused', hasItems && isQueuePaused());
  queueChip.classList.toggle('is-open', hasItems && isQueuePanelOpen());
  queueChipCount.textContent = String(count);

  const countLabel = formatQueueTranslation('chat_queue_chip_count', '{count} queued', { count });
  const openCopy = getQueueTranslation('chat_queue_toggle_open', 'Open queue list');
  const closeCopy = getQueueTranslation('chat_queue_toggle_close', 'Hide queue list');
  const pausedCopy = getQueueTranslation('chat_queue_status_paused', 'Paused');
  const toggleCopy = isQueuePanelOpen() ? closeCopy : openCopy;
  const summaryLabel = hasItems && isQueuePaused()
    ? `${countLabel}, ${pausedCopy}`
    : countLabel;

  queueChipToggle.disabled = !hasItems;
  queueChipToggle.setAttribute('aria-expanded', isQueuePanelOpen() ? 'true' : 'false');
  queueChipToggle.setAttribute('aria-label', `${summaryLabel}. ${toggleCopy}`);
  queueChipToggle.title = toggleCopy;

  const pauseLabel = getQueueTranslation(
    isQueuePaused() ? 'chat_queue_resume' : 'chat_queue_pause',
    isQueuePaused() ? 'Resume queue' : 'Pause queue',
  );
  queuePauseButton.innerHTML = isQueuePaused() ? PLAY_ICON_SVG : PAUSE_ICON_SVG;
  queuePauseButton.setAttribute('aria-label', pauseLabel);
  queuePauseButton.title = pauseLabel;
  queuePauseButton.classList.toggle('is-active', isQueuePaused());
  queuePauseButton.disabled = !hasItems;

  const sendNextDisabled = !hasItems || messageQueue.isProcessing || isQueueProcessingBlocked();
  const sendNextLabel = getQueueTranslation('chat_queue_send_next', 'Send next now');
  queueSendNextButton.disabled = sendNextDisabled;
  queueSendNextButton.setAttribute('aria-label', sendNextLabel);
  queueSendNextButton.title = sendNextLabel;

  const clearLabel = getQueueTranslation('chat_queue_clear', 'Clear queue');
  queueClearButton.disabled = !hasItems;
  queueClearButton.setAttribute('aria-label', clearLabel);
  queueClearButton.title = clearLabel;
}

function refreshQueueTranslations() {
  if (queueTitleText) {
    queueTitleText.textContent = getQueueTranslation('chat_queue_title', 'Queued Messages');
  }
  if (queueList) {
    queueList.setAttribute('aria-label', getQueueTranslation('chat_queue_list_aria', 'Queued messages'));
  }
  if (queueEmptyState) {
    queueEmptyState.textContent = getQueueTranslation('chat_queue_empty', 'No messages in queue');
  }
  if (queueChipToggle) {
    const chipLabel = queueChipToggle.querySelector('.chat-queue-chip__label');
    if (chipLabel) {
      chipLabel.textContent = getQueueTranslation('chat_queue_chip_label', 'Queue');
    }
  }
  updateQueueMeta();
  updateQueueChip();
  updateQueueOverlayContent();
}

// Update queue overlay content
function updateQueueOverlayContent() {
  if (!queueList || !queueOverlay) return;
  
  const emptyState = queueOverlay.querySelector('#messageQueueEmpty');
  const items = messageQueue.items;
  
  queueList.innerHTML = '';
  
  if (items.length === 0) {
    if (emptyState) emptyState.style.display = 'block';
    queueList.style.display = 'none';
    restorePendingQueueFocus();
    return;
  }
  
  if (emptyState) emptyState.style.display = 'none';
  queueList.style.display = 'flex';
  
  const fragment = document.createDocumentFragment();
  
  items.forEach((item, index) => {
    const element = createQueueItemElement(item, index);
    fragment.appendChild(element);
  });
  
  queueList.appendChild(fragment);
  restorePendingQueueFocus();
}

function createQueueItemActionButton({
  className,
  label,
  icon,
  focusTarget,
  disabled = false,
  onClick,
}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `message-queue-item-action ${className}`.trim();
  button.dataset.queueFocusTarget = focusTarget;
  button.disabled = Boolean(disabled);
  button.setAttribute('aria-label', label);
  button.title = label;
  button.setAttribute('draggable', 'false');
  button.innerHTML = icon;
  button.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (button.disabled || typeof onClick !== 'function') {
      return;
    }
    onClick();
  });
  return button;
}

function moveQueueItem(itemId, direction, focusTarget) {
  const normalizedDirection = direction === 'up' ? 'up' : 'down';
  const fromIndex = findQueueItemIndex(itemId);
  if (fromIndex === -1) {
    return false;
  }

  const toIndex = normalizedDirection === 'up' ? fromIndex - 1 : fromIndex + 2;
  setPendingQueueItemFocus(itemId, focusTarget);
  const moved = reorderMessageQueueItems(fromIndex, toIndex);
  if (!moved) {
    pendingQueueFocus = null;
    return false;
  }

  announceQueueMessage(formatQueueTranslation(
    'chat_queue_item_moved_to_position',
    'Moved to position {position}.',
    { position: findQueueItemIndex(itemId) + 1 },
  ));
  return true;
}

// Create queue item element
function createQueueItemElement(item, index) {
  const element = document.createElement('div');
  element.className = 'message-queue-item';
  element.dataset.queueId = item.id;
  element.dataset.queueIndex = String(index);
  element.setAttribute('draggable', 'true');
  element.setAttribute('role', 'listitem');

  const editBtn = document.createElement('button');
  editBtn.type = 'button';
  editBtn.className = 'message-queue-item-main';
  editBtn.dataset.queueFocusTarget = 'edit';
  editBtn.setAttribute('draggable', 'false');
  
  const indexBadge = document.createElement('span');
  indexBadge.className = 'message-queue-item-index';
  indexBadge.textContent = String(index + 1);
  
  const content = document.createElement('span');
  content.className = 'message-queue-item-content';
  
  // Truncate message for display
  const queueMessage = getQueueItemDisplayMessage(item);
  const displayMessage = truncateMessage(queueMessage, 120);
  const isPlaceholderMessage = !resolveQueueItemMessage(item);
  const messageText = document.createElement('span');
  messageText.className = 'message-queue-item-message';
  messageText.classList.toggle('message-queue-item-message--placeholder', isPlaceholderMessage);
  messageText.textContent = displayMessage;
  messageText.title = queueMessage;
  editBtn.title = queueMessage;

  const timestamp = document.createElement('span');
  timestamp.className = 'message-queue-item-timestamp';
  timestamp.textContent = formatQueueTimestamp(item.timestamp) || '';
  timestamp.toggleAttribute('hidden', !timestamp.textContent);

  content.appendChild(messageText);
  content.appendChild(timestamp);

  editBtn.appendChild(indexBadge);
  editBtn.appendChild(content);
  editBtn.addEventListener('click', () => {
    editQueueItem(item);
  });

  const actions = document.createElement('div');
  actions.className = 'message-queue-item-actions';
  actions.setAttribute('role', 'group');
  actions.setAttribute('aria-label', getQueueTranslation('chat_queue_item_actions_aria', 'Queue item actions'));

  const moveUpBtn = createQueueItemActionButton({
    className: 'message-queue-item-move',
    label: getQueueTranslation('chat_queue_item_move_up', 'Move up in queue'),
    icon: MOVE_UP_ICON_SVG,
    focusTarget: 'move-up',
    disabled: index === 0,
    onClick: () => moveQueueItem(item.id, 'up', 'move-up'),
  });

  const moveDownBtn = createQueueItemActionButton({
    className: 'message-queue-item-move',
    label: getQueueTranslation('chat_queue_item_move_down', 'Move down in queue'),
    icon: MOVE_DOWN_ICON_SVG,
    focusTarget: 'move-down',
    disabled: index === messageQueue.items.length - 1,
    onClick: () => moveQueueItem(item.id, 'down', 'move-down'),
  });
  
  const deleteBtn = document.createElement('button');
  deleteBtn.type = 'button';
  deleteBtn.className = 'message-queue-item-action message-queue-item-delete';
  deleteBtn.dataset.queueFocusTarget = 'delete';
  deleteBtn.setAttribute('aria-label', getQueueTranslation('chat_queue_item_remove', 'Remove from queue'));
  deleteBtn.title = getQueueTranslation('chat_queue_item_remove', 'Remove from queue');
  deleteBtn.setAttribute('draggable', 'false');
  deleteBtn.innerHTML = TRASH_ICON_SVG;
  
  // Delete handler
  deleteBtn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const nextFocusItem = messageQueue.items[index + 1] || messageQueue.items[index - 1] || null;
    if (nextFocusItem?.id) {
      setPendingQueueItemFocus(nextFocusItem.id, 'edit');
    } else {
      setPendingComposerFocus();
    }
    if (removeFromQueue(item.id)) {
      announceQueueMessage(getQueueTranslation('chat_queue_item_removed', 'Removed from queue.'));
    } else {
      pendingQueueFocus = null;
    }
  });
  element.addEventListener('dragstart', (event) => {
    queueDragState.sourceIndex = Number(element.dataset.queueIndex);
    queueDragState.sourceId = item.id;
    element.classList.add('dragging');
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      try {
        event.dataTransfer.setData(QUEUE_ITEM_DRAG_MIME, item.id);
      } catch (_) {}
      try {
        event.dataTransfer.setData(QUEUE_ITEM_DRAG_TEXT_MIME, item.id);
      } catch (_) {}
      try {
        event.dataTransfer.setData('text/plain', item.id);
      } catch (_) {}
    }
  });
  element.addEventListener('dragend', () => {
    element.classList.remove('dragging');
    clearQueueDropIndicators();
    resetQueueDragState();
  });
  element.addEventListener('dragover', (event) => {
    if (!queueDragState.sourceId) return;
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'move';
    }
    const rect = element.getBoundingClientRect();
    const shouldInsertAfter = (event.clientY - rect.top) > rect.height / 2;
    updateQueueDropIndicator(element, shouldInsertAfter ? 'after' : 'before');
  });
  element.addEventListener('dragleave', () => {
    element.classList.remove('drop-target-before', 'drop-target-after');
  });
  element.addEventListener('drop', (event) => {
    if (!isQueueItemDragEvent(event)) return;
    event.preventDefault();
    event.stopPropagation();
    const draggedId = getQueueDragEventItemId(event) || queueDragState.sourceId;
    const fromIndex = Number.isInteger(queueDragState.sourceIndex)
      ? queueDragState.sourceIndex
      : findQueueItemIndex(draggedId);
    if (!Number.isInteger(fromIndex) || fromIndex < 0) {
      resetQueueDragState();
      clearQueueDropIndicators();
      return;
    }
    const targetIndex = Number(element.dataset.queueIndex);
    const rect = element.getBoundingClientRect();
    const shouldInsertAfter = (event.clientY - rect.top) > rect.height / 2;
    const toIndex = shouldInsertAfter ? targetIndex + 1 : targetIndex;
    clearQueueDropIndicators();
    reorderMessageQueueItems(fromIndex, toIndex);
    resetQueueDragState();
  });

  actions.appendChild(moveUpBtn);
  actions.appendChild(moveDownBtn);
  actions.appendChild(deleteBtn);

  element.appendChild(editBtn);
  element.appendChild(actions);

  return element;
}

// Edit queue item (removes from queue and puts in input)
function editQueueItem(item) {
  restoreQueueItemToComposer(item);
}

// Truncate message for display
function truncateMessage(message, maxLength) {
  if (!message || message.length <= maxLength) {
    return message || '';
  }
  return message.slice(0, maxLength).trim() + '...';
}

function clearQueueDropIndicators() {
  if (!queueList) return;
  queueList.querySelectorAll('.message-queue-item').forEach((el) => {
    el.classList.remove('drop-target-before', 'drop-target-after');
  });
}

function updateQueueDropIndicator(element, position) {
  element.classList.toggle('drop-target-before', position === 'before');
  element.classList.toggle('drop-target-after', position === 'after');
}

function resetQueueDragState() {
  queueDragState.sourceId = null;
  queueDragState.sourceIndex = null;
}

// Hook into generation lifecycle
function setupGenerationHooks() {
  // Store original functions
  const originalStartGenerationUI = window.startGenerationUI;
  const originalEndGenerationUI = window.endGenerationUI;
  
  // Override startGenerationUI
  window.startGenerationUI = function() {
    if (originalStartGenerationUI) {
      originalStartGenerationUI.call(window);
    }
    updateQueueUI();
  };
  
  // Override endGenerationUI
  window.endGenerationUI = function() {
    if (originalEndGenerationUI) {
      originalEndGenerationUI.call(window);
    }
    // This is visual state only. Generation-specific terminal callbacks own
    // queue advancement and cannot be spoofed by navigation or teardown.
    updateQueueUI();
  };
}

// Initialize on DOM ready
function initMessageQueue() {
  initializeQueueUI();
  bindQueueInputDropZone();
  setupGenerationHooks();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initMessageQueue);
} else {
  initMessageQueue();
}

document.addEventListener('i18n:updated', refreshQueueTranslations);

// Expose functions globally
if (typeof window !== 'undefined') {
  window.messageQueue = {
    add: addToQueue,
    remove: removeFromQueue,
    getNext: getNextQueueItem,
    peek: peekNextQueueItem,
    length: getQueueLength,
    clear: clearQueue,
    isEmpty: isQueueEmpty,
    isPaused: isQueuePaused,
    pause: () => setQueuePaused(true),
    resume: () => setQueuePaused(false),
    isPanelOpen: isQueuePanelOpen,
    openPanel: () => setQueuePanelOpen(true),
    closePanel: () => setQueuePanelOpen(false),
    processNext: processNextQueuedMessage,
    handleGenerationTerminal: handleQueueGenerationTerminal,
    updateUI: updateQueueUI,
    queueInput: queueMessageFromInput,
  };
}
