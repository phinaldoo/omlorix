if (typeof window !== 'undefined') {
  window.getSelectedSkillIds = getSelectedSkillIds;
  window.clearAllSkillAttachments = clearAllSkillAttachments;
  window.removeSkillAttachment = removeSkillAttachment;
  window.getSelectedNoteIds = getSelectedNoteIds;
  window.clearAllNoteAttachments = clearAllNoteAttachments;
  window.removeNoteAttachment = removeNoteAttachment;
  window.getSelectedPromptIds = getSelectedPromptIds;
  window.clearAllPromptAttachments = clearAllPromptAttachments;
  window.removePromptAttachment = removePromptAttachment;
  window.addPromptAttachment = addPromptAttachment;
  window.getSelectedMcpServerIds = getSelectedMcpServerIds;
  window.addMcpConnectorAttachment = addMcpConnectorAttachment;
  window.removeMcpConnectorAttachment = removeMcpConnectorAttachment;
  window.clearAllMcpConnectorAttachments = clearAllMcpConnectorAttachments;
  window.getSelectedReferenceParts = getSelectedReferenceParts;
  window.clearAllReferenceParts = clearAllReferenceParts;
  window.removeReferencePart = removeReferencePart;
  window.addReferencePart = addReferencePart;
  window.captureChatComposerStateSnapshot = captureChatComposerStateSnapshot;
  window.applyChatComposerStateSnapshot = applyChatComposerStateSnapshot;
}

// -------------------
// Reference Parts (selected text from assistant messages)
// -------------------
// (selectedReferenceParts moved to top of file)

function getSelectedReferenceParts() {
  return [...selectedReferenceParts];
}

function addReferencePart(text) {
  if (!text || typeof text !== 'string') return;
  const trimmed = text.trim();
  if (!trimmed) return;
  
  // Check for duplicates
  if (selectedReferenceParts.includes(trimmed)) {
    if (typeof notifyWarning === 'function') {
      notifyWarning(getChatI18nString('chat_reference_text_duplicate_error', 'This text is already added as a reference.'));
    }
    return;
  }
  
  selectedReferenceParts.push(trimmed);
  renderReferencePartElement(trimmed, selectedReferenceParts.length - 1);
  chatBoxReferencePartCount = selectedReferenceParts.length;
  updateChatFilesContainerVisibility();
  persistCurrentChatInputDraft();
}

function normalizeReferenceDisplayText(text) {
  return String(text || '').replace(/\s+/g, ' ').trim();
}

function extractMarkedReferenceText(text) {
  const raw = String(text || '').trim();
  if (!raw) return '';

  // Canvas and LaTeX artifact references carry editing metadata before the
  // user's selection. Show the selected text on the chip so the attachment is
  // recognizable without exposing the implementation header first.
  const fencedMarkedText = raw.match(/(?:^|\n)Marked text:\s*\n```[^\n]*\n([\s\S]*?)\n```(?:\n|$)/i);
  if (fencedMarkedText && fencedMarkedText[1]) {
    return normalizeReferenceDisplayText(fencedMarkedText[1]);
  }

  const plainMarkedText = raw.match(/(?:^|\n)Marked text:\s*\n([\s\S]+)$/i);
  if (plainMarkedText && plainMarkedText[1]) {
    return normalizeReferenceDisplayText(plainMarkedText[1].replace(/^```[^\n]*\n?|\n?```$/g, ''));
  }

  return normalizeReferenceDisplayText(raw);
}

