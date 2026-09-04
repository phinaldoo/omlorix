import { renderIcons } from './icons.js';

const supportedLocales = ['en', 'de', 'es', 'zh', 'fr', 'hi', 'ar', 'ja', 'it', 'pt', 'ru'];
const rtlLocales = new Set(['ar']);
const categoryTranslationKeys = Object.freeze({
  constraint: 'category_constraint',
  experience: 'category_experience',
  goal: 'category_goal',
  identity: 'category_identity',
  other: 'category_other',
  preference: 'category_preference',
  project: 'category_project',
  relationship: 'category_relationship',
});
const eventTranslationKeys = Object.freeze({
  confirmed: 'event_confirmed',
  created: 'event_created',
  expired: 'event_expired',
  forgotten: 'event_forgotten',
  profile: 'event_profile',
  updated: 'event_updated',
});
const memoryStatusTranslationKeys = Object.freeze({
  failed: 'status_memory_failed',
  unchanged: 'status_memory_unchanged',
  updated: 'status_memory_updated',
});
const memoryErrorTranslationKeys = Object.freeze({
  connection: 'status_memory_connection_failed',
  provider: 'status_memory_failed',
  rate_limit: 'status_memory_failed',
});
const elements = {
  activeMetric: document.querySelector('#activeMetric'),
  chatForm: document.querySelector('#chatForm'),
  chatAnnouncement: document.querySelector('#chatAnnouncement'),
  chatLog: document.querySelector('#chatLog'),
  confirmActionButton: document.querySelector('#confirmActionButton'),
  confirmDialog: document.querySelector('#confirmDialog'),
  confirmDialogBody: document.querySelector('#confirmDialogBody'),
  confirmDialogTitle: document.querySelector('#confirmDialogTitle'),
  costMetric: document.querySelector('#costMetric'),
  editDialog: document.querySelector('#editDialog'),
  editForm: document.querySelector('#editForm'),
  editInput: document.querySelector('#editInput'),
  factList: document.querySelector('#factList'),
  freshMetric: document.querySelector('#freshMetric'),
  localeSelect: document.querySelector('#localeSelect'),
  memoryCount: document.querySelector('#memoryCount'),
  messageInput: document.querySelector('#messageInput'),
  modeNote: document.querySelector('#modeNote'),
  modelName: document.querySelector('#modelName'),
  newChatButton: document.querySelector('#newChatButton'),
  operationStatus: document.querySelector('#operationStatus'),
  profileCopy: document.querySelector('#profileCopy'),
  profileTime: document.querySelector('#profileTime'),
  profileVersion: document.querySelector('#profileVersion'),
  resetButton: document.querySelector('#resetButton'),
  runtimeChip: document.querySelector('#runtimeChip'),
  runtimeLabel: document.querySelector('#runtimeLabel'),
  samples: document.querySelector('#samples'),
  sendButton: document.querySelector('#sendButton'),
  simulateButton: document.querySelector('#simulateButton'),
  sweepButton: document.querySelector('#sweepButton'),
  timeline: document.querySelector('#timeline'),
  tokenMetric: document.querySelector('#tokenMetric'),
  toastRegion: document.querySelector('#toastRegion'),
};

let translations = {};
let currentLocale = 'en';
let appState = null;
let pendingMessage = '';
let isSending = false;
let editingMemoryId = null;
let confirmAction = null;
let operationStatusKey = '';
let localeRequestSequence = 0;

function initialLocale() {
  const stored = localStorage.getItem('adaptive-memory-locale');
  if (supportedLocales.includes(stored)) return stored;
  const browserLocale = navigator.language.split('-')[0];
  return supportedLocales.includes(browserLocale) ? browserLocale : 'en';
}

