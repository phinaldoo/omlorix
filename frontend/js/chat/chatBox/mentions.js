// ============================================================================
// Skill & Note Mention System
// ============================================================================

const skillMentionState = {
  skills: [],
  lastFetched: 0,
  isOpen: false,
  highlightedIndex: -1,
  mentionStartIndex: -1,
  query: '',
  expandedCategories: new Set(),
  navItems: [],
  isPointerDownInsideMenu: false,
  pointerDownResetTimer: null,
};

if (typeof window !== 'undefined') {
  window.addEventListener('workspaceSkills:changed', () => {
    skillMentionState.lastFetched = 0;
    if (skillMentionState.isOpen) {
      void handleSkillMentionInput();
    }
  });
}

const noteMentionState = {
  notes: [],
  lastFetched: 0,
  query: '',
  resultsQuery: '',
  offset: 0,
  hasMore: false,
  loading: false,
  requestToken: null,
};

const promptMentionState = {
  prompts: [],
  lastFetched: 0,
};

const modelMentionState = {
  models: [],
  lastFetched: 0,
};

const mcpConnectorMentionState = {
  connectors: [],
  modelId: '',
  projectId: '',
  lastFetched: 0,
};

/**
 * Replace the mention menu's private model cache with an authoritative model
 * list that another chat-shell component has already fetched.
 *
 * Keeping this update synchronous is important when an agent is renamed or
 * deleted while an "@" menu is open: the currently visible menu is rerendered
 * immediately and cannot offer a stale agent entry.
 *
 * @param {Array} models - Models returned by the user-model endpoint.
 */
function updateMentionModelsFromRefresh(models) {
  modelMentionState.models = Array.isArray(models) ? models : [];
  modelMentionState.lastFetched = Date.now();

  if (skillMentionState.isOpen) {
    renderMentionDropdown(
      filterSkills(skillMentionState.query),
      filterNotes(skillMentionState.query),
      filterPrompts(skillMentionState.query),
      filterModels(skillMentionState.query),
    );
  }
}

window.addEventListener('userModels:refreshed', (event) => {
  updateMentionModelsFromRefresh(event?.detail?.models);
});

// (moved to top of file)

const normalizeSkillId = (skillId) => {
  if (skillId === null || typeof skillId === 'undefined') {
    return '';
  }
  return String(skillId);
};

const normalizeNoteId = (noteId) => {
  if (noteId === null || typeof noteId === 'undefined') {
    return '';
  }
  return String(noteId);
};

const normalizePromptId = (promptId) => {
  if (promptId === null || typeof promptId === 'undefined') {
    return '';
  }
  return String(promptId);
};

let skillMentionDropdown = null;
let skillMentionBody = null;
let activeMentionCategory = null;

const featureBodiesForMentions = typeof featureIconBodies !== 'undefined' ? featureIconBodies : Icons.featureIconBodies;

const MENTION_CATEGORY_META = {
  skills: {
    labelKey: 'mention_skills',
    fallbackLabel: 'Skills',
    descriptionKey: 'mention_skills_description',
    fallbackDescription: 'Give the assistant a capability',
    icon: featureBodiesForMentions.promptLightning,
    color: '#1E88E5',
  },
  notes: {
    labelKey: 'mention_notes',
    fallbackLabel: 'Notes',
    descriptionKey: 'mention_notes_description',
    fallbackDescription: 'Reference saved workspace notes',
    iconMarkup: Icons.notes_management,
    color: '#10B981',
  },
  prompts: {
    labelKey: 'mention_prompts',
    fallbackLabel: 'Prompts',
    descriptionKey: 'mention_prompts_description',
    fallbackDescription: 'Reuse a saved prompt',
    icon: featureBodiesForMentions.promptChat,
    color: '#F59E0B',
  },
  models: {
    labelKey: 'mention_models',
    fallbackLabel: 'Models',
    descriptionKey: 'mention_models_description',
    fallbackDescription: 'Choose the model for this message',
    icon: featureBodiesForMentions.promptSettings,
    color: '#8B5CF6',
  },
  connectors: {
    labelKey: 'mention_connectors',
    fallbackLabel: 'Connectors',
    descriptionKey: 'mention_connectors_description',
    fallbackDescription: 'Bring in a connected source',
    iconMarkup: Icons.server,
    color: 'var(--primary-color)',
  },
};

function getMentionCategoryMeta(categoryKey) {
  const meta = MENTION_CATEGORY_META[categoryKey];
  if (!meta) return null;
  return {
    ...meta,
    label: getChatI18nString(meta.labelKey, meta.fallbackLabel),
    description: getChatI18nString(meta.descriptionKey, meta.fallbackDescription),
  };
}

function getMentionMenuAriaLabel() {
  return getChatI18nString('mention_menu_aria_label', 'Connectors, Skills, Notes, Prompts, and Models');
}

function createSkillMentionDropdown() {
  if (skillMentionDropdown) return skillMentionDropdown;

  const chatBox = document.getElementById('chatBox');
  if (!chatBox) return null;

  const dropdown = document.createElement('div');
  dropdown.className = 'mention-menu';
  dropdown.id = 'skillMentionDropdown';
  dropdown.setAttribute('role', 'dialog');
  dropdown.setAttribute('aria-modal', 'false');
  dropdown.setAttribute('aria-label', getMentionMenuAriaLabel());
  dropdown.setAttribute('aria-hidden', 'true');
  dropdown.inert = true;

  const body = document.createElement('div');
  body.className = 'mention-menu__body';
  body.addEventListener('scroll', () => {
    const remaining = body.scrollHeight - body.scrollTop - body.clientHeight;
    if (remaining < 120) void loadMoreMentionNotes();
  });
  dropdown.appendChild(body);

  dropdown.addEventListener('pointerdown', () => {
    skillMentionState.isPointerDownInsideMenu = true;
    if (skillMentionState.pointerDownResetTimer) {
      window.clearTimeout(skillMentionState.pointerDownResetTimer);
    }
    skillMentionState.pointerDownResetTimer = window.setTimeout(() => {
      skillMentionState.isPointerDownInsideMenu = false;
      skillMentionState.pointerDownResetTimer = null;
    }, 300);
  });

  chatBox.style.position = 'relative';
  chatBox.insertBefore(dropdown, chatBox.firstChild);

  skillMentionDropdown = dropdown;
  skillMentionBody = body;

  return dropdown;
}

/** Resize the glass card to the active overview or detail content. */
function syncMentionMenuHeight() {
  if (!skillMentionDropdown || !skillMentionBody) return;
  requestAnimationFrame(() => {
    const maxHeight = Math.max(180, Math.min(430, window.innerHeight - 110));
    const naturalHeight = Math.max(92, Math.min(maxHeight, skillMentionBody.scrollHeight));
    skillMentionDropdown.style.height = `${naturalHeight}px`;
  });
}

/** Build the intro shared by the category overview and direct query results. */
function buildMentionIntro() {
  const intro = document.createElement('div');
  intro.className = 'mention-menu__intro';
  const mark = document.createElement('span');
  mark.className = 'mention-menu__mark';
  mark.setAttribute('aria-hidden', 'true');
  mark.textContent = '@';
  const copy = document.createElement('span');
  copy.className = 'mention-menu__intro-copy';
  const title = document.createElement('strong');
  title.textContent = getChatI18nString('mention_add_to_message', 'Add to your message');
  const subtitle = document.createElement('span');
  subtitle.textContent = getChatI18nString('mention_choose_type', 'Choose a type, then select an item');
  copy.append(title, subtitle);
  const shortcut = document.createElement('span');
  shortcut.className = 'mention-menu__shortcut';
  shortcut.textContent = 'esc';
  intro.append(mark, copy, shortcut);
  return intro;
}

async function fetchSkills({ forceRefresh = false } = {}) {
  const now = Date.now();
  const cacheAge = 60000;
  
  if (!forceRefresh && skillMentionState.skills.length && now - skillMentionState.lastFetched < cacheAge) {
    return skillMentionState.skills;
  }
  
  try {
    const response = await window.authedFetch('/api/v1/skills', {
      method: 'GET',
    });
    
    if (!response.ok) {
      console.error('Failed to fetch skills:', response.status);
      return skillMentionState.skills;
    }
    
    const skills = await response.json();
    skillMentionState.skills = Array.isArray(skills) ? skills : [];
    skillMentionState.lastFetched = now;
    
    return skillMentionState.skills;
  } catch (error) {
    console.error('Failed to fetch skills:', error);
    return skillMentionState.skills;
  }
}

