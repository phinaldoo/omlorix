/* ==========================================================================
   Canvas Files Dropdown — Header button that lists generated files per chat
   ========================================================================== */

(function () {
    'use strict';

    const _wrap     = document.getElementById('headerCanvasButtonWrap');
    const _btn      = document.getElementById('headerCanvasButton');
    const _dropdown = document.getElementById('canvasFilesDropdown');

    // Map of fileId → { title, type, onOpen }
    // type: 'slide-presentation' | 'canvas-markdown' | 'latex-pdf' | 'note' | (extensible)
    let _files = [];
    let _isOpen = false;
    const _dropdownController = window.createDropdownController?.({
        id: 'canvas-files-dropdown',
        trigger: _btn,
        dropdown: _dropdown,
        root: _wrap,
        bindTrigger: false,
        escapePriority: 55,
        onToggle: ({ isOpen }) => {
            _isOpen = isOpen;
        },
    });

    function _t(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    // ── Visibility ────────────────────────────────────────────────────────

    function _syncVisibility() {
        if (!_wrap) return;
        _wrap.style.display = _files.length > 0 ? 'flex' : 'none';
    }

    // ── Dropdown open/close ───────────────────────────────────────────────

    function _openDropdown() {
        _dropdownController?.open({ reason: 'api' });
    }

    function _closeDropdown() {
        _dropdownController?.close({ reason: 'api' });
    }

    function _toggleDropdown() {
        if (_isOpen) {
            _closeDropdown();
        } else {
            _renderDropdown();
            _openDropdown();
        }
    }

    // ── Render ────────────────────────────────────────────────────────────

    function _iconForType(type) {
        if (type === 'slide-presentation') {
            return Icons.desktop;
        }
        if (type === 'canvas' || type === 'canvas-markdown') {
            return Icons.file;
        }
        if (type === 'latex-pdf') {
            return Icons.file;
        }
        if (type === 'note') {
            return Icons.file;
        }
        return Icons.file;
    }

    function _labelForType(type) {
        if (type === 'slide-presentation') return _t('canvas_files_type_presentation', 'Presentation');
        if (type === 'canvas' || type === 'canvas-markdown') return _t('canvas_files_type_canvas', 'Canvas');
        if (type === 'latex-pdf') return _t('canvas_files_type_latex_pdf', 'LaTeX PDF');
        if (type === 'note') return _t('canvas_files_type_note', 'Note');
        return _t('canvas_files_type_file', 'File');
    }

    function _renderDropdown() {
        if (!_dropdown) return;
        _dropdown.innerHTML = '';

        if (_files.length === 0) return;

        const label = document.createElement('div');
        label.className = 'canvas-files-dropdown-label';
        label.textContent = _t('canvas_files_dropdown_title', 'Generated Files');
        _dropdown.appendChild(label);

        _files.forEach(function (file) {
            const btn = document.createElement('button');
            btn.className = 'canvas-files-dropdown-item';
            btn.type = 'button';
            btn.innerHTML =
                '<div class="canvas-files-dropdown-item-icon">' + _iconForType(file.type) + '</div>' +
                '<div class="canvas-files-dropdown-item-info">' +
                    '<div class="canvas-files-dropdown-item-name">' + _escHtml(file.title || _t('canvas_files_untitled', 'Untitled')) + '</div>' +
                    '<div class="canvas-files-dropdown-item-type">' + _labelForType(file.type) + '</div>' +
                '</div>';

            btn.addEventListener('click', function () {
                _closeDropdown();
                if (typeof file.onOpen === 'function') {
                    file.onOpen();
                }
            });

            _dropdown.appendChild(btn);
        });
    }

    // ── Public API ────────────────────────────────────────────────────────

    /**
     * Register a generated file so it appears in the dropdown.
     * @param {string} id       Unique identifier (e.g. presentation_id or file_id)
     * @param {string} title    Display name
     * @param {string} type     'slide-presentation' | ...
     * @param {Function} onOpen Called when the user clicks the item
     */
    function registerFile(id, title, type, onOpen) {
        // Avoid duplicates
        const existing = _files.findIndex(function (f) { return f.id === id; });
        if (existing !== -1) {
            _files[existing] = { id: id, title: title, type: type, onOpen: onOpen };
        } else {
            _files.push({ id: id, title: title, type: type, onOpen: onOpen });
        }
        _syncVisibility();
    }

    /**
     * Remove a file from the dropdown by id.
     */
    function unregisterFile(id) {
        _files = _files.filter(function (f) { return f.id !== id; });
        _syncVisibility();
        if (_files.length === 0) {
            _closeDropdown();
        }
    }

    /**
     * Remove all files (called when switching chats).
     */
    function clearFiles() {
        _files = [];
        _syncVisibility();
        _closeDropdown();
    }

    // ── Event wiring ──────────────────────────────────────────────────────

    if (_btn) {
        _btn.addEventListener('click', function (e) {
            e.stopPropagation();
            _toggleDropdown();
        });
    }

    // ── Helpers ───────────────────────────────────────────────────────────

    function _escHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // ── Expose ────────────────────────────────────────────────────────────

    window.canvasFilesDropdown = {
        registerFile:   registerFile,
        unregisterFile: unregisterFile,
        clearFiles:     clearFiles,
        close:          _closeDropdown,
    };

})();