function renderReferencePartElement(text, index) {
  const container = document.getElementById('chatBoxFiles');
  if (!container) return;
  
  const element = document.createElement('div');
  element.className = 'inline-files-element inline-reference-element';
  element.dataset.referenceIndex = index;

  const iconEl = document.createElement('span');
  iconEl.className = 'inline-reference-element-icon';
  iconEl.innerHTML = Icons.reference;

  const contentEl = document.createElement('div');
  contentEl.className = 'inline-files-element-content';

  const topRow = document.createElement('div');
  topRow.className = 'inline-files-element-content-top';

  const titleEl = document.createElement('p');
  const previewText = extractMarkedReferenceText(text);
  const displayText = previewText.length > 80 ? previewText.slice(0, 80) + '...' : previewText;
  titleEl.textContent = displayText;
  titleEl.title = previewText || text;
  topRow.appendChild(titleEl);

  const bottomRow = document.createElement('div');
  bottomRow.className = 'inline-files-element-content-bottom';
  const labelEl = document.createElement('span');
  labelEl.textContent = getChatI18nString('chat_reference_label', 'Reference');
  bottomRow.appendChild(labelEl);

  contentEl.appendChild(topRow);
  contentEl.appendChild(bottomRow);

  const deleteEl = document.createElement('button');
  deleteEl.className = 'inline-files-element-delete';
  deleteEl.setAttribute('aria-label', getChatI18nString('chat_reference_remove_aria', 'Remove reference'));
  deleteEl.innerHTML = Icons.close;
  
  const removeRef = () => {
    removeReferencePart(index);
  };
  
  deleteEl.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    removeRef();
  });

  element.appendChild(iconEl);
  element.appendChild(contentEl);
  element.appendChild(deleteEl);
  container.appendChild(element);
}

function removeReferencePart(index) {
  if (index < 0 || index >= selectedReferenceParts.length) return;
  
  selectedReferenceParts.splice(index, 1);
  
  // Re-render all reference parts
  const container = document.getElementById('chatBoxFiles');
  if (container) {
    const elements = container.querySelectorAll('.inline-reference-element');
    elements.forEach(el => el.remove());
  }
  
  selectedReferenceParts.forEach((text, idx) => {
    renderReferencePartElement(text, idx);
  });
  
  chatBoxReferencePartCount = selectedReferenceParts.length;
  updateChatFilesContainerVisibility();
  persistCurrentChatInputDraft();
}

function clearAllReferenceParts() {
  const container = document.getElementById('chatBoxFiles');
  if (container) {
    const elements = container.querySelectorAll('.inline-reference-element');
    elements.forEach(el => el.remove());
  }
  selectedReferenceParts.length = 0;
  chatBoxReferencePartCount = 0;
  updateChatFilesContainerVisibility();
  persistCurrentChatInputDraft();
}

function normalizeChatComposerSnapshotId(rawId) {
  return String(rawId ?? '').trim();
}

function resolveUploadedFileSnapshotById(fileId) {
  const normalizedId = normalizeChatComposerSnapshotId(fileId);
  if (!normalizedId) {
    return null;
  }

  const fromMap = chatBoxUploadedFilesById.get(normalizedId);
  const fromCache = Array.isArray(cachedUploadedFiles.list)
    ? cachedUploadedFiles.list.find((file) => normalizeChatComposerSnapshotId(file?.file_id ?? file?.id) === normalizedId)
    : null;
  const source = fromMap || fromCache;
  if (!source || typeof source !== 'object') {
    return {
      id: normalizedId,
      file_id: normalizedId,
      name: normalizedId,
      original_name: normalizedId,
      file_type: '',
      mime_type: '',
      file_size: 0,
      meta: {
        original_filename: normalizedId,
      },
    };
  }

  const sourceMeta = source.meta && typeof source.meta === 'object' ? source.meta : null;
  const fileType = String(
    source.file_type
    || source.mime_type
    || sourceMeta?.file_type
    || sourceMeta?.mime_type
    || ''
  ).trim();
  const fileName = String(
    sourceMeta?.original_filename
    || source.original_name
    || source.name
    || normalizedId
  );
  const rawSize = source.file_size ?? sourceMeta?.file_size ?? sourceMeta?.size ?? source.size ?? 0;
  const numericSize = Number(rawSize);
  const formatLabel = getFileFormatLabel({
    ...source,
    name: fileName,
    original_name: fileName,
    file_type: fileType,
    mime_type: fileType,
    meta: {
      ...(sourceMeta ? { ...sourceMeta } : {}),
      original_filename: fileName,
      file_type: fileType,
      mime_type: fileType,
    },
  });

  return {
    id: normalizedId,
    file_id: normalizedId,
    name: fileName,
    original_name: fileName,
    extension: formatLabel,
    format_label: formatLabel,
    file_type: fileType,
    mime_type: fileType,
    file_size: Number.isFinite(numericSize) && numericSize >= 0 ? numericSize : 0,
    meta: {
      ...(sourceMeta ? { ...sourceMeta } : {}),
      original_filename: fileName,
      file_type: fileType,
      mime_type: fileType,
      extension: formatLabel,
      format_label: formatLabel,
    },
  };
}