async function fetchNotes({ forceRefresh = false, query = skillMentionState.query, append = false } = {}) {
  const now = Date.now();
  const cacheAge = 60000;
  const normalizedQuery = String(query || '').trim();

  if (!append && !forceRefresh && noteMentionState.query === normalizedQuery && noteMentionState.notes.length && now - noteMentionState.lastFetched < cacheAge) {
    return noteMentionState.notes;
  }
  if (append && (noteMentionState.loading || !noteMentionState.hasMore)) return noteMentionState.notes;

  const requestToken = append ? noteMentionState.requestToken : Symbol('mention-notes');
  if (!append) {
    noteMentionState.requestToken = requestToken;
    noteMentionState.query = normalizedQuery;
    noteMentionState.offset = 0;
    noteMentionState.hasMore = false;
  }
  noteMentionState.loading = true;
  
  try {
    const params = new URLSearchParams({
      limit: String(CHAT_MENTION_PAGE_LIMIT),
      offset: String(append ? noteMentionState.offset : 0),
    });
    if (normalizedQuery) params.set('q', normalizedQuery);
    const response = await window.authedFetch(`/api/v1/notes/?${params.toString()}`, {
      method: 'GET',
    });
    
    if (!response.ok) {
      console.error('Failed to fetch notes:', response.status);
      if (!append && noteMentionState.requestToken === requestToken) {
        noteMentionState.resultsQuery = normalizedQuery;
      }
      return noteMentionState.notes;
    }
    
    const payload = await response.json();
    if (noteMentionState.requestToken !== requestToken || noteMentionState.query !== normalizedQuery) return noteMentionState.notes;
    const notes = unwrapMentionPage(payload);
    if (append) {
      const seen = new Set(noteMentionState.notes.map((note) => normalizeNoteId(note.id)));
      noteMentionState.notes.push(...notes.filter((note) => {
        const id = normalizeNoteId(note.id);
        if (!id || seen.has(id)) return false;
        seen.add(id);
        return true;
      }));
    } else {
      noteMentionState.notes = Array.isArray(notes) ? notes : [];
      noteMentionState.resultsQuery = normalizedQuery;
    }
    noteMentionState.offset += notes.length;
    noteMentionState.hasMore = Array.isArray(payload) ? notes.length >= CHAT_MENTION_PAGE_LIMIT : Boolean(payload?.has_more);
    noteMentionState.lastFetched = now;
    
    return noteMentionState.notes;
  } catch (error) {
    console.error('Failed to fetch notes:', error);
    if (!append && noteMentionState.requestToken === requestToken) {
      noteMentionState.resultsQuery = normalizedQuery;
    }
    return noteMentionState.notes;
  } finally {
    if (noteMentionState.requestToken === requestToken) noteMentionState.loading = false;
  }
}

async function loadMoreMentionNotes() {
  if (!skillMentionState.isOpen || noteMentionState.loading || !noteMentionState.hasMore) return;
  const scrollTop = skillMentionBody?.scrollTop || 0;
  await fetchNotes({ query: skillMentionState.query, append: true });
  if (!skillMentionState.isOpen || noteMentionState.query !== skillMentionState.query) return;
  if (activeMentionCategory === 'notes') {
    renderMentionCategoryDetail('notes', filterNotes(skillMentionState.query));
    if (skillMentionBody) skillMentionBody.scrollTop = scrollTop;
    return;
  }
  renderMentionDropdown(
    filterSkills(skillMentionState.query),
    filterNotes(skillMentionState.query),
    filterPrompts(skillMentionState.query),
    filterModels(skillMentionState.query),
    filterMcpConnectors(skillMentionState.query),
  );
  if (skillMentionBody) skillMentionBody.scrollTop = scrollTop;
}

async function fetchPrompts({ forceRefresh = false } = {}) {
  if (typeof window !== 'undefined' && window.enablePromptsFeature === false) {
    promptMentionState.prompts = [];
    return promptMentionState.prompts;
  }

  const now = Date.now();
  const cacheAge = 60000;
  const failureBackoffMs = 5000;
  const hasCachedPrompts = promptMentionState.prompts.length > 0;
  const effectiveCacheAge = hasCachedPrompts ? cacheAge : failureBackoffMs;

  if (!forceRefresh && now - promptMentionState.lastFetched < effectiveCacheAge) {
    return promptMentionState.prompts;
  }

  try {
    const params = new URLSearchParams({
      limit: String(CHAT_MENTION_PAGE_LIMIT),
      offset: '0',
    });
    const response = await window.authedFetch(`/api/v1/prompts/?${params.toString()}`, {
      method: 'GET',
    });

    if (!response.ok) {
      console.error('Failed to fetch prompts:', response.status);
      promptMentionState.lastFetched = now;
      return promptMentionState.prompts;
    }

    const prompts = unwrapMentionPage(await response.json());
    promptMentionState.prompts = Array.isArray(prompts) ? prompts : [];
    promptMentionState.lastFetched = now;
    return promptMentionState.prompts;
  } catch (error) {
    console.error('Failed to fetch prompts:', error);
    promptMentionState.lastFetched = now;
    return promptMentionState.prompts;
  }
}

async function fetchModelsForMention({ forceRefresh = false } = {}) {
  const now = Date.now();
  const cacheAge = 60000;
  
  if (!forceRefresh && modelMentionState.models.length && now - modelMentionState.lastFetched < cacheAge) {
    return modelMentionState.models;
  }
  
  try {
    const models = typeof window.getCachedUserModels === 'function'
      ? await window.getCachedUserModels({ forceRefresh })
      : await (async () => {
        const response = await window.authedFetch('/api/v1/llm/models/user', {
          method: 'GET',
        });

        if (!response.ok) {
          console.error('Failed to fetch models:', response.status);
          return null;
        }

        return response.json();
      })();
    if (!models) {
      return modelMentionState.models;
    }
    modelMentionState.models = Array.isArray(models) ? models : [];
    modelMentionState.lastFetched = now;
    
    return modelMentionState.models;
  } catch (error) {
    console.error('Failed to fetch models:', error);
    return modelMentionState.models;
  }
}

/** Fetch only MCP connectors eligible for the currently selected model. */
async function fetchMcpConnectorsForMention({ forceRefresh = false } = {}) {
  const modelId = String(
    (typeof window.getSelectedModelId === 'function' && window.getSelectedModelId())
    || document.getElementById('modelSelect')?.getAttribute('data-model-id')
    || ''
  ).trim();
  const projectId = String(document.getElementById('chatContainer')?.getAttribute('data-project-id') || '').trim();
  if (!modelId) {
    mcpConnectorMentionState.connectors = [];
    return [];
  }

  const now = Date.now();
  const cacheAge = 60000;
  const sameContext = mcpConnectorMentionState.modelId === modelId
    && mcpConnectorMentionState.projectId === projectId;
  if (
    !forceRefresh
    && sameContext
    && now - mcpConnectorMentionState.lastFetched < cacheAge
  ) {
    return mcpConnectorMentionState.connectors;
  }

  const params = new URLSearchParams({ model_id: modelId });
  if (projectId) {
    params.set('project_id', projectId);
  }
  try {
    const response = await window.authedFetch(`/api/v1/llm/mcp/connectors/mentions?${params.toString()}`);
    if (!response.ok) {
      console.error('Failed to fetch MCP mention connectors:', response.status);
      if (!sameContext) mcpConnectorMentionState.connectors = [];
      return sameContext ? mcpConnectorMentionState.connectors : [];
    }
    const connectors = await response.json();
    mcpConnectorMentionState.connectors = Array.isArray(connectors) ? connectors : [];
    mcpConnectorMentionState.modelId = modelId;
    mcpConnectorMentionState.projectId = projectId;
    mcpConnectorMentionState.lastFetched = now;
    return mcpConnectorMentionState.connectors;
  } catch (error) {
    console.error('Failed to fetch MCP mention connectors:', error);
    if (!sameContext) mcpConnectorMentionState.connectors = [];
    return sameContext ? mcpConnectorMentionState.connectors : [];
  }
}

function filterMcpConnectors(query) {
  const normalized = String(query || '').toLowerCase().trim();
  return mcpConnectorMentionState.connectors.filter((connector) => {
    if (selectedMcpServerIds.has(String(connector?.id || ''))) {
      return false;
    }
    if (!normalized) {
      return true;
    }
    return [connector?.name, connector?.description]
      .map((value) => String(value || '').toLowerCase())
      .some((value) => value.includes(normalized));
  });
}

