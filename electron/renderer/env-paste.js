(function envPasteModule(root) {
  /**
   * Return clipboard text from a paste event when the renderer is allowed to
   * read it. Electron can occasionally skip the native edit command, but the
   * paste event itself still carries safe, user-initiated clipboard data.
   */
  function clipboardTextFromEvent(event) {
    const clipboardData = event && event.clipboardData;
    if (!clipboardData || typeof clipboardData.getData !== 'function') {
      return null;
    }

    const plainText = clipboardData.getData('text/plain');
    if (plainText) return plainText;

    const legacyText = clipboardData.getData('text');
    return legacyText || null;
  }

  /**
   * Replace the selected text inside an input or textarea, preserving the same
   * cursor behavior users expect from a normal paste operation.
   */
  function replaceSelectedText(control, text) {
    const currentValue = String(control.value || '');
    const selectionStart = Number.isInteger(control.selectionStart)
      ? control.selectionStart
      : currentValue.length;
    const selectionEnd = Number.isInteger(control.selectionEnd)
      ? control.selectionEnd
      : selectionStart;
    const start = Math.max(0, Math.min(selectionStart, currentValue.length));
    const end = Math.max(start, Math.min(selectionEnd, currentValue.length));

    if (typeof control.setRangeText === 'function') {
      control.setRangeText(text, start, end, 'end');
      return;
    }

    control.value = `${currentValue.slice(0, start)}${text}${currentValue.slice(end)}`;
    const cursor = start + text.length;
    if (typeof control.setSelectionRange === 'function') {
      control.setSelectionRange(cursor, cursor);
    }
  }

  /**
   * Manually apply a paste event to a text control. Returning true means the
   * event was handled and a synthetic input event was dispatched for app state.
   */
  function applyTextPaste(event, control) {
    if (!control || typeof control.value === 'undefined') return false;

    const text = clipboardTextFromEvent(event);
    if (text === null) return false;

    if (event && typeof event.preventDefault === 'function') {
      event.preventDefault();
    }
    replaceSelectedText(control, text);

    if (typeof control.dispatchEvent === 'function' && root && typeof root.Event === 'function') {
      control.dispatchEvent(new root.Event('input', { bubbles: true }));
    }
    return true;
  }

  const api = {
    applyTextPaste,
    clipboardTextFromEvent,
    replaceSelectedText,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.omlorixEnvEditorPaste = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