function collectChatComposerEntitySnapshots(selectedSet, metadataMap, fallbackTitle) {
  const snapshots = [];
  const seen = new Set();
  Array.from(selectedSet || []).forEach((rawKey) => {
    const key = normalizeChatComposerSnapshotId(rawKey);
    if (!key || seen.has(key)) {
      return;
    }
    seen.add(key);
    const metadata = metadataMap?.get?.(key);
    if (metadata && typeof metadata === 'object') {
      const snapshot = { ...metadata };
      if (snapshot.id === null || typeof snapshot.id === 'undefined' || snapshot.id === '') {
        snapshot.id = key;
      }
      snapshots.push(snapshot);
      return;
    }
    snapshots.push({ id: key, title: fallbackTitle });
  });
  return snapshots;
}

function collectChatComposerUploadedFileSnapshots() {
  const selectedIds = getSelectedUploadedFileIds();
  const attachedFiles = typeof window.getCurrentChatAttachmentFiles === 'function'
    ? (window.getCurrentChatAttachmentFiles() || [])
    : [];
  const snapshots = [];
  const seen = new Set();

  const candidates = [
    ...selectedIds.map((rawId) => ({ file_id: rawId })),
    ...(Array.isArray(attachedFiles) ? attachedFiles : []),
  ];

  candidates.forEach((candidate) => {
    const normalizedCandidate = normalizeChatComposerUploadedFileSnapshot(candidate);
    const fileId = normalizeChatComposerSnapshotId(normalizedCandidate?.file_id ?? normalizedCandidate?.id);
    if (!fileId || seen.has(fileId)) {
      return;
    }
    seen.add(fileId);

    const resolved = resolveUploadedFileSnapshotById(fileId);
    const fallback = normalizedCandidate;
    if (!resolved && !fallback) {
      return;
    }

    const snapshot = {
      ...(resolved || {}),
      ...(fallback || {}),
      id: fileId,
      file_id: fileId,
    };

    const bestName = String(
      snapshot.meta?.original_filename
      || snapshot.original_name
      || snapshot.name
      || fileId
    );
    const bestType = String(
      snapshot.file_type
      || snapshot.mime_type
      || snapshot.meta?.file_type
      || snapshot.meta?.mime_type
      || ''
    ).trim();
    const rawSize = snapshot.file_size ?? snapshot.meta?.file_size ?? snapshot.meta?.size ?? 0;
    const numericSize = Number(rawSize);
    const formatLabel = getFileFormatLabel({
      ...snapshot,
      name: bestName,
      original_name: bestName,
      file_type: bestType,
      mime_type: bestType,
      meta: {
        ...(snapshot.meta && typeof snapshot.meta === 'object' ? { ...snapshot.meta } : {}),
        original_filename: bestName,
        file_type: bestType,
        mime_type: bestType,
      },
    });

    snapshots.push({
      ...snapshot,
      id: fileId,
      file_id: fileId,
      name: bestName,
      original_name: bestName,
      extension: formatLabel,
      format_label: formatLabel,
      file_type: bestType,
      mime_type: bestType,
      file_size: Number.isFinite(numericSize) && numericSize >= 0 ? numericSize : 0,
      meta: {
        ...(snapshot.meta && typeof snapshot.meta === 'object' ? { ...snapshot.meta } : {}),
        original_filename: bestName,
        file_type: bestType,
        mime_type: bestType,
        extension: formatLabel,
        format_label: formatLabel,
      },
    });
  });

  return snapshots;
}