function getMentionMcpConnectorIcon(connector) {
  const iconsMap = typeof Icons !== 'undefined' && Icons ? Icons : (window.Icons || {});
  const fallback = typeof iconsMap.server === 'string' ? iconsMap.server : '';
  // Managed connection rows do not persist a duplicate MCP icon. Resolve the
  // provider through the same preset map used by Connections workspace cards,
  // while preserving custom icons for ordinary admin and personal servers.
  const providerIconKey = typeof iconsMap.getConnectionProviderIconKey === 'function'
    ? iconsMap.getConnectionProviderIconKey(connector?.provider)
    : '';
  const iconValue = providerIconKey || connector?.icon || '';
  if (typeof window.IconPicker?.renderIconMarkup === 'function') {
    return window.IconPicker.renderIconMarkup(iconValue, { fallback }) || fallback;
  }
  if (providerIconKey && typeof iconsMap[providerIconKey] === 'string') {
    return iconsMap[providerIconKey];
  }
  return fallback;
}

function filterModels(query) {
  const normalized = String(query || '').toLowerCase().trim();
  const isSplitActive = Boolean(window.SplitScreenManager && window.SplitScreenManager.active);
  const currentModelId = typeof window.getSelectedModelId === 'function' ? window.getSelectedModelId() : null;
  
  if (!normalized) {
    return isSplitActive
      ? [...modelMentionState.models]
      : modelMentionState.models.filter(m => m.model_id !== currentModelId);
  }
  return modelMentionState.models.filter(model => {
    if (!isSplitActive && model.model_id === currentModelId) return false;
    const name = String(model.name || '').toLowerCase();
    const desc = String(model.description || '').toLowerCase();
    return name.includes(normalized) || desc.includes(normalized);
  });
}

function getMentionModelIcon(model) {
  const iconValue = model?.model_icon;
  const fallback = (typeof Icons === 'object' && Icons?.omlorix) ? Icons.omlorix : '';
  if (window.IconPicker?.renderIconMarkup) {
    return window.IconPicker.renderIconMarkup(iconValue, {
      fallback,
      imageAlt: 'Model icon',
    });
  }
  if (typeof iconValue !== 'string') {
    return fallback;
  }
  const trimmed = iconValue.trim();
  if (!trimmed) {
    return fallback;
  }
  if (trimmed.startsWith('<')) {
    return trimmed;
  }
  const mapped = typeof Icons === 'object' ? Icons?.[trimmed] : null;
  if (typeof mapped === 'string' && mapped.trim()) {
    return mapped;
  }
  return fallback;
}

function getNoteTitleFromContent(content, maxLength = 30) {
  if (!content || !content.trim()) {
    return 'Untitled Note';
  }
  const firstLine = content.split('\n')[0].trim();
  if (firstLine.length <= maxLength) {
    return firstLine || 'Untitled Note';
  }
  return firstLine.substring(0, maxLength) + '…';
}

function getNotePreviewFromContent(content, maxLength = 50) {
  if (!content || !content.trim()) {
    return 'No content';
  }
  const lines = content.split('\n');
  let preview = lines.length > 1 ? lines.slice(1).join(' ').trim() : '';
  if (!preview) {
    preview = lines[0].trim();
  }
  if (preview.length <= maxLength) {
    return preview || 'No content';
  }
  return preview.substring(0, maxLength) + '…';
}

function resolveNoteTitle(note) {
  const rawTitle = typeof note?.title === 'string' ? note.title.trim() : '';
  if (rawTitle) {
    return rawTitle;
  }
  return getNoteTitleFromContent(note?.content || '');
}

function resolveNoteSnippet(note) {
  const rawSnippet = typeof note?.snippet === 'string' ? note.snippet.trim() : '';
  if (rawSnippet) {
    return rawSnippet;
  }
  return getNotePreviewFromContent(note?.content || '');
}

function filterSkills(query) {
  const normalized = String(query || '').toLowerCase().trim();
  if (!normalized) {
    return skillMentionState.skills.filter(s => !selectedSkillIds.has(normalizeSkillId(s.id)));
  }
  return skillMentionState.skills.filter(skill => {
    if (selectedSkillIds.has(normalizeSkillId(skill.id))) return false;
    const title = String(skill.title || '').toLowerCase();
    return title.includes(normalized);
  });
}

function filterNotes(query) {
  const normalized = String(query || '').toLowerCase().trim();
  const serverQuery = String(noteMentionState.resultsQuery || '').toLowerCase().trim();
  if (!normalized || serverQuery === normalized) {
    return noteMentionState.notes.filter(n => !selectedNoteIds.has(normalizeNoteId(n.id)));
  }
  return noteMentionState.notes.filter(note => {
    if (selectedNoteIds.has(normalizeNoteId(note.id))) return false;
    const haystacks = [note.title, note.snippet, note.content]
      .map(value => String(value || '').toLowerCase());
    return haystacks.some(text => text.includes(normalized));
  });
}

function filterPrompts(query) {
  const normalized = String(query || '').toLowerCase().trim();
  if (!normalized) {
    return promptMentionState.prompts.filter((p) => !selectedPromptIds.has(normalizePromptId(p.id)));
  }
  return promptMentionState.prompts.filter((prompt) => {
    if (selectedPromptIds.has(normalizePromptId(prompt.id))) return false;
    const haystacks = [prompt.title, prompt.description, prompt.content_preview, prompt.owner_name]
      .map((value) => String(value || '').toLowerCase());
    return haystacks.some((text) => text.includes(normalized));
  });
}

const SKILL_MENTION_DEFAULT_ICON_SVG = (typeof featureIconBodies !== 'undefined' ? featureIconBodies : Icons.featureIconBodies).skillDefault;
const SKILL_MENTION_DEFAULT_ICON_COLOR = '#1E88E5';
const SKILL_MENTION_DEFAULT_ICON_ID = 'tool';

const skillMentionIconUtils = window.WorkspaceIconUtils;
const SKILL_MENTION_ICON_OPTIONS = skillMentionIconUtils.getWorkspaceIconOptions();

function parseSkillIcon(iconData) {
  const fallback = { type: 'preset', iconId: SKILL_MENTION_DEFAULT_ICON_ID, svg: SKILL_MENTION_DEFAULT_ICON_SVG, color: SKILL_MENTION_DEFAULT_ICON_COLOR };
  if (!iconData) return fallback;
  const normalizedIconData = typeof iconData === 'string' ? iconData.trim() : iconData;
  try {
    const resolved = skillMentionIconUtils.resolveWorkspaceStoredIcon(normalizedIconData, {
      iconOptions: SKILL_MENTION_ICON_OPTIONS,
      defaultIconId: SKILL_MENTION_DEFAULT_ICON_ID,
      defaultColor: SKILL_MENTION_DEFAULT_ICON_COLOR,
    });
    return {
      ...resolved,
      type: 'preset',
      iconId: resolved?.iconId || SKILL_MENTION_DEFAULT_ICON_ID,
      svg: resolved?.svg || SKILL_MENTION_DEFAULT_ICON_SVG,
      color: skillMentionIconUtils.normalizeColor(resolved?.color, SKILL_MENTION_DEFAULT_ICON_COLOR),
    };
  } catch (e) {
    return fallback;
  }
}

function renderSkillMentionIconMarkup(iconData, size = 16) {
  return skillMentionIconUtils.renderWorkspaceIcon(iconData, {
    size,
    iconOptions: SKILL_MENTION_ICON_OPTIONS,
    defaultIconId: SKILL_MENTION_DEFAULT_ICON_ID,
  });
}

const NOTE_ICON_COLOR = '#10B981';
const featureBodies = typeof featureIconBodies !== 'undefined' ? featureIconBodies : Icons.featureIconBodies;
const PROMPT_ICON_SVG = featureBodies.prompt;
const PROMPT_ICON_COLOR = '#f59e0b';

function buildMentionResultItem({
  itemType,
  id,
  navIndex,
  iconColor = '',
  iconSvg = '',
  iconClass = '',
  titleText = '',
  descriptionText = '',
  onSelect,
}) {
  const item = document.createElement('button');
  item.type = 'button';
  item.className = `mention-menu__item mention-menu__item--${itemType}`;
  if (navIndex === skillMentionState.highlightedIndex) {
    item.classList.add('is-highlighted');
  }
  item.setAttribute(`data-${itemType}-id`, id);
  item.setAttribute('data-type', itemType);
  item.setAttribute('data-nav-index', String(navIndex));

  const iconWrapper = document.createElement('div');
  iconWrapper.className = `mention-menu__item-icon${iconClass ? ' ' + iconClass : ''}`;
  if (iconColor) {
    iconWrapper.style.backgroundColor = iconColor;
  }
  if (iconSvg) {
    iconWrapper.innerHTML = iconSvg;
  }

  const content = document.createElement('div');
  content.className = 'mention-menu__item-content';

  const title = document.createElement('div');
  title.className = 'mention-menu__item-title';
  title.textContent = titleText;
  content.appendChild(title);

  if (descriptionText) {
    const description = document.createElement('div');
    description.className = 'mention-menu__item-description';
    description.textContent = descriptionText;
    content.appendChild(description);
  }

  item.appendChild(iconWrapper);
  item.appendChild(content);

  // Match the demo's trailing action affordance without introducing another
  // inline SVG; the shared icon registry keeps this glyph consistent.
  const addIcon = document.createElement('span');
  addIcon.className = 'mention-menu__add-icon';
  addIcon.setAttribute('aria-hidden', 'true');
  addIcon.innerHTML = Icons.plus;
  item.appendChild(addIcon);

  item.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (typeof onSelect === 'function') {
      onSelect();
    }
  });

  item.addEventListener('mouseenter', () => {
    skillMentionState.highlightedIndex = navIndex;
    updateHighlightedItem();
  });

  return item;
}

