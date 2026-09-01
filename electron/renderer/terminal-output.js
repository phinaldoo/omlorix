/*
 * Render streamed command output into a plain-text launcher log.
 *
 * Child-process "data" events are arbitrary chunks, not logical lines. A
 * download may therefore emit "10%\r", "20%\r", and "30%\n" in separate
 * events, while Docker Compose additionally moves the cursor to redraw a
 * multi-line progress block. Appending a newline for every event turns that
 * compact terminal output into hundreds of lines.
 *
 * The launcher does not need a fully interactive terminal emulator, but it
 * does need the small, deterministic subset below. Keeping this renderer
 * shared makes the setup log and Console page behave identically.
 */
(function exposeTerminalOutput(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.OmlorixTerminalOutput = api;
}(typeof window !== 'undefined' ? window : globalThis, () => {
  const buffers = new WeakMap();

  /** Create a cursor buffer from text that may already be present in a log. */
  function createBuffer(text = '') {
    const lines = String(text).split('\n');
    return {
      lines,
      row: lines.length - 1,
      column: lines[lines.length - 1].length,
      pendingControl: '',
      replaceLineOnWrite: false,
      savedCursor: null,
    };
  }

  function renderedText(buffer) {
    return buffer.lines.join('\n');
  }

  /**
   * Adopt external textContent changes, such as the Console's Clear button.
   * This keeps callers free to reset their element without retaining stale
   * cursor state in the WeakMap.
   */
  function bufferFor(element) {
    let buffer = buffers.get(element);
    if (!buffer || element.textContent !== renderedText(buffer)) {
      buffer = createBuffer(element.textContent || '');
      buffers.set(element, buffer);
    }
    return buffer;
  }

  function ensureRow(buffer, row) {
    while (buffer.lines.length <= row) buffer.lines.push('');
  }

  /** Write one visible character at the emulated cursor. */
  function writeCharacter(buffer, character) {
    ensureRow(buffer, buffer.row);
    if (buffer.replaceLineOnWrite) {
      // Progress writers use carriage return as "replace this status line".
      // Deferring the clear preserves normal CRLF line endings.
      buffer.lines[buffer.row] = '';
      buffer.column = 0;
      buffer.replaceLineOnWrite = false;
    }
    const line = buffer.lines[buffer.row];
    const padded = line.padEnd(buffer.column, ' ');
    buffer.lines[buffer.row] = `${padded.slice(0, buffer.column)}${character}${padded.slice(buffer.column + 1)}`;
    buffer.column += character.length;
  }

  function positiveParameter(parameters, index = 0, fallback = 1) {
    const parsed = Number.parseInt(parameters[index], 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
  }

  /** Apply the ANSI CSI commands used by Docker and common progress tools. */
  function applyCsi(buffer, body, command) {
    // Private-mode prefixes (for example ?25l to hide the cursor) do not
    // affect the launcher's text-only representation.
    const normalizedBody = body.replace(/^[?>!]/, '');
    const parameters = normalizedBody.split(';');
    const amount = positiveParameter(parameters);

    if (command === 'A') {
      buffer.row = Math.max(0, buffer.row - amount);
    } else if (command === 'B') {
      buffer.row += amount;
      ensureRow(buffer, buffer.row);
    } else if (command === 'C') {
      buffer.column += amount;
    } else if (command === 'D') {
      buffer.column = Math.max(0, buffer.column - amount);
    } else if (command === 'E') {
      buffer.row += amount;
      buffer.column = 0;
      ensureRow(buffer, buffer.row);
    } else if (command === 'F') {
      buffer.row = Math.max(0, buffer.row - amount);
      buffer.column = 0;
    } else if (command === 'G') {
      buffer.column = amount - 1;
    } else if (command === 'H' || command === 'f') {
      buffer.row = positiveParameter(parameters, 0) - 1;
      buffer.column = positiveParameter(parameters, 1) - 1;
      ensureRow(buffer, buffer.row);
    } else if (command === 'K') {
      const mode = Number.parseInt(parameters[0] || '0', 10);
      const line = buffer.lines[buffer.row] || '';
      if (mode === 1) {
        buffer.lines[buffer.row] = `${' '.repeat(buffer.column)}${line.slice(buffer.column + 1)}`;
      } else if (mode === 2) {
        buffer.lines[buffer.row] = '';
      } else {
        buffer.lines[buffer.row] = line.slice(0, buffer.column);
      }
      buffer.replaceLineOnWrite = false;
    } else if (command === 'J') {
      const mode = Number.parseInt(parameters[0] || '0', 10);
      if (mode === 2 || mode === 3) {
        buffer.lines = [''];
        buffer.row = 0;
        buffer.column = 0;
      } else if (mode === 1) {
        buffer.lines = buffer.lines.slice(buffer.row);
        buffer.row = 0;
      } else {
        buffer.lines = buffer.lines.slice(0, buffer.row + 1);
        buffer.lines[buffer.row] = (buffer.lines[buffer.row] || '').slice(0, buffer.column);
      }
      buffer.replaceLineOnWrite = false;
    } else if (command === 's') {
      buffer.savedCursor = { row: buffer.row, column: buffer.column };
    } else if (command === 'u' && buffer.savedCursor) {
      buffer.row = buffer.savedCursor.row;
      buffer.column = buffer.savedCursor.column;
      ensureRow(buffer, buffer.row);
    }
    // SGR styling (m), cursor visibility (h/l), and terminal queries have no
    // visible meaning inside a plain <pre>, so they are intentionally ignored.
  }

  /** Return the complete control sequence length, or zero for a split chunk. */
  function controlSequenceLength(source, start) {
    if (source[start] !== '\x1b') return 0;
    if (start + 1 >= source.length) return 0;
    const introducer = source[start + 1];
    if (introducer === '[') {
      for (let index = start + 2; index < source.length; index += 1) {
        const code = source.charCodeAt(index);
        if (code >= 0x40 && code <= 0x7e) return index - start + 1;
      }
      return 0;
    }
    if (introducer === ']') {
      for (let index = start + 2; index < source.length; index += 1) {
        if (source[index] === '\x07') return index - start + 1;
        if (source[index] === '\x1b' && source[index + 1] === '\\') return index - start + 2;
      }
      return 0;
    }
    return 2;
  }

  /** Apply a raw process chunk without inventing any line breaks. */
  function write(buffer, incomingText) {
    const source = `${buffer.pendingControl}${String(incomingText || '')}`;
    buffer.pendingControl = '';

    for (let index = 0; index < source.length;) {
      const character = source[index];
      if (character === '\x1b') {
        const length = controlSequenceLength(source, index);
        if (!length) {
          buffer.pendingControl = source.slice(index);
          break;
        }
        const sequence = source.slice(index, index + length);
        if (sequence[1] === '[') applyCsi(buffer, sequence.slice(2, -1), sequence.at(-1));
        index += length;
        continue;
      }
      if (character === '\r') {
        buffer.column = 0;
        buffer.replaceLineOnWrite = true;
        index += 1;
        continue;
      }
      if (character === '\n') {
        buffer.row += 1;
        buffer.column = 0;
        buffer.replaceLineOnWrite = false;
        ensureRow(buffer, buffer.row);
        index += 1;
        continue;
      }
      if (character === '\b') {
        buffer.column = Math.max(0, buffer.column - 1);
        buffer.replaceLineOnWrite = false;
        index += 1;
        continue;
      }
      // Ignore remaining C0 controls while preserving tabs as readable spacing.
      if (character === '\t') {
        const spaces = 8 - (buffer.column % 8);
        for (let offset = 0; offset < spaces; offset += 1) writeCharacter(buffer, ' ');
        index += 1;
        continue;
      }
      if (source.charCodeAt(index) < 0x20 || character === '\x7f') {
        index += 1;
        continue;
      }

      const codePoint = source.codePointAt(index);
      const visibleCharacter = String.fromCodePoint(codePoint);
      writeCharacter(buffer, visibleCharacter);
      index += visibleCharacter.length;
    }
  }

  /** Keep long-running diagnostic streams bounded without breaking the tail. */
  function trimBuffer(buffer, { maxLines = 0, maxCharacters = 0 } = {}) {
    const lineLimit = Number.isInteger(maxLines) && maxLines > 0 ? maxLines : 0;
    const characterLimit = Number.isInteger(maxCharacters) && maxCharacters > 0
      ? maxCharacters
      : 0;
    let removedRows = 0;
    if (lineLimit && buffer.lines.length > lineLimit) {
      removedRows = buffer.lines.length - lineLimit;
      buffer.lines.splice(0, removedRows);
    }

    if (characterLimit) {
      let overflow = renderedText(buffer).length - characterLimit;
      while (overflow > 0 && buffer.lines.length > 1) {
        const removedLength = buffer.lines[0].length + 1;
        buffer.lines.shift();
        removedRows += 1;
        overflow -= removedLength;
      }
      if (overflow > 0) {
        buffer.lines[0] = buffer.lines[0].slice(overflow);
        if (buffer.row - removedRows === 0) {
          buffer.column = Math.max(0, buffer.column - overflow);
        }
      }
    }

    if (removedRows) {
      buffer.row = Math.max(0, buffer.row - removedRows);
      if (buffer.savedCursor) {
        buffer.savedCursor.row = Math.max(0, buffer.savedCursor.row - removedRows);
      }
    }
    ensureRow(buffer, buffer.row);
    buffer.column = Math.min(buffer.column, buffer.lines[buffer.row].length);
  }

  /**
   * Append terminal output to an element.
   *
   * `separate` is for launcher-authored messages such as operation headings.
   * Raw stdout/stderr must leave it false so chunk boundaries remain invisible.
   */
  function append(element, text, { separate = false, maxLines = 0, maxCharacters = 0 } = {}) {
    if (!element || text === undefined || text === null || text === '') return;
    const buffer = bufferFor(element);
    let source = String(text);
    if (separate) {
      // A completed launcher message belongs after any Docker redraw region,
      // even if the last ANSI command left its cursor on an earlier row.
      // A process that ended halfway through an escape sequence must not let
      // that fragment consume characters from the launcher's own summary.
      buffer.pendingControl = '';
      buffer.row = buffer.lines.length - 1;
      buffer.column = buffer.lines[buffer.row].length;
      buffer.replaceLineOnWrite = false;
      if (renderedText(buffer) && !renderedText(buffer).endsWith('\n') && !source.startsWith('\n')) {
        source = `\n${source}`;
      }
    }
    write(buffer, source);
    trimBuffer(buffer, { maxLines, maxCharacters });
    element.textContent = renderedText(buffer);
    element.scrollTop = element.scrollHeight;
  }

  /** Reset both visible text and cursor/parser state. */
  function clear(element) {
    if (!element) return;
    element.textContent = '';
    buffers.delete(element);
  }

  return { append, clear };
}));