function captureChatComposerStateSnapshot() {
  const uploadedFiles = collectChatComposerUploadedFileSnapshots();
  return {
    message: String(chatInput?.value || ''),
    uploadedFiles,
    uploadedFileIds: uploadedFiles.map((file) => normalizeChatComposerSnapshotId(file?.file_id ?? file?.id)).filter(Boolean),
    skills: collectChatComposerEntitySnapshots(selectedSkillIds, skillMetadataMap, 'Skill'),
    notes: collectChatComposerEntitySnapshots(selectedNoteIds, noteMetadataMap, 'Note'),
    prompts: collectChatComposerEntitySnapshots(selectedPromptIds, promptMetadataMap, 'Prompt'),
    mcpConnectors: collectChatComposerEntitySnapshots(selectedMcpServerIds, mcpConnectorMetadataMap, 'MCP Server'),
    chatReferences: getSelectedChatReferencePayload(),
    referenceParts: getSelectedReferenceParts(),
  };
}

function normalizeChatComposerEntitySnapshot(entity, fallbackTitle) {
  if (!entity) {
    return null;
  }
  const asObject = typeof entity === 'object' ? entity : { id: entity };
  const id = normalizeChatComposerSnapshotId(asObject.id);
  if (!id) {
    return null;
  }
  const normalized = { ...asObject, id: asObject.id ?? id };
  if (!normalized.title && fallbackTitle) {
    normalized.title = fallbackTitle;
  }
  return normalized;
}

function normalizeChatComposerUploadedFileSnapshot(file) {
  const asObject = file && typeof file === 'object' ? file : { file_id: file };
  const fileId = normalizeChatComposerSnapshotId(asObject.file_id ?? asObject.id);
  if (!fileId) {
    return null;
  }
  const sourceMeta = asObject.meta && typeof asObject.meta === 'object' ? asObject.meta : null;
  const fileName = String(
    sourceMeta?.original_filename
    || asObject.original_name
    || asObject.name
    || fileId
  );
  const fileType = String(
    asObject.file_type
    || asObject.mime_type
    || sourceMeta?.file_type
    || sourceMeta?.mime_type
    || ''
  ).trim();
  const rawSize = asObject.file_size ?? sourceMeta?.file_size ?? sourceMeta?.size ?? 0;
  const numericSize = Number(rawSize);
  const formatLabel = getFileFormatLabel({
    ...asObject,
    name: fileName,
    original_name: fileName,
    file_type: fileType,
    mime_type: fileType,
    meta: {
      ...(sourceMeta ? { ...sourceMeta } : {}),
      original_filename: fileName,
      file_type: fileType,
      mime_type: fileType,
    },
  });

  return {
    ...asObject,
    id: fileId,
    file_id: fileId,
    name: fileName,
    original_name: fileName,
    extension: formatLabel,
    format_label: formatLabel,
    file_type: fileType,
    mime_type: fileType,
    file_size: Number.isFinite(numericSize) && numericSize >= 0 ? numericSize : 0,
    meta: {
      ...(sourceMeta ? { ...sourceMeta } : {}),
      original_filename: fileName,
      file_type: fileType,
      mime_type: fileType,
      extension: formatLabel,
      format_label: formatLabel,
    },
  };
}