function buildResultItemForCategory(categoryKey, entity, navIndex) {
  if (categoryKey === 'skills') {
    const iconData = parseSkillIcon(entity.icon);
    return buildMentionResultItem({
      itemType: 'skill',
      id: entity.id,
      navIndex,
      iconColor: iconData.color,
      iconSvg: renderSkillMentionIconMarkup(iconData, 16),
      titleText: entity.title || getChatI18nString('mention_untitled_skill', 'Untitled Skill'),
      descriptionText: entity.content || '',
      onSelect: () => selectSkill(entity),
    });
  }
  if (categoryKey === 'notes') {
    return buildMentionResultItem({
      itemType: 'note',
      id: entity.id,
      navIndex,
      iconColor: NOTE_ICON_COLOR,
      iconSvg: Icons.notes_management,
      titleText: resolveNoteTitle(entity),
      descriptionText: resolveNoteSnippet(entity),
      onSelect: () => selectNote(entity),
    });
  }
  if (categoryKey === 'prompts') {
    return buildMentionResultItem({
      itemType: 'prompt',
      id: entity.id,
      navIndex,
      iconColor: PROMPT_ICON_COLOR,
      iconSvg: Icons.wrapSvgBody(PROMPT_ICON_SVG, { ariaHidden: false }),
      titleText: entity.title || getChatI18nString('mention_untitled_prompt', 'Untitled Prompt'),
      descriptionText: entity.description || entity.content_preview || '',
      onSelect: () => selectPrompt(entity),
    });
  }
  if (categoryKey === 'models') {
    const iconMarkup = getMentionModelIcon(entity);
    const iconHolder = document.createElement('div');
    iconHolder.innerHTML = iconMarkup;
    const iconSvg = iconHolder.querySelector('svg');
    if (iconSvg) {
      iconSvg.setAttribute('width', '18');
      iconSvg.setAttribute('height', '18');
    }
    return buildMentionResultItem({
      itemType: 'model',
      id: entity.model_id,
      navIndex,
      iconClass: 'mention-menu__item-icon--bare',
      iconSvg: iconHolder.innerHTML,
      titleText: entity.name || getChatI18nString('mention_unknown_model', 'Unknown Model'),
      descriptionText: entity.description || '',
      onSelect: () => selectModelFromMention(entity),
    });
  }
  if (categoryKey === 'connectors') {
    return buildMentionResultItem({
      itemType: 'connector',
      id: entity.id,
      navIndex,
      iconClass: 'mention-menu__item-icon--bare',
      iconSvg: getMentionMcpConnectorIcon(entity),
      titleText: entity.name || getChatI18nString('mention_unknown_connector', 'Unknown connector'),
      descriptionText: entity.description || getChatI18nString('mention_connector_description', 'Add this connector to the next request'),
      onSelect: () => selectMcpConnector(entity),
    });
  }
  return null;
}

function buildCategoryHeaderEl({ categoryKey, count, countHasMore = false, expanded, navIndex, expandable }) {
  const meta = getMentionCategoryMeta(categoryKey);
  if (!meta) return null;
  const header = document.createElement('button');
  header.type = 'button';
  header.className = 'mention-menu__category-header';
  header.setAttribute('data-category', categoryKey);
  header.setAttribute('data-nav-index', String(navIndex));
  header.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  if (navIndex === skillMentionState.highlightedIndex) {
    header.classList.add('is-highlighted');
  }
  if (expanded) {
    header.classList.add('is-expanded');
  }

  const iconWrap = document.createElement('span');
  iconWrap.className = 'mention-menu__category-icon';
  iconWrap.style.color = meta.color;
  iconWrap.innerHTML = meta.iconMarkup
    || Icons.wrapSvgBody(meta.icon, { strokeWidth: '1.75', ariaHidden: false });

  const copy = document.createElement('span');
  copy.className = 'mention-menu__category-copy';
  const label = document.createElement('strong');
  label.className = 'mention-menu__category-label';
  label.textContent = meta.label;
  const description = document.createElement('span');
  description.className = 'mention-menu__category-description';
  description.textContent = meta.description;
  copy.append(label, description);

  const countEl = document.createElement('span');
  countEl.className = 'mention-menu__category-count';
  countEl.textContent = `${count}${countHasMore ? '+' : ''}`;

  header.appendChild(iconWrap);
  header.appendChild(copy);
  header.appendChild(countEl);

  if (expandable) {
    const chevron = document.createElement('span');
    chevron.className = 'mention-menu__category-chevron';
    header.appendChild(chevron);
  }

  header.addEventListener('mousedown', (e) => {
    e.preventDefault();
  });

  header.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!expandable) return;
    skillMentionState.highlightedIndex = navIndex;
    toggleMentionCategory(categoryKey);
  });

  header.addEventListener('mouseenter', () => {
    skillMentionState.highlightedIndex = navIndex;
    updateHighlightedItem();
  });

  return header;
}

function toggleMentionCategory(categoryKey) {
  const categoryItems = {
    connectors: filterMcpConnectors(''),
    skills: filterSkills(''),
    notes: filterNotes(''),
    prompts: filterPrompts(''),
    models: filterModels(''),
  }[categoryKey] || [];
  renderMentionCategoryDetail(categoryKey, categoryItems);
}

/** Render one category inside the same card instead of opening a side menu. */
function renderMentionCategoryDetail(categoryKey, items) {
  if (!skillMentionBody) return;
  const meta = getMentionCategoryMeta(categoryKey);
  if (!meta) return;
  activeMentionCategory = categoryKey;
  skillMentionBody.innerHTML = '';
  skillMentionBody.scrollTop = 0;
  skillMentionState.navItems = [];
  skillMentionState.highlightedIndex = -1;

  const header = document.createElement('header');
  header.className = 'mention-menu__detail-header';
  const back = document.createElement('button');
  back.type = 'button';
  back.className = 'mention-menu__back';
  back.setAttribute('aria-label', getChatI18nString('mention_back_to_types', 'Back to mention types'));
  back.addEventListener('mousedown', (event) => event.preventDefault());
  back.addEventListener('click', (event) => {
    event.preventDefault();
    renderMentionDropdown(
      filterSkills(''),
      filterNotes(''),
      filterPrompts(''),
      filterModels(''),
      filterMcpConnectors(''),
    );
    document.getElementById('chatBoxInput')?.focus({ preventScroll: true });
  });
  const heading = document.createElement('span');
  heading.className = 'mention-menu__detail-heading';
  const title = document.createElement('strong');
  title.textContent = meta.label;
  const subtitle = document.createElement('span');
  subtitle.textContent = getChatI18nString('mention_select_item', 'Select an item to add');
  heading.append(title, subtitle);
  const shortcut = document.createElement('span');
  shortcut.className = 'mention-menu__shortcut';
  shortcut.textContent = 'esc';
  header.append(back, heading, shortcut);

  const searchWrap = document.createElement('label');
  searchWrap.className = 'mention-menu__search-wrap';
  const searchIcon = document.createElement('span');
  searchIcon.className = 'mention-menu__search-icon';
  searchIcon.setAttribute('aria-hidden', 'true');
  const search = document.createElement('input');
  search.type = 'search';
  const searchLabel = formatChatI18nString(
    'mention_search_category',
    'Search {category}…',
    { category: meta.label.toLocaleLowerCase() },
  );
  search.placeholder = searchLabel;
  search.setAttribute('aria-label', searchLabel);
  searchWrap.append(searchIcon, search);

  const list = document.createElement('div');
  list.className = 'mention-menu__detail-list';
  const empty = buildEmptyState();
  empty.hidden = true;

  const renderItems = (query = '') => {
    const normalized = String(query).trim().toLocaleLowerCase();
    list.innerHTML = '';
    skillMentionState.navItems = [];
    let navIndex = 0;
    items.forEach((entity) => {
      const item = buildResultItemForCategory(categoryKey, entity, navIndex);
      if (!item) return;
      const haystack = `${item.textContent || ''}`.toLocaleLowerCase();
      if (normalized && !haystack.includes(normalized)) return;
      item.dataset.navIndex = String(navIndex);
      list.appendChild(item);
      skillMentionState.navItems.push({ type: 'item', categoryKey, entity });
      navIndex += 1;
    });
    empty.hidden = navIndex > 0;
    syncMentionMenuHeight();
  };
  search.addEventListener('input', () => renderItems(search.value));
  search.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      back.click();
    }
  });

  skillMentionBody.append(header, searchWrap, list, empty);
  renderItems();
  requestAnimationFrame(() => search.focus({ preventScroll: true }));
}

