(function codeExecutionPage() {
  'use strict';

  const api = window.omlorixServer?.codeExecution;
  if (!api) return;

  const CUSTOM_VERSION_VALUE = '__custom__';
  const VERSION_PATTERN = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$/;

  const state = {
    instances: [],
    loading: false,
    editingId: '',
    deleteConfirmId: '',
    deleteConfirmTimer: null,
    updateInfo: new Map(),
    lastFocusedElement: null,
    versionRequest: 0,
    versionLoading: false,
    editorInstanceVersion: '',
  };

  const elements = {
    add: document.getElementById('codeExecutionAddButton'),
    emptyAdd: document.getElementById('codeExecutionEmptyAddButton'),
    refresh: document.getElementById('codeExecutionRefreshButton'),
    total: document.getElementById('codeExecutionTotal'),
    running: document.getElementById('codeExecutionRunning'),
    healthy: document.getElementById('codeExecutionHealthy'),
    feedback: document.getElementById('codeExecutionFeedback'),
    instances: document.getElementById('codeExecutionInstances'),
    empty: document.getElementById('codeExecutionEmpty'),
    editorOverlay: document.getElementById('codeExecutionEditorOverlay'),
    editor: document.querySelector('#codeExecutionEditorOverlay .code-execution-editor'),
    editorTitle: document.getElementById('codeExecutionEditorTitle'),
    editorDescription: document.getElementById('codeExecutionEditorDescription'),
    editorForm: document.getElementById('codeExecutionEditorForm'),
    editorError: document.getElementById('codeExecutionEditorError'),
    editorCancel: document.getElementById('codeExecutionEditorCancelButton'),
    editorSave: document.getElementById('codeExecutionEditorSaveButton'),
    name: document.getElementById('codeExecutionNameInput'),
    versionSelect: document.getElementById('codeExecutionVersionSelect'),
    customVersionField: document.getElementById('codeExecutionCustomVersionField'),
    customVersion: document.getElementById('codeExecutionCustomVersionInput'),
    versionStatus: document.getElementById('codeExecutionVersionStatus'),
    versionRetry: document.getElementById('codeExecutionVersionRetryButton'),
    port: document.getElementById('codeExecutionPortInput'),
    memory: document.getElementById('codeExecutionMemoryInput'),
    concurrency: document.getElementById('codeExecutionConcurrencyInput'),
    sessionTimeout: document.getElementById('codeExecutionSessionTimeoutInput'),
    network: document.getElementById('codeExecutionNetworkInput'),
    pip: document.getElementById('codeExecutionPipInput'),
    logsOverlay: document.getElementById('codeExecutionLogsOverlay'),
    logsDialog: document.querySelector('#codeExecutionLogsOverlay .code-execution-logs-dialog'),
    logsTitle: document.getElementById('codeExecutionLogsTitle'),
    logsOutput: document.getElementById('codeExecutionLogsOutput'),
    logsClose: document.getElementById('codeExecutionLogsCloseButton'),
  };

  const tr = (source) => (
    typeof window.omlorixLauncherTranslate === 'function'
      ? window.omlorixLauncherTranslate(source)
      : source
  );

  function node(tag, className = '', text = '') {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = tr(text);
    return element;
  }

  function button(label, action, instanceId, className = 'btn btn-ghost btn-sm') {
    const element = node('button', className, label);
    element.type = 'button';
    element.dataset.action = action;
    element.dataset.instanceId = instanceId;
    return element;
  }

  function formatCount(value) {
    return new Intl.NumberFormat(document.documentElement.lang || 'en').format(Number(value || 0));
  }

  function feedback(message = '', kind = '') {
    elements.feedback.textContent = message ? tr(message) : '';
    elements.feedback.classList.toggle('is-error', kind === 'error');
    elements.feedback.classList.toggle('is-success', kind === 'success');
  }

  function statusLabel(instance) {
    if (instance.busy) return 'Working';
    if (instance.status?.healthy) return 'Healthy';
    if (instance.status?.running) return 'Starting';
    return 'Stopped';
  }

  function policyChip(text) {
    return node('span', 'code-execution-policy-chip', text);
  }

  function runtimeItem(label, value) {
    const item = node('div', 'code-execution-runtime-item');
    item.append(node('span', '', label), node('strong', '', value));
    return item;
  }

  function createInstanceCard(instance) {
    const card = node('article', 'code-execution-instance-card');
    card.dataset.instanceId = instance.id;
    card.classList.toggle('is-busy', Boolean(instance.busy));

    const heading = node('div', 'code-execution-card-head');
    const titleRow = node('div', 'code-execution-card-title-row');
    const dot = node('span', 'code-execution-status-dot');
    dot.setAttribute('aria-hidden', 'true');
    dot.classList.add(
      instance.status?.healthy
        ? 'is-healthy'
        : instance.status?.running
          ? 'is-running'
          : instance.status?.composeError
            ? 'is-error'
            : 'is-stopped',
    );
    const title = node('h3', '', instance.name);
    titleRow.append(dot, title);
    const status = node('span', 'tag muted', statusLabel(instance));
    heading.append(titleRow, status);

    const endpoint = node('code', 'code-execution-endpoint', instance.connectionUrl);
    endpoint.title = instance.connectionUrl;

    const policies = node('div', 'code-execution-policy-row');
    policies.append(
      policyChip(`Version ${instance.version}`),
      policyChip(instance.imageSource === 'local' ? 'Local source build' : 'Release images'),
      policyChip(`${instance.memory} memory`),
      policyChip(instance.networkAccess ? 'Network enabled' : 'Network disabled'),
      policyChip(instance.allowPip ? 'pip enabled' : 'pip disabled'),
    );

    const runtime = node('div', 'code-execution-runtime-grid');
    runtime.append(
      runtimeItem('Active jobs', formatCount(
        Number(instance.status?.activeExecutions || 0) + Number(instance.status?.activeRenders || 0),
      )),
      runtimeItem('Concurrency', formatCount(instance.maxConcurrent)),
      runtimeItem('Sessions', `${formatCount(instance.sessionTimeout)} s`),
    );

    const footer = node('div', 'code-execution-card-footer');
    const lifecycle = node('div', 'code-execution-card-actions');
    if (instance.status?.running) {
      lifecycle.append(
        button('Stop', 'stop', instance.id),
        button('Restart', 'restart', instance.id),
      );
    } else {
      lifecycle.append(button('Start', 'start', instance.id, 'btn btn-primary btn-sm'));
    }
    const tools = node('div', 'code-execution-card-actions');
    tools.append(
      button('Connect', 'connect', instance.id),
      button('Logs', 'logs', instance.id),
      button('Settings', 'edit', instance.id),
      button(
        state.updateInfo.get(instance.id)?.updateAvailable ? 'Install update' : 'Check update',
        state.updateInfo.get(instance.id)?.updateAvailable ? 'update' : 'check-update',
        instance.id,
      ),
      button('Server files', 'reveal', instance.id),
      button(
        state.deleteConfirmId === instance.id ? 'Confirm delete' : 'Delete',
        'delete',
        instance.id,
        state.deleteConfirmId === instance.id
          ? 'btn btn-ghost btn-sm code-execution-delete-confirm'
          : 'btn btn-ghost btn-sm',
      ),
    );
    footer.append(lifecycle, tools);
    card.append(heading, endpoint, policies, runtime, footer);

    card.querySelectorAll('button').forEach((control) => {
      control.disabled = Boolean(instance.busy || state.loading);
    });
    return card;
  }

  function render() {
    const total = state.instances.length;
    const running = state.instances.filter((item) => item.status?.running).length;
    const healthy = state.instances.filter((item) => item.status?.healthy).length;
    elements.total.textContent = formatCount(total);
    elements.running.textContent = formatCount(running);
    elements.healthy.textContent = formatCount(healthy);
    elements.instances.replaceChildren(...state.instances.map(createInstanceCard));
    elements.empty.hidden = total !== 0 || state.loading;
    elements.instances.hidden = total === 0;
    elements.add.disabled = state.loading;
    elements.emptyAdd.disabled = state.loading;
    elements.refresh.disabled = state.loading;
  }

  async function refresh({ quiet = false } = {}) {
    if (state.loading) return;
    state.loading = true;
    if (!quiet) feedback('Refreshing Code Execution services');
    render();
    try {
      const result = await api.list();
      state.instances = Array.isArray(result?.instances) ? result.instances : [];
      if (!quiet) feedback('Code Execution services refreshed', 'success');
    } catch (_error) {
      feedback('Could not load Code Execution services', 'error');
    } finally {
      state.loading = false;
      render();
    }
  }

  function instanceById(instanceId) {
    return state.instances.find((item) => item.id === instanceId) || null;
  }

  /** Translate safe manager messages and keep unexpected failures generic. */
  function editorErrorMessage(error) {
    const message = String(error?.message || '').trim();
    const translatedCodes = new Map([
      ['NAME_REQUIRED', 'Enter a name for the Code Execution service.'],
      ['VERSION_INVALID', 'Enter a semantic Code Execution version such as 0.9.0.'],
      ['MEMORY_INVALID', 'Choose a supported sandbox memory limit.'],
      ['IMAGE_SOURCE_INVALID', 'Choose a supported Code Execution image source.'],
      ['SOURCE_MISSING', 'The local Code Execution source checkout could not be found.'],
      ['INSTANCE_NOT_FOUND', 'The Code Execution instance was not found.'],
      ['SECRET_MISSING', 'The Code Execution API key is missing.'],
    ]);
    const translatedMessages = new Set([
      'That gateway port is already assigned to another managed instance.',
      'That gateway port is already in use on this computer.',
    ]);
    if (translatedCodes.has(error?.code)) return tr(translatedCodes.get(error.code));
    return translatedMessages.has(message)
      ? tr(message)
      : tr('Could not save the Code Execution service');
  }

  /** Keep the dialog open and put correction focus on the affected control. */
  function showEditorError(error) {
    const message = editorErrorMessage(error);
    elements.editorError.textContent = message;
    elements.editorError.hidden = false;
    if (error?.code === 'PORT_IN_USE') elements.port.setAttribute('aria-invalid', 'true');
    else elements.port.removeAttribute('aria-invalid');
    feedback(message, 'error');
    if (error?.code === 'PORT_IN_USE') elements.port.focus();
    else elements.editorError.focus();
  }

  /** Add a renderer-safe option without interpolating server text into HTML. */
  function versionOption(value, label, { version = '', imageSource = '' } = {}) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    if (version) option.dataset.version = version;
    if (imageSource) option.dataset.imageSource = imageSource;
    return option;
  }

  function versionOptionValue(imageSource, version) {
    return `${imageSource}:${version}`;
  }

  /** Keep the manual pinning control visible and required only when selected. */
  function syncCustomVersionField({ focus = false } = {}) {
    const custom = elements.versionSelect.value === CUSTOM_VERSION_VALUE;
    elements.customVersionField.hidden = !custom;
    elements.customVersion.required = custom;
    if (!custom) elements.customVersion.setCustomValidity('');
    if (custom && focus) elements.customVersion.focus();
  }

  /** Validate the explicit pin before it crosses the trusted IPC boundary. */
  function validateCustomVersion() {
    if (elements.versionSelect.value !== CUSTOM_VERSION_VALUE) {
      elements.customVersion.setCustomValidity('');
      return true;
    }
    const value = elements.customVersion.value.trim().replace(/^v/i, '');
    const valid = VERSION_PATTERN.test(value);
    elements.customVersion.setCustomValidity(valid
      ? ''
      : tr('Enter a semantic version such as 1.2.3.'));
    return valid;
  }

  /** Render an explicit fallback when GitHub is unavailable. */
  function renderVersionLoadFailure(selectedVersion = '') {
    elements.versionSelect.replaceChildren(
      versionOption(CUSTOM_VERSION_VALUE, tr('Enter a custom version…'), { imageSource: 'release' }),
    );
    elements.versionSelect.disabled = false;
    elements.customVersion.value = selectedVersion;
    elements.versionStatus.textContent = tr('Could not load published releases. Enter a version manually or retry.');
    elements.versionStatus.setAttribute('aria-busy', 'false');
    elements.versionStatus.classList.add('is-error');
    elements.versionRetry.hidden = false;
    syncCustomVersionField();
  }

  /**
   * Load concrete versions whenever the editor opens. The request counter
   * prevents a slow response from an earlier dialog from replacing newer UI.
   */
  async function loadEditorVersions(selectedVersion = '') {
    const requestId = state.versionRequest + 1;
    state.versionRequest = requestId;
    state.versionLoading = true;
    elements.editorSave.disabled = true;
    elements.versionSelect.disabled = true;
    elements.versionSelect.replaceChildren(versionOption('', tr('Loading versions…')));
    elements.customVersionField.hidden = true;
    elements.customVersion.required = false;
    elements.customVersion.setCustomValidity('');
    elements.versionStatus.textContent = tr('Loading published Code Execution releases…');
    elements.versionStatus.setAttribute('aria-busy', 'true');
    elements.versionStatus.classList.remove('is-error');
    elements.versionRetry.hidden = true;

    try {
      const result = await api.getAvailableVersions();
      if (requestId !== state.versionRequest || elements.editorOverlay.hidden) return;
      const versions = Array.isArray(result?.versions)
        ? result.versions.map((value) => String(value || '').trim()).filter(Boolean)
        : [];
      const latestVersion = String(result?.latestVersion || versions[0] || '').trim();
      if (!versions.length || !latestVersion) throw new Error('no versions');

      elements.versionSelect.replaceChildren();
      for (const version of versions) {
        const label = version === latestVersion
          ? `${version} — ${tr('Latest release')}`
          : version;
        elements.versionSelect.append(versionOption(
          versionOptionValue('release', version),
          label,
          { version, imageSource: 'release' },
        ));
      }

      // Preserve a configured version that is no longer in the current release
      // list, so merely opening Settings never changes the pinned tag.
      const selectedValue = versionOptionValue('release', selectedVersion);
      const knownSelection = Array.from(elements.versionSelect.options)
        .some((option) => option.value === selectedValue);
      if (selectedVersion && !knownSelection) {
        elements.versionSelect.append(
          versionOption(
            selectedValue,
            `${selectedVersion} — ${tr('Pinned version')}`,
            { version: selectedVersion, imageSource: 'release' },
          ),
        );
      }

      elements.versionSelect.append(versionOption(
        CUSTOM_VERSION_VALUE,
        tr('Enter a custom version…'),
        { imageSource: 'release' },
      ));
      if (selectedVersion) {
        elements.versionSelect.value = selectedValue;
      } else if (latestVersion) {
        elements.versionSelect.value = versionOptionValue('release', latestVersion);
      }
      elements.versionSelect.disabled = false;
      elements.versionStatus.textContent = tr('Choose a published release or enter a custom version.');
      elements.versionStatus.setAttribute('aria-busy', 'false');
      syncCustomVersionField();
    } catch (_error) {
      if (requestId !== state.versionRequest || elements.editorOverlay.hidden) return;
      renderVersionLoadFailure(selectedVersion);
    } finally {
      if (requestId === state.versionRequest && !elements.editorOverlay.hidden) {
        state.versionLoading = false;
        elements.editorSave.disabled = false;
      }
    }
  }

  function showEditor(instance = null) {
    state.editingId = instance?.id || '';
    state.lastFocusedElement = document.activeElement;
    elements.editorTitle.textContent = tr(instance ? 'Edit Code Execution service' : 'Add Code Execution service');
    elements.editorDescription.textContent = tr(instance
      ? 'Changes are applied by recreating this service when it is running.'
      : 'Create an independently managed gateway and sandbox pool.');
    elements.editorSave.textContent = tr(instance ? 'Save changes' : 'Create service');
    // Input values are properties rather than DOM text nodes, so the launcher's
    // translation observer cannot translate this default after assignment.
    elements.name.value = instance?.name || tr('Local Code Execution');
    state.editorInstanceVersion = instance?.version || '';
    elements.customVersion.value = state.editorInstanceVersion;
    elements.port.value = instance?.port || nextSuggestedPort();
    elements.memory.value = instance?.memory || '512m';
    elements.concurrency.value = instance?.maxConcurrent || 10;
    elements.sessionTimeout.value = instance?.sessionTimeout || 1200;
    elements.network.checked = Boolean(instance?.networkAccess);
    elements.pip.checked = Boolean(instance?.allowPip);
    elements.editorError.hidden = true;
    elements.editorError.textContent = '';
    elements.port.removeAttribute('aria-invalid');
    elements.editorOverlay.hidden = false;
    void loadEditorVersions(state.editorInstanceVersion);
    window.requestAnimationFrame(() => elements.name.focus());
  }

  function nextSuggestedPort() {
    const used = new Set(state.instances.map((item) => Number(item.port)));
    let port = 8000;
    while (used.has(port) && port < 65535) port += 1;
    return port;
  }

  function hideEditor() {
    elements.editorOverlay.hidden = true;
    state.versionRequest += 1;
    state.versionLoading = false;
    state.editingId = '';
    state.lastFocusedElement?.focus?.();
  }

  function editorPayload() {
    const customVersion = elements.customVersion.value.trim().replace(/^v/i, '');
    const selectedOption = elements.versionSelect.selectedOptions[0];
    const selectedVersion = elements.versionSelect.value === CUSTOM_VERSION_VALUE
      ? customVersion
      : String(selectedOption?.dataset.version || '');
    return {
      name: elements.name.value,
      version: selectedVersion,
      imageSource: 'release',
      port: Number(elements.port.value),
      memory: elements.memory.value,
      maxConcurrent: Number(elements.concurrency.value),
      sessionTimeout: Number(elements.sessionTimeout.value),
      networkAccess: elements.network.checked,
      allowPip: elements.pip.checked,
    };
  }

  async function saveEditor(event) {
    event.preventDefault();
    if (state.versionLoading) {
      const message = tr('Loading published Code Execution releases…');
      elements.versionStatus.textContent = message;
      feedback(message, 'info');
      return;
    }
    if (!validateCustomVersion()) {
      elements.customVersion.reportValidity();
      return;
    }
    if (!elements.editorForm.reportValidity()) return;
    elements.editorSave.disabled = true;
    elements.editorCancel.disabled = true;
    elements.editorError.hidden = true;
    try {
      if (state.editingId) {
        const previous = instanceById(state.editingId);
        await api.save(state.editingId, editorPayload());
        if (previous?.status?.running) await api.start(state.editingId);
        feedback('Code Execution service saved', 'success');
      } else {
        let created;
        try {
          created = await api.create(editorPayload());
        } catch (error) {
          showEditorError(error);
          return;
        }
        hideEditor();
        feedback('Code Execution service created; preparing images', 'success');
        try {
          await api.start(created.id);
        } catch (_error) {
          feedback('Code Execution service was created but could not be started', 'error');
          return;
        }
      }
      hideEditor();
      await refresh({ quiet: true });
    } catch (error) {
      showEditorError(error);
    } finally {
      elements.editorSave.disabled = false;
      elements.editorCancel.disabled = false;
    }
  }

  async function runAction(instanceId, action, successMessage) {
    feedback('Working');
    try {
      await api[action](instanceId);
      feedback(successMessage, 'success');
    } catch (_error) {
      feedback('Code Execution action failed', 'error');
    } finally {
      await refresh({ quiet: true });
    }
  }

  function beginDeleteConfirmation(instanceId) {
    if (state.deleteConfirmTimer) window.clearTimeout(state.deleteConfirmTimer);
    state.deleteConfirmId = instanceId;
    feedback('Select Confirm delete to remove containers, sessions, settings, and the Redis volume.');
    state.deleteConfirmTimer = window.setTimeout(() => {
      state.deleteConfirmId = '';
      state.deleteConfirmTimer = null;
      render();
    }, 10000);
    render();
  }

  async function deleteInstance(instanceId) {
    if (state.deleteConfirmId !== instanceId) {
      beginDeleteConfirmation(instanceId);
      return;
    }
    state.deleteConfirmId = '';
    if (state.deleteConfirmTimer) window.clearTimeout(state.deleteConfirmTimer);
    state.deleteConfirmTimer = null;
    await runAction(instanceId, 'remove', 'Code Execution service deleted');
  }

  async function showLogs(instance) {
    state.lastFocusedElement = document.activeElement;
    elements.logsTitle.textContent = `${instance.name} — ${tr('Code Execution logs')}`;
    elements.logsOutput.textContent = tr('Loading logs');
    elements.logsOverlay.hidden = false;
    elements.logsClose.focus();
    try {
      elements.logsOutput.textContent = await api.logs(instance.id, 350);
    } catch (_error) {
      elements.logsOutput.textContent = tr('Could not load Code Execution logs');
    }
  }

  function hideLogs() {
    elements.logsOverlay.hidden = true;
    state.lastFocusedElement?.focus?.();
  }

  async function connect(instanceId) {
    try {
      await api.copyConnection(instanceId);
      feedback('Connection settings copied. Choose Paste from launcher on the Omlorix page.', 'success');
      await api.openOmlorixConnections();
    } catch (_error) {
      feedback('Could not prepare the Omlorix connection', 'error');
    }
  }

  async function checkUpdate(instanceId) {
    feedback('Checking for a Code Execution update');
    try {
      const result = await api.checkUpdate(instanceId);
      state.updateInfo.set(instanceId, result);
      feedback(
        result.updateAvailable
          ? 'A Code Execution update is available'
          : 'This Code Execution service is up to date',
        'success',
      );
      render();
    } catch (_error) {
      feedback('Could not check for a Code Execution update', 'error');
    }
  }

  async function handleCardAction(event) {
    const control = event.target.closest('button[data-action][data-instance-id]');
    if (!control) return;
    const instanceId = control.dataset.instanceId;
    const instance = instanceById(instanceId);
    if (!instance) return;
    switch (control.dataset.action) {
      case 'start': await runAction(instanceId, 'start', 'Code Execution service started'); break;
      case 'stop': await runAction(instanceId, 'stop', 'Code Execution service stopped'); break;
      case 'restart': await runAction(instanceId, 'restart', 'Code Execution service restarted'); break;
      case 'edit': showEditor(instance); break;
      case 'logs': await showLogs(instance); break;
      case 'connect': await connect(instanceId); break;
      case 'reveal':
        try {
          await api.reveal(instanceId);
        } catch (_error) {
          feedback('Code Execution action failed', 'error');
        }
        break;
      case 'check-update': await checkUpdate(instanceId); break;
      case 'update':
        await runAction(instanceId, 'update', 'Code Execution service updated');
        state.updateInfo.delete(instanceId);
        break;
      case 'delete': await deleteInstance(instanceId); break;
      default: break;
    }
  }

  function focusableElements(container) {
    return Array.from(container.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((element) => !element.hidden);
  }

  function trapDialogFocus(event, container) {
    if (event.key !== 'Tab') return;
    const focusable = focusableElements(container);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  elements.add.addEventListener('click', () => showEditor());
  elements.emptyAdd.addEventListener('click', () => showEditor());
  elements.refresh.addEventListener('click', () => refresh());
  elements.instances.addEventListener('click', (event) => { void handleCardAction(event); });
  elements.editorForm.addEventListener('submit', (event) => { void saveEditor(event); });
  elements.versionSelect.addEventListener('change', () => syncCustomVersionField({ focus: true }));
  elements.customVersion.addEventListener('input', validateCustomVersion);
  elements.port.addEventListener('input', () => {
    elements.port.removeAttribute('aria-invalid');
    if (!elements.editorError.hidden) {
      elements.editorError.hidden = true;
      elements.editorError.textContent = '';
    }
  });
  elements.versionRetry.addEventListener('click', () => {
    void loadEditorVersions(elements.customVersion.value.trim() || state.editorInstanceVersion);
  });
  elements.editorCancel.addEventListener('click', hideEditor);
  elements.editorOverlay.addEventListener('click', (event) => {
    if (event.target === elements.editorOverlay) hideEditor();
  });
  elements.logsClose.addEventListener('click', hideLogs);
  elements.logsOverlay.addEventListener('click', (event) => {
    if (event.target === elements.logsOverlay) hideLogs();
  });
  window.addEventListener('keydown', (event) => {
    if (!elements.editorOverlay.hidden) {
      if (event.key === 'Escape') hideEditor();
      else trapDialogFocus(event, elements.editor);
      return;
    }
    if (!elements.logsOverlay.hidden) {
      if (event.key === 'Escape') hideLogs();
      else trapDialogFocus(event, elements.logsDialog);
    }
  });

  api.onOperationStart((payload) => {
    const instance = instanceById(payload.instanceId);
    if (instance) instance.busy = true;
    feedback('Working');
    render();
  });
  api.onOperationOutput((payload) => {
    if (!elements.logsOverlay.hidden && payload.text) {
      elements.logsOutput.textContent += payload.text;
    }
  });
  api.onOperationEnd((payload) => {
    const instance = instanceById(payload.instanceId);
    if (instance) instance.busy = false;
    if (!payload.ok) feedback('Code Execution action failed', 'error');
    void refresh({ quiet: true });
  });

  render();
  void refresh({ quiet: true });
  window.setInterval(() => {
    if (!document.hidden && elements.editorOverlay.hidden && elements.logsOverlay.hidden) {
      void refresh({ quiet: true });
    }
  }, 10000);
})();