function applyChatComposerStateSnapshot(state, {
  focusInput = true,
  dispatchInputEvent = true,
  includeMessage = true,
  persistDraft = true,
} = {}) {
  const snapshot = state && typeof state === 'object' ? state : {};
  const nextMessage = typeof snapshot.message === 'string' ? snapshot.message : '';
  if (includeMessage && chatInput) {
    chatInput.value = nextMessage;
  }

  isApplyingChatComposerStateSnapshot = true;
  try {
    const clearWithAttachmentReset = () => {
      if (typeof window.resetChatAttachmentsState === 'function') {
        try {
          window.resetChatAttachmentsState({ preserveSkills: false });
        } catch (error) {
          console.error('Failed to reset chat attachment state before snapshot restore', error);
        }
      }
      clearChatUploadedFilesSelection({ notify: false });
      clearAllSkillAttachments();
      clearAllNoteAttachments();
      clearAllPromptAttachments();
      clearAllMcpConnectorAttachments();
      clearAllChatReferenceAttachments();
    };

    clearWithAttachmentReset();

    clearAllReferenceParts();
    const uploadedFiles = [];
    const seenUploadedIds = new Set();
    const uploadCandidates = Array.isArray(snapshot.uploadedFiles)
      ? snapshot.uploadedFiles
      : [];
    uploadCandidates.forEach((candidate) => {
      const normalized = normalizeChatComposerUploadedFileSnapshot(candidate);
      const fileId = normalizeChatComposerSnapshotId(normalized?.file_id ?? normalized?.id);
      if (!normalized || !fileId || seenUploadedIds.has(fileId)) {
        return;
      }
      seenUploadedIds.add(fileId);
      chatBoxUploadedFilesById.set(fileId, normalized);
      uploadedFiles.push(normalized);
    });

    if (!uploadedFiles.length && Array.isArray(snapshot.uploadedFileIds)) {
      snapshot.uploadedFileIds.forEach((candidateId) => {
        const fileId = normalizeChatComposerSnapshotId(candidateId);
        if (!fileId || seenUploadedIds.has(fileId)) {
          return;
        }
        seenUploadedIds.add(fileId);
        const resolved = resolveUploadedFileSnapshotById(fileId);
        if (resolved) {
          chatBoxUploadedFilesById.set(fileId, resolved);
          uploadedFiles.push(resolved);
        }
      });
    }

    if (uploadedFiles.length) {
      setSelectedUploadedFileIds(uploadedFiles.map((file) => file.file_id), { notify: true });
    }

    const seenSkillIds = new Set();
    (Array.isArray(snapshot.skills) ? snapshot.skills : []).forEach((candidate) => {
      const normalized = normalizeChatComposerEntitySnapshot(candidate, 'Skill');
      const id = normalizeChatComposerSnapshotId(normalized?.id);
      if (!normalized || !id || seenSkillIds.has(id)) {
        return;
      }
      seenSkillIds.add(id);
      addSkillAttachment(normalized);
    });

    const seenNoteIds = new Set();
    (Array.isArray(snapshot.notes) ? snapshot.notes : []).forEach((candidate) => {
      const normalized = normalizeChatComposerEntitySnapshot(candidate, 'Note');
      const id = normalizeChatComposerSnapshotId(normalized?.id);
      if (!normalized || !id || seenNoteIds.has(id)) {
        return;
      }
      seenNoteIds.add(id);
      addNoteAttachment(normalized);
    });

    const seenPromptIds = new Set();
    (Array.isArray(snapshot.prompts) ? snapshot.prompts : []).forEach((candidate) => {
      const normalized = normalizeChatComposerEntitySnapshot(candidate, 'Prompt');
      const id = normalizeChatComposerSnapshotId(normalized?.id);
      if (!normalized || !id || seenPromptIds.has(id)) {
        return;
      }
      seenPromptIds.add(id);
      addPromptAttachment(normalized);
    });

    const seenMcpServerIds = new Set();
    (Array.isArray(snapshot.mcpConnectors) ? snapshot.mcpConnectors : []).forEach((candidate) => {
      const normalized = normalizeChatComposerEntitySnapshot(candidate, 'MCP Server');
      const id = normalizeChatComposerSnapshotId(normalized?.id);
      if (!normalized || !id || seenMcpServerIds.has(id)) {
        return;
      }
      seenMcpServerIds.add(id);
      addMcpConnectorAttachment(normalized);
    });

    const seenChatReferenceIds = new Set();
    (Array.isArray(snapshot.chatReferences) ? snapshot.chatReferences : []).forEach((candidate) => {
      const chatId = normalizeChatComposerSnapshotId(candidate?.chat_id ?? candidate?.id);
      if (!chatId || seenChatReferenceIds.has(chatId)) {
        return;
      }
      seenChatReferenceIds.add(chatId);
      addChatReferenceAttachment({
        ...(candidate && typeof candidate === 'object' ? candidate : {}),
        chat_id: chatId,
      });
    });

    const seenReferenceParts = new Set();
    (Array.isArray(snapshot.referenceParts) ? snapshot.referenceParts : []).forEach((candidate) => {
      const text = String(candidate || '').trim();
      if (!text || seenReferenceParts.has(text)) {
        return;
      }
      seenReferenceParts.add(text);
      addReferencePart(text);
    });
  } finally {
    isApplyingChatComposerStateSnapshot = false;
  }

  if (persistDraft && chatInput) {
    writeChatInputDraft(chatInput.value || '');
  }

  updateChatFilesContainerVisibility();
  toggleInputButtons();

  if (chatInput && dispatchInputEvent) {
    chatInput.dispatchEvent(new Event('input', { bubbles: true }));
  }

  if (focusInput) {
    if (typeof focusChatInput === 'function') {
      focusChatInput();
    } else if (chatInput) {
      try {
        chatInput.focus({ preventScroll: true });
      } catch (_) {
        chatInput.focus();
      }
    }
  }

  return true;
}