function buildEmptyState() {
  const empty = document.createElement('div');
  empty.className = 'mention-menu__empty';
  empty.textContent = getChatI18nString('mention_no_matches', 'No matches');
  return empty;
}

function renderMentionDropdown(
  filteredSkills,
  filteredNotes,
  filteredPrompts = [],
  filteredModels = [],
  filteredConnectors = filterMcpConnectors(skillMentionState.query),
) {
  if (!skillMentionBody) return;

  skillMentionBody.innerHTML = '';
  skillMentionBody.scrollTop = 0;
  activeMentionCategory = null;
  skillMentionState.navItems = [];
  skillMentionBody.appendChild(buildMentionIntro());

  const hasQuery = String(skillMentionState.query || '').trim().length > 0;
  const totalCount =
    filteredSkills.length + filteredNotes.length + filteredPrompts.length + filteredModels.length + filteredConnectors.length;

  // The bare-@ overview always exposes every supported destination, matching
  // the demo even when a workspace does not have items in a category yet.
  if (hasQuery && !totalCount) {
    skillMentionDropdown.classList.add('mention-menu--empty');
    skillMentionBody.appendChild(buildEmptyState());
    syncMentionMenuHeight();
    return;
  }
  skillMentionDropdown.classList.remove('mention-menu--empty');

  skillMentionDropdown.classList.toggle('mention-menu--filtered', hasQuery);
  skillMentionDropdown.classList.toggle('mention-menu--categories', !hasQuery);

  const categoryEntries = [
    { key: 'models', items: filteredModels },
    { key: 'notes', items: filteredNotes },
    { key: 'connectors', items: filteredConnectors },
    { key: 'skills', items: filteredSkills },
    { key: 'prompts', items: filteredPrompts },
  ].filter((c) => !hasQuery || c.items.length > 0);

  let navIndex = 0;

  if (hasQuery) {
    // FILTERED MODE: show all matches grouped by category with subtle labels
    categoryEntries.forEach((cat, idx) => {
      const meta = getMentionCategoryMeta(cat.key);
      if (!meta) return;
      const section = document.createElement('div');
      section.className = `mention-menu__section mention-menu__section--${cat.key}`;
      if (idx > 0) {
        section.classList.add('mention-menu__section--with-divider');
      }

      const labelRow = document.createElement('div');
      labelRow.className = 'mention-menu__section-label';

      const labelDot = document.createElement('span');
      labelDot.className = 'mention-menu__section-label-dot';
      labelDot.style.backgroundColor = meta.color;
      labelRow.appendChild(labelDot);

      const labelText = document.createElement('span');
      labelText.textContent = meta.label;
      labelRow.appendChild(labelText);

      const labelCount = document.createElement('span');
      labelCount.className = 'mention-menu__section-label-count';
      labelCount.textContent = `${cat.items.length}${cat.key === 'notes' && noteMentionState.hasMore ? '+' : ''}`;
      labelRow.appendChild(labelCount);

      section.appendChild(labelRow);

      const list = document.createElement('div');
      list.className = 'mention-menu__list';

      cat.items.forEach((entity) => {
        const item = buildResultItemForCategory(cat.key, entity, navIndex);
        if (item) {
          list.appendChild(item);
          skillMentionState.navItems.push({ type: 'item', categoryKey: cat.key, entity });
          navIndex += 1;
        }
      });

      section.appendChild(list);
      skillMentionBody.appendChild(section);
    });
    syncMentionMenuHeight();
    return;
  }

  // CATEGORIES MODE: show demo-style type rows that open in-card details.
  const sectionTitle = document.createElement('div');
  sectionTitle.className = 'mention-menu__section-title';
  sectionTitle.textContent = getChatI18nString('mention_section_title', 'Mention');
  skillMentionBody.appendChild(sectionTitle);

  categoryEntries.forEach((cat) => {
    const wrapper = document.createElement('div');
    wrapper.className = 'mention-menu__category';
    wrapper.setAttribute('data-category', cat.key);

    const header = buildCategoryHeaderEl({
      categoryKey: cat.key,
      count: cat.items.length,
      countHasMore: cat.key === 'notes' && noteMentionState.hasMore,
      expanded: false,
      navIndex,
      expandable: true,
    });
    if (!header) return;
    wrapper.appendChild(header);
    skillMentionState.navItems.push({ type: 'category', categoryKey: cat.key });
    navIndex += 1;

    skillMentionBody.appendChild(wrapper);
  });
  syncMentionMenuHeight();
}

document.addEventListener('i18n:updated', () => {
  if (!skillMentionDropdown) return;
  skillMentionDropdown.setAttribute('aria-label', getMentionMenuAriaLabel());
  if (!skillMentionState.isOpen) return;
  renderMentionDropdown(
    filterSkills(skillMentionState.query),
    filterNotes(skillMentionState.query),
    filterPrompts(skillMentionState.query),
    filterModels(skillMentionState.query),
  );
});

function updateHighlightedItem() {
  if (!skillMentionDropdown) return;
  const allItems = skillMentionDropdown.querySelectorAll('[data-nav-index]');
  allItems.forEach((item) => {
    const idx = parseInt(item.getAttribute('data-nav-index'), 10);
    const isHighlighted = idx === skillMentionState.highlightedIndex;
    item.classList.toggle('is-highlighted', isHighlighted);
  });
}

async function selectModelFromMention(model) {
  if (!model || !model.model_id) return;
  
  const input = document.getElementById('chatBoxInput');
  if (!input) return;
  
  const value = input.value;
  const mentionStart = skillMentionState.mentionStartIndex;
  const cursorPos = input.selectionStart;
  
  // Remove the @query from input
  const beforeMention = value.slice(0, mentionStart);
  const afterCursor = value.slice(cursorPos);
  
  input.value = beforeMention + afterCursor;
  input.setSelectionRange(beforeMention.length, beforeMention.length);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  
  closeSkillMentionDropdown();
  
  if (window.SplitScreenManager && window.SplitScreenManager.active && typeof window.SplitScreenManager.selectMentionModel === 'function') {
    window.SplitScreenManager.selectMentionModel(model);
  } else if (typeof window.selectModel === 'function') {
    // Use the selectModel function from modelSelect.js if available
    await window.selectModel(model);
  } else {
    // Fallback: trigger model selection manually
    if (typeof window.updateModelSelectLabel === 'function') {
      window.updateModelSelectLabel(model);
    }
    if (typeof window.setModelSelectDataAttribute === 'function') {
      window.setModelSelectDataAttribute(model.model_id);
    }
    window.dispatchEvent(new CustomEvent('modelSelect:changed', { detail: { modelId: model.model_id } }));
    
    // Save the selection
    try {
      await window.authedFetch('/api/v1/users/settings/last-model/set', {
        method: 'POST',
        body: JSON.stringify({ model_id: model.model_id })
      });
    } catch (e) {
      console.error('Failed to save model selection:', e);
    }
  }
  
  input.focus();
}

function selectMcpConnector(connector) {
  if (!connector?.id) return;
  const input = document.getElementById('chatBoxInput');
  if (!input) return;

  const value = input.value;
  const mentionStart = skillMentionState.mentionStartIndex;
  const cursorPos = input.selectionStart;
  const beforeMention = value.slice(0, mentionStart);
  const afterCursor = value.slice(cursorPos);
  input.value = beforeMention + afterCursor;
  input.setSelectionRange(beforeMention.length, beforeMention.length);
  input.dispatchEvent(new Event('input', { bubbles: true }));

  addMcpConnectorAttachment(connector);
  closeSkillMentionDropdown();
  input.focus();
}