function t(key) {
  return translations[key] || key;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

async function setLocale(locale) {
  const requestSequence = ++localeRequestSequence;
  let requestedLocale = supportedLocales.includes(locale) ? locale : 'en';
  let response = await fetch(`/static/i18n/${requestedLocale}.json`);
  if (!response.ok) {
    requestedLocale = 'en';
    response = await fetch('/static/i18n/en.json');
  }
  const requestedTranslations = await response.json();
  if (requestSequence !== localeRequestSequence) return;
  currentLocale = requestedLocale;
  translations = requestedTranslations;
  localStorage.setItem('adaptive-memory-locale', currentLocale);
  document.documentElement.lang = currentLocale;
  document.documentElement.dir = rtlLocales.has(currentLocale) ? 'rtl' : 'ltr';
  document.title = t('app_name');
  document.querySelector('meta[name="description"]').content = t('hero_subtitle');
  elements.localeSelect.value = currentLocale;

  document.querySelectorAll('[data-i18n]').forEach((element) => {
    element.textContent = t(element.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((element) => {
    element.placeholder = t(element.dataset.i18nPlaceholder);
  });
  document.querySelectorAll('[data-i18n-aria]').forEach((element) => {
    element.setAttribute('aria-label', t(element.dataset.i18nAria));
  });
  elements.operationStatus.textContent = operationStatusKey ? t(operationStatusKey) : '';
  renderState();
}

function formatDate(value) {
  if (!value) return '—';
  return new Intl.DateTimeFormat(currentLocale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));
}

function formatNumber(value) {
  return new Intl.NumberFormat(currentLocale, { maximumFractionDigits: 0 }).format(value || 0);
}

function formatPercent(value) {
  return new Intl.NumberFormat(currentLocale, {
    style: 'percent',
    maximumFractionDigits: 0,
  }).format(value || 0);
}

function formatMoney(value) {
  return new Intl.NumberFormat(currentLocale, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  }).format(value || 0);
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  if (!response.ok) throw new Error(`request_failed_${response.status}`);
  return response.json();
}

function renderState() {
  if (!appState) return;
  renderRuntime();
  renderMetrics();
  renderChat();
  renderProfile();
  renderFacts();
  renderTimeline();
  renderIcons(document);
}

function renderRuntime() {
  const mode = appState.runtime.mode;
  const labelKey = mode === 'live'
    ? 'live_mode'
    : mode === 'unconfigured'
      ? 'unconfigured_mode'
      : 'simulation_mode';
  const noteKey = mode === 'live'
    ? 'mode_note_live'
    : mode === 'unconfigured'
      ? 'mode_note_unconfigured'
      : 'mode_note_simulation';
  elements.runtimeChip.classList.toggle('live', mode === 'live');
  elements.runtimeChip.classList.toggle('unconfigured', mode === 'unconfigured');
  elements.runtimeLabel.textContent = t(labelKey);
  elements.modeNote.textContent = t(noteKey);
  elements.modelName.textContent = appState.runtime.memory_model;
  const unavailable = mode === 'unconfigured';
  elements.messageInput.disabled = unavailable || isSending;
  elements.sendButton.disabled = unavailable || isSending;
  elements.newChatButton.disabled = isSending;
}

function renderMetrics() {
  const metrics = appState.metrics;
  const memoryCapacity = `${formatNumber(metrics.active_memories)} / ${formatNumber(metrics.max_memories)}`;
  elements.activeMetric.textContent = memoryCapacity;
  elements.freshMetric.textContent = formatPercent(metrics.average_freshness);
  elements.tokenMetric.textContent = formatNumber(metrics.total_tokens);
  elements.costMetric.textContent = formatMoney(metrics.last_turn_cost_usd);
  elements.memoryCount.textContent = memoryCapacity;
}

function messageMarkup(message) {
  const roleKey = message.role === 'user' ? 'role_user' : 'role_assistant';
  const icon = message.role === 'user' ? 'user' : 'bot';
  return `
    <article class="message ${message.role}" aria-label="${escapeHtml(t(roleKey))}">
      <span class="message-avatar" data-icon="${icon}" aria-hidden="true"></span>
      <div class="message-bubble">
        <span class="message-role">${escapeHtml(t(roleKey))}</span>
        <div class="message-content">${escapeHtml(message.content)}</div>
      </div>
    </article>`;
}

function renderChat() {
  const messages = [...appState.messages];
  if (pendingMessage) messages.push({ role: 'user', content: pendingMessage });
  if (messages.length === 0 && !isSending) {
    elements.chatLog.innerHTML = `
      <div class="empty-chat">
        <div class="empty-chat-inner">
          <span class="empty-icon" data-icon="memory" aria-hidden="true"></span>
          <h3>${escapeHtml(t('empty_chat_title'))}</h3>
          <p>${escapeHtml(t('empty_chat_body'))}</p>
        </div>
      </div>`;
  } else {
    elements.chatLog.innerHTML = messages.map(messageMarkup).join('');
    if (isSending) {
      elements.chatLog.insertAdjacentHTML('beforeend', `
        <article class="message assistant" aria-label="${escapeHtml(t('role_assistant'))}">
          <span class="message-avatar" data-icon="bot" aria-hidden="true"></span>
          <div class="message-bubble typing" aria-label="${escapeHtml(t('sending'))}">
            <span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>
          </div>
        </article>`);
    }
  }
  elements.samples.hidden = appState.messages.length > 6;
  renderIcons(elements.chatLog);
  requestAnimationFrame(() => {
    elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
  });
}

function renderProfile() {
  const profile = appState.profile;
  elements.profileVersion.textContent = formatNumber(profile.version);
  elements.profileCopy.textContent = profile.content || t('profile_empty');
  elements.profileTime.textContent = profile.updated_at
    ? `${t('profile_updated')}: ${formatDate(profile.updated_at)}`
    : '';
}

function factMarkup(memory) {
  const lifecycleKey = memory.lifecycle_state === 'review' ? 'lifecycle_review' : 'lifecycle_fresh';
  const confidence = Math.round(memory.confidence * 100);
  return `
    <article class="fact-card" data-memory-id="${escapeHtml(memory.id)}">
      <div class="fact-header">
        <span class="fact-category">${escapeHtml(t(categoryTranslationKeys[memory.kind] || 'category_other'))}</span>
        <span class="lifecycle-badge ${memory.lifecycle_state}">
          <span data-icon="${memory.lifecycle_state === 'review' ? 'clock' : 'check'}" aria-hidden="true"></span>
          ${escapeHtml(t(lifecycleKey))}
        </span>
      </div>
      <p class="fact-content">${escapeHtml(memory.content)}</p>
      <div class="confidence-row">
        <span>${escapeHtml(t('fact_confidence'))}</span>
        <div class="confidence-track" role="progressbar" aria-label="${escapeHtml(t('fact_confidence'))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${confidence}">
          <div class="confidence-fill" style="width:${confidence}%"></div>
        </div>
        <span>${formatPercent(memory.confidence)}</span>
      </div>
      <dl class="fact-meta">
        <div><dt>${escapeHtml(t('fact_verified'))}</dt><dd>${formatDate(memory.last_confirmed_at)}</dd></div>
        <div><dt>${escapeHtml(t('fact_review'))}</dt><dd>${formatDate(memory.review_at)}</dd></div>
        <div><dt>${escapeHtml(t('fact_expires'))}</dt><dd>${formatDate(memory.expires_at)}</dd></div>
        <div class="fact-source"><dt>${escapeHtml(t('fact_source'))}</dt><dd>“${escapeHtml(memory.source_excerpt)}”</dd></div>
      </dl>
      <div class="fact-actions">
        <button class="secondary-button" type="button" data-action="confirm"><span data-icon="check" aria-hidden="true"></span>${escapeHtml(t('confirm'))}</button>
        <button class="secondary-button" type="button" data-action="edit"><span data-icon="edit" aria-hidden="true"></span>${escapeHtml(t('edit'))}</button>
        <button class="danger-button" type="button" data-action="forget"><span data-icon="trash" aria-hidden="true"></span>${escapeHtml(t('forget'))}</button>
      </div>
    </article>`;
}

function renderFacts() {
  const memories = appState.memories;
  elements.factList.innerHTML = memories.length
    ? memories.map(factMarkup).join('')
    : `<p class="empty-state">${escapeHtml(t('facts_empty'))}</p>`;
  renderIcons(elements.factList);
}

function focusFactAction(memoryId, action = 'confirm') {
  const card = [...elements.factList.querySelectorAll('[data-memory-id]')]
    .find((candidate) => candidate.dataset.memoryId === memoryId);
  const actionButton = card?.querySelector(`[data-action="${action}"]`);
  (actionButton || document.querySelector('#factsTab'))?.focus();
}

function renderTimeline() {
  const events = appState.events;
  elements.timeline.innerHTML = events.length
    ? events.map((event) => `
      <li class="timeline-item">
        <span class="timeline-dot" aria-hidden="true"></span>
        <div class="timeline-content">
          <strong>${escapeHtml(t(eventTranslationKeys[event.action] || 'event_updated'))}</strong>
          <span>${event.kind ? escapeHtml(t(categoryTranslationKeys[event.kind] || 'category_other')) : ''}</span>
        </div>
        <time datetime="${escapeHtml(event.created_at)}">${formatDate(event.created_at)}</time>
      </li>`).join('')
    : `<li class="empty-state">${escapeHtml(t('timeline_empty'))}</li>`;
}

function showToast(key, type = 'success') {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = t(key);
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 3200);
}

function setSending(value) {
  isSending = value;
  const unavailable = appState?.runtime.mode === 'unconfigured';
  elements.messageInput.disabled = value || unavailable;
  elements.sendButton.disabled = value || unavailable;
  elements.newChatButton.disabled = value;
  const label = elements.sendButton.querySelector('span:last-child');
  label.textContent = t(value ? 'sending' : 'send');
  if (value) {
    operationStatusKey = 'sending';
    elements.operationStatus.textContent = t(operationStatusKey);
  }
  renderChat();
}

async function submitMessage(event) {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  if (!message || isSending) return;
  pendingMessage = message;
  operationStatusKey = '';
  elements.operationStatus.textContent = '';
  elements.messageInput.value = '';
  resizeComposer();
  setSending(true);
  try {
    const result = await apiRequest('/api/chat', {
      method: 'POST',
      body: JSON.stringify({
        message,
        conversation_id: appState.conversation_id,
        locale: currentLocale,
      }),
    });
    appState = result.state;
    const assistantMessage = [...appState.messages].reverse().find((item) => item.role === 'assistant');
    elements.chatAnnouncement.textContent = assistantMessage?.content || '';
    operationStatusKey = result.memory_status === 'failed'
      ? memoryErrorTranslationKeys[result.memory_error] || 'status_memory_failed'
      : memoryStatusTranslationKeys[result.memory_status] || 'status_memory_failed';
    elements.operationStatus.textContent = t(operationStatusKey);
  } catch (error) {
    operationStatusKey = 'status_memory_failed';
    elements.operationStatus.textContent = t(operationStatusKey);
    showToast('toast_error', 'error');
    await loadState();
  } finally {
    pendingMessage = '';
    setSending(false);
    renderState();
    elements.messageInput.focus();
  }
}

async function loadState() {
  try {
    appState = await apiRequest('/api/state');
    renderState();
    return true;
  } catch (error) {
    showToast('toast_error', 'error');
    return false;
  }
}

function resizeComposer() {
  elements.messageInput.style.height = 'auto';
  elements.messageInput.style.height = `${Math.min(elements.messageInput.scrollHeight, 150)}px`;
}

function openEditDialog(memory) {
  editingMemoryId = memory.id;
  elements.editInput.value = memory.content;
  elements.editDialog.showModal();
  requestAnimationFrame(() => elements.editInput.focus());
}

async function submitEdit(event) {
  event.preventDefault();
  if (!editingMemoryId) return;
  const memoryId = editingMemoryId;
  try {
    const result = await apiRequest(`/api/memories/${encodeURIComponent(memoryId)}`, {
      method: 'PATCH',
      body: JSON.stringify({ content: elements.editInput.value }),
    });
    appState = result.state;
    elements.editDialog.close();
    editingMemoryId = null;
    showToast('toast_saved');
    renderState();
    focusFactAction(memoryId, 'edit');
  } catch (error) {
    showToast('toast_error', 'error');
  }
}

function openConfirmation(titleKey, bodyKey, action) {
  confirmAction = action;
  elements.confirmDialogTitle.textContent = t(titleKey);
  elements.confirmDialogBody.textContent = t(bodyKey);
  elements.confirmDialog.showModal();
  requestAnimationFrame(() => elements.confirmActionButton.focus());
}

async function runConfirmedAction() {
  if (!confirmAction) return;
  elements.confirmActionButton.disabled = true;
  try {
    const restoreFocus = await confirmAction();
    elements.confirmDialog.close();
    if (restoreFocus) restoreFocus();
  } catch (error) {
    showToast('toast_error', 'error');
  } finally {
    elements.confirmActionButton.disabled = false;
    confirmAction = null;
  }
}

async function confirmMemory(memoryId) {
  const result = await apiRequest(`/api/memories/${encodeURIComponent(memoryId)}/confirm`, {
    method: 'POST',
    body: '{}',
  });
  appState = result.state;
  showToast('toast_confirmed');
  renderState();
  focusFactAction(memoryId);
}

async function forgetMemory(memoryId) {
  const memoryIndex = appState.memories.findIndex((memory) => memory.id === memoryId);
  const result = await apiRequest(`/api/memories/${encodeURIComponent(memoryId)}`, {
    method: 'DELETE',
  });
  appState = result.state;
  showToast('toast_forgotten');
  renderState();
  const nextMemory = appState.memories[Math.min(memoryIndex, appState.memories.length - 1)];
  return () => focusFactAction(nextMemory?.id);
}

async function runSweep(advanceDays = 0) {
  const result = await apiRequest('/api/lifecycle/sweep', {
    method: 'POST',
    body: JSON.stringify({ advance_days: advanceDays }),
  });
  appState = result.state;
  showToast('toast_sweep');
  renderState();
}

async function resetDemo() {
  const result = await apiRequest('/api/reset', { method: 'POST', body: '{}' });
  appState = result.state;
  showToast('toast_reset');
  renderState();
}

async function createNewChat() {
  if (isSending) return;
  elements.newChatButton.disabled = true;
  try {
    const result = await apiRequest('/api/conversations', { method: 'POST', body: '{}' });
    appState = result.state;
    pendingMessage = '';
    operationStatusKey = '';
    elements.operationStatus.textContent = '';
    showToast('toast_new_chat');
    renderState();
    elements.messageInput.focus();
  } catch (error) {
    showToast('toast_error', 'error');
  } finally {
    elements.newChatButton.disabled = isSending;
  }
}

function selectTab(tab) {
  document.querySelectorAll('[role="tab"]').forEach((button) => {
    const selected = button === tab;
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
    document.querySelector(`#${button.getAttribute('aria-controls')}`).hidden = !selected;
  });
}

function bindEvents() {
  elements.chatForm.addEventListener('submit', submitMessage);
  elements.editForm.addEventListener('submit', submitEdit);
  elements.messageInput.addEventListener('input', resizeComposer);
  elements.messageInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      elements.chatForm.requestSubmit();
    }
  });
  elements.localeSelect.addEventListener('change', (event) => setLocale(event.target.value));
  elements.newChatButton.addEventListener('click', createNewChat);

  document.querySelectorAll('[data-sample]').forEach((button) => {
    button.addEventListener('click', () => {
      elements.messageInput.value = t(button.dataset.sample);
      resizeComposer();
      elements.messageInput.focus();
    });
  });

  document.querySelectorAll('[role="tab"]').forEach((tab) => {
    tab.addEventListener('click', () => selectTab(tab));
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const tabs = [...document.querySelectorAll('[role="tab"]')];
      let next;
      if (event.key === 'Home') {
        next = tabs[0];
      } else if (event.key === 'End') {
        next = tabs.at(-1);
      } else {
        const direction = event.key === 'ArrowRight' ? 1 : -1;
        next = tabs[(tabs.indexOf(tab) + direction + tabs.length) % tabs.length];
      }
      next.focus();
      selectTab(next);
    });
  });

  elements.factList.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action]');
    const card = event.target.closest('[data-memory-id]');
    if (!button || !card) return;
    const memory = appState.memories.find((item) => item.id === card.dataset.memoryId);
    if (!memory) return;
    if (button.dataset.action === 'confirm') confirmMemory(memory.id).catch(() => showToast('toast_error', 'error'));
    if (button.dataset.action === 'edit') openEditDialog(memory);
    if (button.dataset.action === 'forget') {
      openConfirmation('dialog_forget_title', 'dialog_forget_body', () => forgetMemory(memory.id));
    }
  });

  elements.sweepButton.addEventListener('click', () => runSweep().catch(() => showToast('toast_error', 'error')));
  elements.simulateButton.addEventListener('click', () => {
    openConfirmation('dialog_simulate_title', 'dialog_simulate_body', () => runSweep(400));
  });
  elements.resetButton.addEventListener('click', () => {
    openConfirmation('dialog_reset_title', 'dialog_reset_body', resetDemo);
  });
  elements.confirmActionButton.addEventListener('click', runConfirmedAction);
  document.querySelectorAll('[data-close-dialog]').forEach((button) => {
    button.addEventListener('click', () => button.closest('dialog').close());
  });
}

async function initialize() {
  renderIcons(document);
  await setLocale(initialLocale());
  const loaded = await loadState();
  bindEvents();
  if (!loaded) {
    elements.messageInput.disabled = true;
    elements.sendButton.disabled = true;
  }
}

initialize();