// -------------------
// Selection Tooltip for Assistant Messages
// -------------------
let selectionTooltip = null;
let selectionTooltipController = null;
let suppressedSelectionTooltipFingerprint = null;

/**
 * Build the shared Copy / Add reference tooltip used for marked text.
 *
 * Canvas and the assistant transcript both use this factory so button markup,
 * translations, clipboard feedback, keyboard activation, and visibility
 * semantics stay identical. Callers only provide the selected text and the
 * feature-specific action that turns that text into a reference.
 */
function createSelectionActionTooltip({
  id = '',
  className = '',
  getSelectionText,
  onAddReference,
  onEmptySelection,
  clearSelection,
  getLabels,
} = {}) {
  const tooltip = document.createElement('div');
  if (id) tooltip.id = id;
  tooltip.className = ['selection-tooltip', className].filter(Boolean).join(' ');
  tooltip.setAttribute('aria-hidden', 'true');
  tooltip.inert = true;
  tooltip.innerHTML = `
    <button type="button" class="selection-tooltip-btn" data-action="copy">
      <span class="selection-tooltip-btn-icon" aria-hidden="true">
        ${Icons.copy}
      </span>
      <span class="selection-tooltip-btn-label"></span>
    </button>
    <button type="button" class="selection-tooltip-btn" data-action="add-reference">
      <span class="selection-tooltip-btn-icon" aria-hidden="true">
        ${Icons.reference}
      </span>
      <span class="selection-tooltip-btn-label"></span>
    </button>
  `;

  const defaultLabels = () => ({
    copyLabel: getChatI18nString('chat_selection_copy_label', 'Copy'),
    copyTitle: getChatI18nString('chat_selection_copy_title', 'Copy'),
    addReferenceLabel: getChatI18nString('chat_selection_add_reference_label', 'Add reference'),
    addReferenceTitle: getChatI18nString('chat_selection_add_reference_title', 'Add to message'),
  });

  /** Refresh visible and accessible labels after a locale change. */
  function updateLabels() {
    const labels = typeof getLabels === 'function' ? getLabels() : defaultLabels();
    const copyButton = tooltip.querySelector('[data-action="copy"]');
    const addReferenceButton = tooltip.querySelector('[data-action="add-reference"]');
    const copyLabel = String(labels?.copyLabel || defaultLabels().copyLabel);
    const copyTitle = String(labels?.copyTitle || copyLabel);
    const addReferenceLabel = String(labels?.addReferenceLabel || defaultLabels().addReferenceLabel);
    const addReferenceTitle = String(labels?.addReferenceTitle || addReferenceLabel);

    copyButton?.querySelector('.selection-tooltip-btn-label')?.replaceChildren(copyLabel);
    addReferenceButton?.querySelector('.selection-tooltip-btn-label')?.replaceChildren(addReferenceLabel);
    copyButton?.setAttribute('aria-label', copyTitle);
    copyButton?.setAttribute('title', copyTitle);
    addReferenceButton?.setAttribute('aria-label', addReferenceTitle);
    addReferenceButton?.setAttribute('title', addReferenceTitle);
  }

  function hide() {
    tooltip.classList.remove('visible');
    tooltip.setAttribute('aria-hidden', 'true');
    tooltip.inert = true;
  }

  function showAt(x, y) {
    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${y}px`;
    tooltip.setAttribute('aria-hidden', 'false');
    tooltip.inert = false;
    tooltip.classList.add('visible');
  }

  /** Measure the tooltip at its fully expanded scale without displaying it. */
  function measure() {
    const wasVisible = tooltip.classList.contains('visible');
    if (!wasVisible) tooltip.classList.add('visible');
    const rect = tooltip.getBoundingClientRect();
    if (!wasVisible) tooltip.classList.remove('visible');
    return rect;
  }

  function destroy() {
    document.removeEventListener('i18n:updated', updateLabels);
    tooltip.remove();
  }

  function notifyCopySuccess() {
    const message = getChatI18nString('chat_selection_copy_success', 'Copied to clipboard');
    if (typeof window.notifySuccess === 'function') {
      window.notifySuccess(message);
    } else if (typeof showNotification === 'function') {
      showNotification(message, 'success');
    }
  }

  function notifyCopyError() {
    const message = getChatI18nString('chat_selection_copy_error', 'Failed to copy');
    if (typeof window.notifyError === 'function') {
      window.notifyError(message);
    } else if (typeof showNotification === 'function') {
      showNotification(message, 'error');
    }
  }

  // Prevent the button press from collapsing the marked range before its text
  // has been copied or converted into an artifact reference.
  tooltip.addEventListener('mousedown', (event) => {
    event.preventDefault();
    event.stopPropagation();
  });

  tooltip.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    hide();
  });

  let actionPending = false;
  tooltip.addEventListener('click', async (event) => {
    const button = event.target.closest('.selection-tooltip-btn');
    if (!button || actionPending) return;

    const text = String(typeof getSelectionText === 'function' ? getSelectionText() : '').trim();
    if (!text) {
      hide();
      if (typeof onEmptySelection === 'function') onEmptySelection();
      return;
    }

    actionPending = true;
    try {
      if (button.dataset.action === 'copy') {
        try {
          const exportText = typeof window !== 'undefined'
            && typeof window.appendComplianceWatermarkIfNeeded === 'function'
            ? window.appendComplianceWatermarkIfNeeded(text)
            : text;
          await navigator.clipboard.writeText(exportText);
          notifyCopySuccess();
        } catch (_) {
          notifyCopyError();
        }
      } else if (button.dataset.action === 'add-reference' && typeof onAddReference === 'function') {
        await onAddReference(text);
      }
    } finally {
      actionPending = false;
      hide();
      if (typeof clearSelection === 'function') clearSelection();
    }
  });

  updateLabels();
  document.addEventListener('i18n:updated', updateLabels);

  return {
    destroy,
    element: tooltip,
    hide,
    isVisible: () => tooltip.classList.contains('visible'),
    measure,
    showAt,
    updateLabels,
  };
}

// Canvas and Notes load after chatBox.js and consume this shared component.
window.createSelectionActionTooltip = createSelectionActionTooltip;

function isSelectionTooltipVisible() {
  return Boolean(selectionTooltipController?.isVisible());
}

function getNodePath(node) {
  const path = [];
  let current = node;

  while (current && current !== document.body) {
    let index = 0;
    let sibling = current.previousSibling;

    while (sibling) {
      index += 1;
      sibling = sibling.previousSibling;
    }

    path.unshift(index);
    current = current.parentNode;
  }

  return path.join('.');
}

function getAssistantSelectionData() {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
    return null;
  }

  const text = selection.toString().trim();
  if (!text || text.length < 2) {
    return null;
  }

  const range = selection.getRangeAt(0);
  const commonAncestor = range.commonAncestorContainer;
  if (!commonAncestor || !isInsideAssistantMessage(commonAncestor)) {
    return null;
  }

  const fingerprint = `${text}::${getNodePath(range.startContainer)}:${range.startOffset}-${getNodePath(range.endContainer)}:${range.endOffset}`;

  return {
    selection,
    range,
    text,
    fingerprint,
  };
}

function suppressCurrentSelectionTooltip() {
  const selectionData = getAssistantSelectionData();
  suppressedSelectionTooltipFingerprint = selectionData ? selectionData.fingerprint : null;
  hideSelectionTooltip();
}

function clearSelectionTooltipSuppression(selectionData = null) {
  const currentSelectionData = selectionData || getAssistantSelectionData();
  if (!currentSelectionData || currentSelectionData.fingerprint !== suppressedSelectionTooltipFingerprint) {
    suppressedSelectionTooltipFingerprint = null;
  }
}

function isInteractiveSelectionTarget(target) {
  if (!(target instanceof Element)) {
    return false;
  }

  return Boolean(target.closest('button, a, input, textarea, select, label, summary, [role="button"], [role="link"], [contenteditable="true"]'));
}

function createSelectionTooltip() {
  if (selectionTooltip) return selectionTooltip;
  selectionTooltipController = createSelectionActionTooltip({
    id: 'selectionTooltip',
    getSelectionText: () => getAssistantSelectionData()?.text || '',
    onAddReference: (text) => addReferencePart(text),
    clearSelection: () => window.getSelection()?.removeAllRanges(),
  });
  selectionTooltip = selectionTooltipController.element;
  document.body.appendChild(selectionTooltip);
  return selectionTooltip;
}

function showSelectionTooltip(x, y) {
  createSelectionTooltip();
  selectionTooltipController.showAt(x, y);
}

function hideSelectionTooltip() {
  if (!selectionTooltip) return;
  selectionTooltipController.hide();
  delete selectionTooltip.dataset.selectionFingerprint;
}

function isInsideAssistantMessage(node) {
  let current = node;
  while (current && current !== document.body) {
    if (current.classList && current.classList.contains('assistant-message-container')) {
      return true;
    }
    current = current.parentNode;
  }
  return false;
}

document.addEventListener('mouseup', (e) => {
  // Small delay to allow selection to complete
  setTimeout(() => {
    if (isInteractiveSelectionTarget(e.target)) {
      hideSelectionTooltip();
      return;
    }

    const selectionData = getAssistantSelectionData();
    if (!selectionData) {
      clearSelectionTooltipSuppression(null);
      hideSelectionTooltip();
      return;
    }

    clearSelectionTooltipSuppression(selectionData);

    if (selectionData.fingerprint === suppressedSelectionTooltipFingerprint) {
      hideSelectionTooltip();
      return;
    }

    // Position tooltip above selection
    const rect = selectionData.range.getBoundingClientRect();
    if (!rect || (!rect.width && !rect.height)) {
      hideSelectionTooltip();
      return;
    }

    const tooltip = createSelectionTooltip();
    // Temporarily show off-screen to measure
    tooltip.style.left = '-9999px';
    tooltip.style.top = '-9999px';
    tooltip.classList.add('visible');
    const tw = tooltip.offsetWidth;
    const th = tooltip.offsetHeight;
    tooltip.classList.remove('visible');

    const tooltipX = Math.max(8, Math.min(rect.left + (rect.width / 2) - (tw / 2), window.innerWidth - tw - 8));
    const tooltipY = rect.top - th - 10 + window.scrollY;

    tooltip.dataset.selectionFingerprint = selectionData.fingerprint;
    showSelectionTooltip(tooltipX, tooltipY);
  }, 10);
});

document.addEventListener('pointerdown', (e) => {
  if (!selectionTooltip || selectionTooltip.contains(e.target)) {
    return;
  }

  suppressCurrentSelectionTooltip();
});

document.addEventListener('selectionchange', () => {
  const selectionData = getAssistantSelectionData();

  if (!selectionData) {
    suppressedSelectionTooltipFingerprint = null;
    hideSelectionTooltip();
    return;
  }

  clearSelectionTooltipSuppression(selectionData);

  if (isSelectionTooltipVisible() && selectionTooltip.dataset.selectionFingerprint !== selectionData.fingerprint) {
    hideSelectionTooltip();
  }
});

window.addEventListener('scroll', () => {
  if (!isSelectionTooltipVisible()) {
    return;
  }

  suppressCurrentSelectionTooltip();
}, true);

document.addEventListener('wheel', () => {
  if (!isSelectionTooltipVisible()) {
    return;
  }

  suppressCurrentSelectionTooltip();
}, { passive: true });

document.addEventListener('touchmove', () => {
  if (!isSelectionTooltipVisible()) {
    return;
  }

  suppressCurrentSelectionTooltip();
}, { passive: true });