function addMcpConnectorAttachment(connector) {
  const serverId = String(connector?.id || '').trim();
  if (!serverId || selectedMcpServerIds.has(serverId)) return;

  selectedMcpServerIds.add(serverId);
  mcpConnectorMetadataMap.set(serverId, { ...connector, id: serverId });
  window.setMcpServerEnabledForCurrentRequest?.(serverId, true);

  const container = document.getElementById('chatBoxFiles');
  if (!container) return;
  const element = document.createElement('div');
  element.className = 'inline-files-element inline-mcp-connector-element';
  element.dataset.mcpServerId = serverId;

  const iconEl = document.createElement('span');
  iconEl.className = 'inline-skill-element-icon inline-mcp-connector-icon';
  iconEl.setAttribute('aria-hidden', 'true');
  iconEl.innerHTML = getMentionMcpConnectorIcon(connector);

  const contentEl = document.createElement('div');
  contentEl.className = 'inline-files-element-content';
  const topRow = document.createElement('div');
  topRow.className = 'inline-files-element-content-top';
  const titleEl = document.createElement('p');
  titleEl.textContent = connector.name || getChatI18nString('mention_unknown_connector', 'Unknown connector');
  titleEl.title = titleEl.textContent;
  topRow.appendChild(titleEl);
  const bottomRow = document.createElement('div');
  bottomRow.className = 'inline-files-element-content-bottom';
  const typeMeta = document.createElement('p');
  typeMeta.textContent = getChatI18nString('chat_attachment_type_connector', 'CONNECTOR');
  bottomRow.appendChild(typeMeta);
  contentEl.append(topRow, bottomRow);

  const deleteEl = document.createElement('div');
  deleteEl.className = 'inline-files-element-delete';
  deleteEl.setAttribute('role', 'button');
  deleteEl.setAttribute('tabindex', '0');
  deleteEl.setAttribute('aria-label', getChatI18nString('chat_attachment_remove_connector', 'Remove connector'));
  deleteEl.innerHTML = Icons.close;
  const removeConnector = () => removeMcpConnectorAttachment(serverId);
  deleteEl.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    removeConnector();
  });
  deleteEl.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      removeConnector();
    }
  });

  element.append(iconEl, contentEl, deleteEl);
  container.appendChild(element);
  toggleChatFilesContainer(true);
  persistCurrentChatInputDraft();
}

function removeMcpConnectorAttachment(serverId) {
  const normalizedId = String(serverId || '').trim();
  if (!normalizedId) return;
  selectedMcpServerIds.delete(normalizedId);
  mcpConnectorMetadataMap.delete(normalizedId);
  Array.from(document.querySelectorAll('.inline-mcp-connector-element')).find(
    (element) => element.dataset.mcpServerId === normalizedId
  )?.remove();
  window.setMcpServerEnabledForCurrentRequest?.(normalizedId, false);
  updateChatFilesContainerVisibility();
  persistCurrentChatInputDraft();
}

function clearAllMcpConnectorAttachments() {
  const serverIds = Array.from(selectedMcpServerIds);
  document.querySelectorAll('.inline-mcp-connector-element').forEach((element) => element.remove());
  selectedMcpServerIds.clear();
  mcpConnectorMetadataMap.clear();
  serverIds.forEach((serverId) => window.setMcpServerEnabledForCurrentRequest?.(serverId, false));
  window.clearMcpServersForNextRequest?.();
  updateChatFilesContainerVisibility();
  persistCurrentChatInputDraft();
}

function getSelectedMcpServerIds() {
  return Array.from(selectedMcpServerIds);
}

function selectNote(note) {
  if (!note || !note.id) return;
  
  const input = document.getElementById('chatBoxInput');
  if (!input) return;
  
  const value = input.value;
  const mentionStart = skillMentionState.mentionStartIndex;
  const cursorPos = input.selectionStart;
  
  const beforeMention = value.slice(0, mentionStart);
  const afterCursor = value.slice(cursorPos);
  
  input.value = beforeMention + afterCursor;
  input.setSelectionRange(beforeMention.length, beforeMention.length);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  
  addNoteAttachment(note);
  closeSkillMentionDropdown();
  
  input.focus();
}

function selectPrompt(prompt) {
  if (!prompt || !prompt.id) return;

  const input = document.getElementById('chatBoxInput');
  if (!input) return;

  const value = input.value;
  const mentionStart = skillMentionState.mentionStartIndex;
  const cursorPos = input.selectionStart;

  const beforeMention = value.slice(0, mentionStart);
  const afterCursor = value.slice(cursorPos);

  input.value = beforeMention + afterCursor;
  input.setSelectionRange(beforeMention.length, beforeMention.length);
  input.dispatchEvent(new Event('input', { bubbles: true }));

  addPromptAttachment(prompt);
  closeSkillMentionDropdown();

  input.focus();
}

function addNoteAttachment(note) {
  if (!note || !note.id) return;
  const noteKey = normalizeNoteId(note.id);
  if (!noteKey) return;
  if (selectedNoteIds.has(noteKey)) return;
  
  selectedNoteIds.add(noteKey);
  noteMetadataMap.set(noteKey, note);
  
  const container = document.getElementById('chatBoxFiles');
  if (!container) return;
  
  const element = document.createElement('div');
  element.className = 'inline-files-element inline-note-element';
  element.dataset.noteId = noteKey;
  
  const iconEl = document.createElement('span');
  iconEl.className = 'inline-skill-element-icon';
  iconEl.style.backgroundColor = NOTE_ICON_COLOR;
  iconEl.innerHTML = Icons.notes_management;
  
  const contentEl = document.createElement('div');
  contentEl.className = 'inline-files-element-content';

  const topRow = document.createElement('div');
  topRow.className = 'inline-files-element-content-top';

  const titleEl = document.createElement('p');
  const noteTitle = resolveNoteTitle(note);
  titleEl.textContent = noteTitle;
  titleEl.title = noteTitle;
  topRow.appendChild(titleEl);

  const bottomRow = document.createElement('div');
  bottomRow.className = 'inline-files-element-content-bottom';

  const typeMeta = document.createElement('p');
  typeMeta.textContent = getChatI18nString('chat_attachment_type_note', 'NOTE');
  bottomRow.appendChild(typeMeta);

  const snippetMeta = document.createElement('p');
  snippetMeta.className = 'inline-note-snippet';
  snippetMeta.textContent = resolveNoteSnippet(note);
  bottomRow.appendChild(snippetMeta);

  contentEl.appendChild(topRow);
  contentEl.appendChild(bottomRow);

  const deleteEl = document.createElement('div');
  deleteEl.className = 'inline-files-element-delete';
  deleteEl.setAttribute('role', 'button');
  deleteEl.setAttribute('tabindex', '0');
  deleteEl.setAttribute('aria-label', getChatI18nString('chat_attachment_remove_note', 'Remove note'));
  deleteEl.innerHTML = Icons.close;
  
  const removeNote = () => {
    removeNoteAttachment(noteKey);
  };
  
  deleteEl.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    removeNote();
  });
  
  deleteEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      removeNote();
    }
  });

  element.appendChild(iconEl);
  element.appendChild(contentEl);
  element.appendChild(deleteEl);
  container.appendChild(element);

  toggleChatFilesContainer(true);
  persistCurrentChatInputDraft();
}

function removeNoteAttachment(noteId) {
  const noteKey = normalizeNoteId(noteId);
  if (!noteKey) return;
  
  selectedNoteIds.delete(noteKey);
  noteMetadataMap.delete(noteKey);
  
  const container = document.getElementById('chatBoxFiles');
  if (container) {
    const element = container.querySelector(`.inline-note-element[data-note-id="${noteKey}"]`);
    if (element) {
      element.remove();
    }
  }
  
  updateChatFilesContainerVisibility();
  persistCurrentChatInputDraft();
}

function clearAllNoteAttachments() {
  const container = document.getElementById('chatBoxFiles');
  if (container) {
    const elements = container.querySelectorAll('.inline-note-element');
    elements.forEach(el => el.remove());
  }
  selectedNoteIds.clear();
  noteMetadataMap.clear();
  updateChatFilesContainerVisibility();
  persistCurrentChatInputDraft();
}

function getSelectedNoteIds() {
  return Array.from(selectedNoteIds).map((noteKey) => {
    const note = noteMetadataMap.get(noteKey);
    return typeof note?.id !== 'undefined' ? note.id : noteKey;
  });
}

