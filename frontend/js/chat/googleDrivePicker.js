(() => {
  'use strict';

  const GOOGLE_PICKER_SCRIPT_URL = 'https://apis.google.com/js/api.js';
  const GOOGLE_PICKER_SCRIPT_ID = 'omlorixGooglePickerApi';
  const GOOGLE_DRIVE_IMPORT_LIMIT = 20;
  const GOOGLE_PICKER_LOAD_TIMEOUT_MS = 15000;

  let pickerApiPromise = null;
  let activePicker = null;
  let activeSelectionPromise = null;

  function pickerT(key, fallback) {
    if (typeof window.getTranslation === 'function') {
      return window.getTranslation(key, fallback);
    }
    return fallback;
  }

  function pickerError(code, fallback) {
    const error = new Error(fallback);
    error.code = code;
    return error;
  }

  function loadGoogleApiScript() {
    if (window.gapi?.load) {
      return Promise.resolve();
    }

    return new Promise((resolve, reject) => {
      const existingScript = document.getElementById(GOOGLE_PICKER_SCRIPT_ID);
      const script = existingScript || document.createElement('script');
      let timeoutId = null;

      const cleanup = () => {
        window.clearTimeout(timeoutId);
        script.removeEventListener('load', handleLoad);
        script.removeEventListener('error', handleError);
      };
      const handleLoad = () => {
        cleanup();
        if (window.gapi?.load) {
          resolve();
          return;
        }
        reject(pickerError('picker_api_unavailable', 'Google Picker API did not initialize.'));
      };
      const handleError = () => {
        cleanup();
        script.remove();
        reject(pickerError('picker_script_failed', 'Google Picker could not be loaded.'));
      };

      script.addEventListener('load', handleLoad, { once: true });
      script.addEventListener('error', handleError, { once: true });
      timeoutId = window.setTimeout(() => {
        cleanup();
        // Remove a stalled loader so the next user action can make a clean retry.
        if (!window.gapi?.load) script.remove();
        reject(pickerError('picker_script_timeout', 'Google Picker took too long to load.'));
      }, GOOGLE_PICKER_LOAD_TIMEOUT_MS);

      if (!existingScript) {
        script.id = GOOGLE_PICKER_SCRIPT_ID;
        script.src = GOOGLE_PICKER_SCRIPT_URL;
        script.async = true;
        script.defer = true;
        script.referrerPolicy = 'strict-origin-when-cross-origin';
        document.head.appendChild(script);
      }
    });
  }

  function loadPickerApi() {
    if (window.google?.picker?.PickerBuilder) {
      return Promise.resolve();
    }
    if (pickerApiPromise) {
      return pickerApiPromise;
    }

    pickerApiPromise = loadGoogleApiScript()
      .then(() => new Promise((resolve, reject) => {
        window.gapi.load('picker', {
          callback: () => {
            if (window.google?.picker?.PickerBuilder) {
              resolve();
              return;
            }
            reject(pickerError('picker_api_unavailable', 'Google Picker API did not initialize.'));
          },
          onerror: () => reject(pickerError('picker_api_failed', 'Google Picker API could not be loaded.')),
          timeout: GOOGLE_PICKER_LOAD_TIMEOUT_MS,
          ontimeout: () => reject(pickerError('picker_api_timeout', 'Google Picker API took too long to load.')),
        });
      }))
      .catch((error) => {
        // A later click should be able to retry after a temporary network error.
        pickerApiPromise = null;
        throw error;
      });
    return pickerApiPromise;
  }

  async function createPickerSession() {
    const response = await window.authedFetch('/api/v1/files/google-drive/picker-session', {
      method: 'POST',
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
      },
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw pickerError(
        'picker_session_failed',
        payload?.detail || `Google Picker session failed (${response.status}).`
      );
    }
    if (payload?.picker_ready !== true) {
      throw pickerError(
        String(payload?.error_code || 'picker_not_configured'),
        'Google Picker is not configured.'
      );
    }
    if (payload?.connected !== true || !payload?.access_token) {
      throw pickerError(
        String(payload?.error_code || 'drive_not_connected'),
        'Google Drive must be reconnected.'
      );
    }
    if (!payload?.developer_key || !payload?.app_id) {
      throw pickerError('picker_not_configured', 'Google Picker configuration is incomplete.');
    }
    return payload;
  }

  function resolvePickerLocale() {
    const locale = String(document.documentElement.lang || navigator.language || 'en').trim();
    return locale || 'en';
  }

  function disposeActivePicker() {
    if (!activePicker) return;
    try {
      activePicker.dispose?.();
    } catch (_) {
      try {
        activePicker.setVisible?.(false);
      } catch (_) {}
    }
    activePicker = null;
  }

  function showNativePicker(session) {
    return new Promise((resolve, reject) => {
      const pickerApi = window.google?.picker;
      if (!pickerApi?.PickerBuilder) {
        reject(pickerError('picker_api_unavailable', 'Google Picker API is unavailable.'));
        return;
      }

      let dismissalWatcherId = null;
      let pickerDialogWasVisible = false;
      let settled = false;

      // Google Picker can close itself without delivering its CANCEL callback
      // (for example, when its popup is dismissed by the browser). Track its
      // dialog so the calling flow never remains pending in that case.
      const stopDismissalWatcher = () => {
        if (dismissalWatcherId === null) return;
        window.clearInterval(dismissalWatcherId);
        dismissalWatcherId = null;
      };

      const settle = (callback) => {
        if (settled) return;
        settled = true;
        stopDismissalWatcher();
        disposeActivePicker();
        callback();
      };

      const startDismissalWatcher = (picker) => {
        if (typeof window.setInterval !== 'function') return;
        dismissalWatcherId = window.setInterval(() => {
          if (activePicker !== picker) {
            stopDismissalWatcher();
            return;
          }
          if (document.querySelector('.picker-dialog')) {
            pickerDialogWasVisible = true;
            return;
          }
          if (pickerDialogWasVisible) {
            settle(() => resolve({ fileIds: [], documents: [], cancelled: true }));
          }
        }, 250);
      };

      // Keep a regular Drive view first so the picker opens in the user's
      // familiar "My Drive" file tree. Google Picker's setEnableDrives(true)
      // does not add shared drives to a regular view: it changes that view to
      // show *only* shared drives. Using it on the sole view therefore makes a
      // personal Drive look empty for users who belong to no shared drive.
      // Folders remain navigation targets but cannot be returned as selections
      // because Omlorix's import endpoint accepts files only.
      const myDriveView = new pickerApi.DocsView(pickerApi.ViewId.DOCS)
        .setIncludeFolders(true)
        .setSelectFolderEnabled(false);

      // Shared drives are still available as a separate native Picker view.
      // Keeping this isolated is required by the Picker API and preserves the
      // default personal-Drive route and its folder/search behavior.
      const sharedDrivesView = new pickerApi.DocsView(pickerApi.ViewId.DOCS)
        .setIncludeFolders(true)
        .setSelectFolderEnabled(false)
        .setEnableDrives(true);

      const handlePickerAction = (data) => {
        const action = data?.[pickerApi.Response.ACTION] || data?.action;
        if (action === pickerApi.Action.PICKED) {
          const documents = data?.[pickerApi.Response.DOCUMENTS] || data?.docs || [];
          const seenIds = new Set();
          const fileIds = [];
          for (const documentItem of documents) {
            const fileId = String(documentItem?.[pickerApi.Document.ID] || documentItem?.id || '').trim();
            if (!fileId || seenIds.has(fileId)) continue;
            seenIds.add(fileId);
            fileIds.push(fileId);
          }
          if (!fileIds.length) {
            settle(() => reject(pickerError('picker_empty_selection', 'Google Picker returned no files.')));
            return;
          }
          settle(() => resolve({ fileIds, documents }));
          return;
        }
        if (action === pickerApi.Action.CANCEL) {
          settle(() => resolve({ fileIds: [], documents: [], cancelled: true }));
        }
      };

      try {
        activePicker = new pickerApi.PickerBuilder()
          .addView(myDriveView)
          .addView(sharedDrivesView)
          .enableFeature(pickerApi.Feature.MULTISELECT_ENABLED)
          .setMaxItems(GOOGLE_DRIVE_IMPORT_LIMIT)
          .setOAuthToken(session.access_token)
          .setDeveloperKey(session.developer_key)
          .setAppId(session.app_id)
          .setOrigin(window.location.origin)
          .setLocale(resolvePickerLocale())
          .setTitle(pickerT('chat_files_add_google_drive', 'Add from Google Drive'))
          .setCallback(handlePickerAction)
          .build();
        activePicker.setVisible(true);
        startDismissalWatcher(activePicker);
      } catch (error) {
        stopDismissalWatcher();
        disposeActivePicker();
        reject(error);
      }
    });
  }

  async function open() {
    if (activeSelectionPromise) {
      return activeSelectionPromise;
    }

    const lastFocusedElement = document.activeElement;
    activeSelectionPromise = Promise.all([
      createPickerSession(),
      loadPickerApi(),
    ])
      .then(([session]) => showNativePicker(session))
      .finally(() => {
        activeSelectionPromise = null;
        lastFocusedElement?.focus?.({ preventScroll: true });
      });
    return activeSelectionPromise;
  }

  window.GoogleDrivePicker = Object.freeze({
    open,
  });
})();