function addPromptAttachment(prompt) {
  if (!prompt || !prompt.id) return;
  const promptKey = normalizePromptId(prompt.id);
  if (!promptKey) return;
  if (selectedPromptIds.has(promptKey)) return;

  selectedPromptIds.add(promptKey);
  promptMetadataMap.set(promptKey, prompt);

  const container = document.getElementById('chatBoxFiles');
  if (!container) return;

  const element = document.createElement('div');
  element.className = 'inline-files-element inline-prompt-element';
  element.dataset.promptId = promptKey;

  const iconEl = document.createElement('span');
  iconEl.className = 'inline-skill-element-icon';
  iconEl.style.backgroundColor = PROMPT_ICON_COLOR;
  iconEl.innerHTML = Icons.wrapSvgBody(PROMPT_ICON_SVG, { ariaHidden: false });

  const contentEl = document.createElement('div');
  contentEl.className = 'inline-files-element-content';

  const topRow = document.createElement('div');
  topRow.className = 'inline-files-element-content-top';

  const titleEl = document.createElement('p');
  const fallbackPromptTitle = getChatI18nString('chat_attachment_prompt_fallback', 'Prompt');
  titleEl.textContent = prompt.title || fallbackPromptTitle;
  titleEl.title = prompt.title || fallbackPromptTitle;
  topRow.appendChild(titleEl);

  const bottomRow = document.createElement('div');
  bottomRow.className = 'inline-files-element-content-bottom';

  const typeMeta = document.createElement('p');
  typeMeta.textContent = getChatI18nString('chat_attachment_type_prompt', 'PROMPT');
  bottomRow.appendChild(typeMeta);

  const descMeta = document.createElement('p');
  descMeta.className = 'inline-note-snippet';
  descMeta.textContent = prompt.description || prompt.content_preview || '';
  bottomRow.appendChild(descMeta);

  contentEl.appendChild(topRow);
  contentEl.appendChild(bottomRow);

  const deleteEl = document.createElement('div');
  deleteEl.className = 'inline-files-element-delete';
  deleteEl.setAttribute('role', 'button');
  deleteEl.setAttribute('tabindex', '0');
  deleteEl.setAttribute('aria-label', getChatI18nString('chat_attachment_remove_prompt', 'Remove prompt'));
  deleteEl.innerHTML = Icons.close;

  const removePrompt = () => {
    removePromptAttachment(promptKey);
  };

  deleteEl.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    removePrompt();
  });

  deleteEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      removePrompt();
    }
  });

  element.appendChild(iconEl);
  element.appendChild(contentEl);
  element.appendChild(deleteEl);
  container.appendChild(element);

  toggleChatFilesContainer(true);
  persistCurrentChatInputDraft();
}

function removePromptAttachment(promptId) {
  const promptKey = normalizePromptId(promptId);
  if (!promptKey) return;

  selectedPromptIds.delete(promptKey);
  promptMetadataMap.delete(promptKey);

  const container = document.getElementById('chatBoxFiles');
  if (container) {
    const element = container.querySelector(`.inline-prompt-element[data-prompt-id="${promptKey}"]`);
    if (element) {
      element.remove();
    }
  }
  updateChatFilesContainerVisibility();
  persistCurrentChatInputDraft();
}

function clearAllPromptAttachments() {
  const container = document.getElementById('chatBoxFiles');
  if (container) {
    const elements = container.querySelectorAll('.inline-prompt-element');
    elements.forEach((el) => el.remove());
  }
  selectedPromptIds.clear();
  promptMetadataMap.clear();
  updateChatFilesContainerVisibility();
  persistCurrentChatInputDraft();
}

function getSelectedPromptIds() {
  return Array.from(selectedPromptIds).map((promptKey) => {
    const prompt = promptMetadataMap.get(promptKey);
    return typeof prompt?.id !== 'undefined' ? prompt.id : promptKey;
  });
}

function openSkillMentionDropdown() {
  if (!skillMentionDropdown) {
    createSkillMentionDropdown();
  }
  if (skillMentionDropdown) {
    skillMentionDropdown.classList.add('open');
    skillMentionDropdown.setAttribute('aria-hidden', 'false');
    skillMentionDropdown.inert = false;
    skillMentionState.isOpen = true;
    document.getElementById('chatBoxInput')?.setAttribute('aria-expanded', 'true');
    syncMentionMenuHeight();
  }
}

function closeSkillMentionDropdown() {
  if (skillMentionDropdown) {
    skillMentionDropdown.classList.remove('open');
    skillMentionDropdown.setAttribute('aria-hidden', 'true');
    skillMentionDropdown.inert = true;
    skillMentionDropdown.style.height = '0px';
  }
  document.getElementById('chatBoxInput')?.setAttribute('aria-expanded', 'false');
  if (skillMentionState.pointerDownResetTimer) {
    window.clearTimeout(skillMentionState.pointerDownResetTimer);
    skillMentionState.pointerDownResetTimer = null;
  }
  skillMentionState.isOpen = false;
  skillMentionState.highlightedIndex = -1;
  skillMentionState.mentionStartIndex = -1;
  skillMentionState.query = '';
  skillMentionState.expandedCategories.clear();
  skillMentionState.navItems = [];
  skillMentionState.isPointerDownInsideMenu = false;
  activeMentionCategory = null;
}

function selectSkill(skill) {
  if (!skill || !skill.id) return;
  
  const input = document.getElementById('chatBoxInput');
  if (!input) return;
  
  const value = input.value;
  const mentionStart = skillMentionState.mentionStartIndex;
  const cursorPos = input.selectionStart;
  
  const beforeMention = value.slice(0, mentionStart);
  const afterCursor = value.slice(cursorPos);
  
  input.value = beforeMention + afterCursor;
  input.setSelectionRange(beforeMention.length, beforeMention.length);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  
  addSkillAttachment(skill);
  closeSkillMentionDropdown();
  
  input.focus();
}

function addSkillAttachment(skill) {
  if (!skill || !skill.id) return;
  const skillKey = normalizeSkillId(skill.id);
  if (!skillKey) return;
  
  if (selectedSkillIds.has(skillKey)) return;
  
  selectedSkillIds.add(skillKey);
  skillMetadataMap.set(skillKey, skill);
  
  const container = document.getElementById('chatBoxFiles');
  if (!container) return;
  
  const iconData = parseSkillIcon(skill.icon);
  
  const element = document.createElement('div');
  element.className = 'inline-files-element inline-skill-element';
  element.dataset.skillId = skillKey;

  const iconEl = document.createElement('span');
  iconEl.className = 'inline-skill-element-icon';
  iconEl.style.backgroundColor = iconData.color;
  iconEl.innerHTML = renderSkillMentionIconMarkup(iconData, 16);

  const contentEl = document.createElement('div');
  contentEl.className = 'inline-files-element-content';

  const topRow = document.createElement('div');
  topRow.className = 'inline-files-element-content-top';

  const titleEl = document.createElement('p');
  const fallbackSkillTitle = getChatI18nString('chat_attachment_skill_fallback', 'Skill');
  titleEl.textContent = skill.title || fallbackSkillTitle;
  titleEl.title = skill.title || fallbackSkillTitle;
  topRow.appendChild(titleEl);

  const bottomRow = document.createElement('div');
  bottomRow.className = 'inline-files-element-content-bottom';

  const typeMeta = document.createElement('p');
  typeMeta.textContent = getChatI18nString('chat_attachment_type_skill', 'SKILL');
  bottomRow.appendChild(typeMeta);

  if (skill.is_admin_skill) {
    const adminMeta = document.createElement('p');
    adminMeta.textContent = getChatI18nString('chat_attachment_admin_skill', 'Managed Skill');
    bottomRow.appendChild(adminMeta);
  }

  if (skill.owner_name) {
    const ownerMeta = document.createElement('p');
    ownerMeta.textContent = skill.owner_name;
    bottomRow.appendChild(ownerMeta);
  }

  contentEl.appendChild(topRow);
  contentEl.appendChild(bottomRow);
  
  const deleteEl = document.createElement('div');
  deleteEl.className = 'inline-files-element-delete';
  deleteEl.setAttribute('role', 'button');
  deleteEl.setAttribute('tabindex', '0');
  deleteEl.setAttribute('aria-label', getChatI18nString('chat_attachment_remove_skill', 'Remove skill'));
  deleteEl.innerHTML = Icons.close;
  
  const removeSkill = () => {
    removeSkillAttachment(skillKey);
  };
  
  deleteEl.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    removeSkill();
  });
  
  deleteEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      removeSkill();
    }
  });
  
  element.appendChild(iconEl);
  element.appendChild(contentEl);
  element.appendChild(deleteEl);
  container.appendChild(element);
  
  toggleChatFilesContainer(true);
  persistCurrentChatInputDraft();
}

function removeSkillAttachment(skillId) {
  const skillKey = normalizeSkillId(skillId);
  if (!skillKey) return;
  
  selectedSkillIds.delete(skillKey);
  skillMetadataMap.delete(skillKey);
  
  const container = document.getElementById('chatBoxFiles');
  if (container) {
    const element = container.querySelector(`.inline-skill-element[data-skill-id="${skillKey}"]`);
    if (element) {
      element.remove();
    }
  }
  
  updateChatFilesContainerVisibility();
  persistCurrentChatInputDraft();
}

function clearAllSkillAttachments() {
  const container = document.getElementById('chatBoxFiles');
  if (container) {
    const elements = container.querySelectorAll('.inline-skill-element');
    elements.forEach(el => el.remove());
  }
  selectedSkillIds.clear();
  skillMetadataMap.clear();
  updateChatFilesContainerVisibility();
  persistCurrentChatInputDraft();
}

function getSelectedSkillIds() {
  return Array.from(selectedSkillIds).map((skillKey) => {
    const skill = skillMetadataMap.get(skillKey);
    return typeof skill?.id !== 'undefined' ? skill.id : skillKey;
  });
}

async function handleSkillMentionInput() {
  const input = document.getElementById('chatBoxInput');
  if (!input) return;

  const value = input.value;
  const cursorPos = input.selectionStart;

  const mentionMatch = findMentionAtCursor(value, cursorPos);

  if (!mentionMatch) {
    if (skillMentionState.isOpen) {
      closeSkillMentionDropdown();
    }
    return;
  }

  const queryChanged = skillMentionState.query !== mentionMatch.query;
  skillMentionState.mentionStartIndex = mentionMatch.startIndex;
  skillMentionState.query = mentionMatch.query;

  // When transitioning to/from a query, collapse all expanded categories so
  // the categorized view starts fresh when the user clears the query.
  if (queryChanged && !mentionMatch.query) {
    skillMentionState.expandedCategories.clear();
  }

  const hasQuery = String(mentionMatch.query || '').trim().length > 0;
  // Open immediately from cached data so typing @ always feels responsive;
  // live sources replace the cached rows as soon as their parallel requests
  // complete.
  skillMentionState.highlightedIndex = hasQuery ? 0 : -1;
  openSkillMentionDropdown();
  renderMentionDropdown(
    filterSkills(mentionMatch.query),
    filterNotes(mentionMatch.query),
    filterPrompts(mentionMatch.query),
    filterModels(mentionMatch.query),
    filterMcpConnectors(mentionMatch.query),
  );

  // Every source is independent, so opening the menu never serializes API IO.
  await Promise.all([
    fetchSkills(),
    fetchNotes({ query: mentionMatch.query, forceRefresh: queryChanged }),
    fetchPrompts(),
    fetchModelsForMention(),
    fetchMcpConnectorsForMention(),
  ]);

  const currentMention = findMentionAtCursor(input.value, input.selectionStart);
  if (!currentMention
      || currentMention.startIndex !== mentionMatch.startIndex
      || currentMention.query !== mentionMatch.query
      || !skillMentionState.isOpen) {
    return;
  }

  const filteredSkills = filterSkills(mentionMatch.query);
  const filteredNotes = filterNotes(mentionMatch.query);
  const filteredPrompts = filterPrompts(mentionMatch.query);
  const filteredModels = filterModels(mentionMatch.query);
  const filteredConnectors = filterMcpConnectors(mentionMatch.query);

  // In categories mode we don't auto-highlight (to keep it minimalist);
  // in filtered mode we highlight the first item for fast Enter-to-select.
  skillMentionState.highlightedIndex = hasQuery ? 0 : -1;

  renderMentionDropdown(filteredSkills, filteredNotes, filteredPrompts, filteredModels, filteredConnectors);
}

function findMentionAtCursor(value, cursorPos) {
  const beforeCursor = value.slice(0, cursorPos);
  
  const atIndex = beforeCursor.lastIndexOf('@');
  if (atIndex === -1) return null;
  
  const charBefore = atIndex > 0 ? beforeCursor.charAt(atIndex - 1) : '';
  if (charBefore && !/\s/.test(charBefore)) {
    return null;
  }
  
  const query = beforeCursor.slice(atIndex + 1);
  
  if (/\s/.test(query)) {
    return null;
  }

  if (query && !/^[a-zA-Z0-9._-]+$/.test(query)) {
    return null;
  }
  
  return {
    startIndex: atIndex,
    query: query.trim(),
  };
}

function selectMentionNavItem(navItem) {
  if (!navItem) return;
  if (navItem.type === 'category') {
    toggleMentionCategory(navItem.categoryKey);
    return;
  }
  const { categoryKey, entity } = navItem;
  if (categoryKey === 'skills') return selectSkill(entity);
  if (categoryKey === 'notes') return selectNote(entity);
  if (categoryKey === 'prompts') return selectPrompt(entity);
  if (categoryKey === 'models') return selectModelFromMention(entity);
  if (categoryKey === 'connectors') return selectMcpConnector(entity);
}

function handleSkillMentionKeydown(event) {
  if (!skillMentionState.isOpen) return false;

  const navItems = skillMentionState.navItems;
  const total = navItems.length;

  switch (event.key) {
    case 'ArrowDown':
      event.preventDefault();
      if (!total) return true;
      skillMentionState.highlightedIndex =
        skillMentionState.highlightedIndex < 0
          ? 0
          : Math.min(skillMentionState.highlightedIndex + 1, total - 1);
      updateHighlightedItem();
      scrollToHighlightedItem();
      return true;

    case 'ArrowUp':
      event.preventDefault();
      if (!total) return true;
      skillMentionState.highlightedIndex =
        skillMentionState.highlightedIndex <= 0
          ? 0
          : skillMentionState.highlightedIndex - 1;
      updateHighlightedItem();
      scrollToHighlightedItem();
      return true;

    case 'ArrowRight': {
      const idx = skillMentionState.highlightedIndex;
      if (idx < 0 || idx >= total) return false;
      const navItem = navItems[idx];
      if (navItem && navItem.type === 'category' && !skillMentionState.expandedCategories.has(navItem.categoryKey)) {
        event.preventDefault();
        toggleMentionCategory(navItem.categoryKey);
        return true;
      }
      return false;
    }

    case 'ArrowLeft': {
      const idx = skillMentionState.highlightedIndex;
      if (idx < 0 || idx >= total) return false;
      const navItem = navItems[idx];
      if (navItem && navItem.type === 'category' && skillMentionState.expandedCategories.has(navItem.categoryKey)) {
        event.preventDefault();
        toggleMentionCategory(navItem.categoryKey);
        return true;
      }
      // If on an item inside an expanded category, collapse parent and move to header.
      if (navItem && navItem.type === 'item') {
        const categoryKey = navItem.categoryKey;
        const headerIdx = navItems.findIndex((n) => n.type === 'category' && n.categoryKey === categoryKey);
        if (headerIdx >= 0) {
          event.preventDefault();
          skillMentionState.highlightedIndex = headerIdx;
          toggleMentionCategory(categoryKey);
          return true;
        }
      }
      return false;
    }

    case 'Enter':
    case 'Tab': {
      const idx = skillMentionState.highlightedIndex;
      if (idx >= 0 && idx < total) {
        event.preventDefault();
        selectMentionNavItem(navItems[idx]);
        return true;
      }
      return false;
    }

    case 'Escape':
      event.preventDefault();
      closeSkillMentionDropdown();
      return true;

    default:
      return false;
  }
}

function scrollToHighlightedItem() {
  if (!skillMentionDropdown) return;
  const highlightedItem = skillMentionDropdown.querySelector('[data-nav-index].is-highlighted');
  if (highlightedItem) {
    highlightedItem.scrollIntoView({ block: 'nearest' });
  }
}

if (chatInput) {
  chatInput.addEventListener('input', () => {
    if (isChatInputDeferredInputWorkActive()) {
      return;
    }
    handleSkillMentionInput();
  });
  
  chatInput.addEventListener('keydown', (e) => {
    if (handleSkillMentionKeydown(e)) {
      e.__skillMentionHandled = true;
      return;
    }
  }, true);
  
  chatInput.addEventListener('blur', () => {
    setTimeout(() => {
      if (skillMentionState.isPointerDownInsideMenu) {
        return;
      }
      if (!skillMentionDropdown?.contains(document.activeElement)) {
        closeSkillMentionDropdown();
      }
    }, 150);
  });
}

document.addEventListener('click', (event) => {
  if (skillMentionState.isOpen) {
    const target = event.target;
    const isInsideDropdown = skillMentionDropdown?.contains(target);
    const isInput = target === chatInput;
    if (!isInsideDropdown && !isInput) {
      closeSkillMentionDropdown();
    }
  }
});

