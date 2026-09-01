(() => {
    'use strict';

    if (window.ChatSpreadsheetEditor) return;

    const ROW_HEIGHT = 30;
    const HEADER_HEIGHT = 32;
    const ROW_NUMBER_WIDTH = 48;
    const DEFAULT_COLUMN_WIDTH = 128;
    const MIN_COLUMN_WIDTH = 52;
    const MAX_COLUMN_WIDTH = 520;
    const MIN_VISIBLE_ROWS = 100;
    const MIN_VISIBLE_COLUMNS = 26;
    const MAX_SEARCH_MATCHES = 20000;
    const MAX_STATISTICS_CELLS = 50000;
    const FORMAT_MIME_TYPES = {
        csv: 'text/csv;charset=utf-8',
        tsv: 'text/tab-separated-values;charset=utf-8',
        xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        xls: 'application/vnd.ms-excel',
    };
    const FEATURE_TRANSLATIONS = {
        workbook_structure: ['spreadsheet_feature_workbook_structure', 'workbook structure'],
        formulas: ['spreadsheet_feature_formulas', 'formulas'],
        merged_cells: ['spreadsheet_feature_merged_cells', 'merged cells'],
        macros: ['spreadsheet_feature_macros', 'macros'],
        charts: ['spreadsheet_feature_charts', 'charts'],
        pivot_tables: ['spreadsheet_feature_pivot_tables', 'pivot tables'],
        drawings: ['spreadsheet_feature_drawings', 'drawings'],
        external_links: ['spreadsheet_feature_external_links', 'external links'],
        slicers: ['spreadsheet_feature_slicers', 'slicers'],
        uninspected_package: ['spreadsheet_feature_uninspected_package', 'unverified workbook features'],
    };
    let editorInstanceSequence = 0;

    function editorT(key, fallback, vars = {}) {
        const translated = typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback;
        return String(translated ?? fallback).replace(/\{(\w+)\}/g, (_, name) => {
            const value = vars[name];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    function icon(name) {
        const icons = typeof Icons === 'object' ? Icons : (globalThis.Icons || {});
        return icons?.[name] || '';
    }

    function normalizeFormat(format, fileName = '') {
        const explicit = String(format || '').trim().toLowerCase();
        if (Object.prototype.hasOwnProperty.call(FORMAT_MIME_TYPES, explicit)) return explicit;
        const match = String(fileName || '').toLowerCase().match(/\.([a-z0-9]+)$/);
        return match && Object.prototype.hasOwnProperty.call(FORMAT_MIME_TYPES, match[1])
            ? match[1]
            : 'csv';
    }

    function colName(index) {
        let value = Number(index) + 1;
        let result = '';
        while (value > 0) {
            value -= 1;
            result = String.fromCharCode(65 + (value % 26)) + result;
            value = Math.floor(value / 26);
        }
        return result;
    }

    function cloneCell(cell) {
        if (!cell) return null;
        const copy = { ...cell };
        if (cell.v instanceof Date) copy.v = new Date(cell.v.getTime());
        if (Array.isArray(cell.r)) copy.r = cell.r.map((run) => ({ ...run }));
        return copy;
    }

    function cellsEqual(left, right) {
        if (!left && !right) return true;
        if (!left || !right) return false;
        const comparable = (cell) => ({
            t: cell.t || '',
            v: cell.v instanceof Date ? cell.v.toISOString() : cell.v,
            f: cell.f || '',
            F: cell.F || '',
            z: cell.z || '',
            s: cell.s,
        });
        return JSON.stringify(comparable(left)) === JSON.stringify(comparable(right));
    }

    function displayCell(cell) {
        if (!cell) return '';
        if (cell.w !== undefined && cell.w !== null) return String(cell.w);
        if (cell.v instanceof Date) return cell.v.toLocaleDateString();
        if (cell.v !== undefined && cell.v !== null) return String(cell.v);
        return cell.f ? `=${cell.f}` : '';
    }

    function editCellValue(cell) {
        if (!cell) return '';
        if (cell.f) return `=${cell.f}`;
        if (cell.v instanceof Date) return cell.v.toISOString().slice(0, 10);
        return cell.v === undefined || cell.v === null ? '' : String(cell.v);
    }

    function inferCell(text, previousCell = null) {
        const raw = String(text ?? '');
        const preserved = previousCell ? { ...previousCell } : {};
        delete preserved.w;
        delete preserved.r;
        delete preserved.h;
        delete preserved.f;
        delete preserved.F;
        if (!raw) return null;
        if (raw.startsWith('=') && raw.length > 1) {
            return { ...preserved, t: previousCell?.t || 'n', f: raw.slice(1), v: undefined };
        }
        const normalized = raw.trim();
        if (/^(true|false)$/i.test(normalized)) {
            return { ...preserved, t: 'b', v: normalized.toLowerCase() === 'true' };
        }
        if (normalized !== '' && Number.isFinite(Number(normalized))) {
            return { ...preserved, t: 'n', v: Number(normalized) };
        }
        return { ...preserved, t: 's', v: raw };
    }

    function safeFileStem(fileName) {
        return (String(fileName || 'spreadsheet').replace(/\.[^.]+$/, '') || 'spreadsheet')
            .replace(/[\\/:*?"<>|]/g, '_');
    }

    function xmlLocalElements(parent, localName) {
        return Array.from(parent.getElementsByTagName('*'))
            .filter((node) => node.localName === localName);
    }

    class SpreadsheetEditor {
        constructor(options = {}) {
            this.options = options;
            this.instanceId = ++editorInstanceSequence;
            this.format = normalizeFormat(options.format, options.fileName);
            this.fileName = String(options.fileName || `spreadsheet.${this.format}`);
            this.editable = options.editable !== false;
            this.originalBytes = options.data instanceof ArrayBuffer
                ? options.data.slice(0)
                : (ArrayBuffer.isView(options.data)
                    ? options.data.buffer.slice(options.data.byteOffset, options.data.byteOffset + options.data.byteLength)
                    : null);
            this.workbook = null;
            this.activeSheetIndex = 0;
            this.selection = { ar: 0, ac: 0, hr: 0, hc: 0 };
            this.editing = null;
            this.undoStack = [];
            this.redoStack = [];
            this.dirty = false;
            this.destroyed = false;
            this.renderQueued = false;
            this.columnWidths = new Map();
            this.editedColumnWidths = new Map();
            this.extraDimensions = new Map();
            this.editedCells = new Map();
            this.dragState = null;
            this.searchMatches = [];
            this.searchIndex = -1;
            this.searchQuery = '';
            this.advancedFeatures = [];
            this.compatibilityMode = false;
            // A saved workbook can still contain stale cached formula values:
            // SheetJS preserves expressions but does not execute them. The
            // backend persists this flag with browser-authored revisions so a
            // remounted editor remains conservative until a native spreadsheet
            // application recalculates the workbook.
            this.requiresRecalculation = options.requiresRecalculation === true;
            this.listeners = [];
            this.element = this.buildShell();
        }

        async initialize() {
            if (!globalThis.XLSX) {
                throw new Error(editorT('spreadsheet_library_unavailable', 'Spreadsheet support is unavailable. Reload the page and try again.'));
            }
            this.workbook = this.readWorkbook();
            if (!this.workbook.SheetNames.length) {
                const sheet = XLSX.utils.aoa_to_sheet([[]]);
                XLSX.utils.book_append_sheet(this.workbook, sheet, editorT('spreadsheet_default_sheet_name', 'Sheet 1'));
            }
            await this.inspectNativeFeatures();
            this.bindEvents();
            this.refreshAll();
            return this;
        }

        readWorkbook() {
            const options = {
                cellFormula: true,
                cellNF: true,
                cellStyles: true,
                cellDates: true,
                bookVBA: true,
                sheetStubs: true,
                xlfn: true,
            };
            if (this.format === 'csv' || this.format === 'tsv') {
                const decoder = new TextDecoder('utf-8');
                const source = typeof this.options.text === 'string'
                    ? this.options.text
                    : decoder.decode(this.originalBytes || new ArrayBuffer(0));
                return XLSX.read(source, { ...options, type: 'string', raw: true });
            }
            return XLSX.read(this.originalBytes || new ArrayBuffer(0), { ...options, type: 'array' });
        }

        async inspectNativeFeatures() {
            const features = new Set();
            let formulaCount = 0;
            this.workbook.SheetNames.forEach((sheetName) => {
                const worksheet = this.workbook.Sheets[sheetName];
                Object.keys(worksheet || {}).forEach((address) => {
                    if (!address.startsWith('!') && worksheet[address]?.f) formulaCount += 1;
                });
                if (worksheet?.['!merges']?.length) features.add('merged_cells');
            });
            if (formulaCount) features.add('formulas');
            if (this.workbook.vbaraw) features.add('macros');

            if (this.format === 'xlsx' && this.originalBytes && globalThis.JSZip) {
                try {
                    const zip = await JSZip.loadAsync(this.originalBytes);
                    // Cell-level package patching retains worksheet metadata
                    // that SheetJS CE does not model, including validation,
                    // conditional formatting, tables, and named structures.
                    features.add('workbook_structure');
                    Object.keys(zip.files).forEach((path) => {
                        const normalized = path.toLowerCase();
                        if (normalized.startsWith('xl/charts/')) features.add('charts');
                        if (normalized.startsWith('xl/pivottables/') || normalized.startsWith('xl/pivotcache/')) features.add('pivot_tables');
                        if (normalized.startsWith('xl/drawings/')) features.add('drawings');
                        if (normalized.startsWith('xl/externallinks/')) features.add('external_links');
                        if (normalized.startsWith('xl/slicers/')) features.add('slicers');
                    });
                } catch (_) {
                    // Parsing through SheetJS already succeeded. Failure to
                    // inspect the package only disables the preservation path.
                    features.add('uninspected_package');
                }
            }
            this.advancedFeatures = Array.from(features);
            this.compatibilityMode = this.format === 'xlsx' && this.advancedFeatures.some((feature) => (
                feature !== 'formulas' && feature !== 'merged_cells'
            ));
        }

        buildShell() {
            const root = document.createElement('section');
            root.className = 'canvas-spreadsheet-editor';
            root.dataset.format = this.format;
            root.setAttribute('aria-label', editorT('spreadsheet_editor_aria', 'Spreadsheet editor'));
            root.innerHTML = `
                <div class="canvas-spreadsheet-toolbar" role="toolbar" aria-label="${editorT('spreadsheet_toolbar_aria', 'Spreadsheet editing tools')}">
                    <button class="canvas-spreadsheet-icon-btn" type="button" data-action="undo" aria-label="${editorT('spreadsheet_undo', 'Undo')}" title="${editorT('spreadsheet_undo', 'Undo')}">${icon('undo')}</button>
                    <button class="canvas-spreadsheet-icon-btn" type="button" data-action="redo" aria-label="${editorT('spreadsheet_redo', 'Redo')}" title="${editorT('spreadsheet_redo', 'Redo')}">${icon('redo')}</button>
                    <span class="canvas-spreadsheet-toolbar-separator" aria-hidden="true"></span>
                    <div class="canvas-spreadsheet-search">
                        <span aria-hidden="true">${icon('magnifyingGlass')}</span>
                        <input type="search" data-role="search" placeholder="${editorT('spreadsheet_find_placeholder', 'Find in sheet')}" aria-label="${editorT('spreadsheet_find_aria', 'Find in current sheet')}">
                        <span class="canvas-spreadsheet-search-count" data-role="search-count" aria-live="polite"></span>
                    </div>
                    <span class="canvas-spreadsheet-toolbar-spacer"></span>
                    <span class="canvas-spreadsheet-compatibility" data-role="compatibility" hidden></span>
                </div>
                <div class="canvas-spreadsheet-formula-bar">
                    <output class="canvas-spreadsheet-name-box" data-role="cell-name" aria-label="${editorT('spreadsheet_selected_cell', 'Selected cell')}">A1</output>
                    <span class="canvas-spreadsheet-fx" aria-hidden="true">fx</span>
                    <input class="canvas-spreadsheet-formula-input" data-role="formula" aria-label="${editorT('spreadsheet_formula_bar_aria', 'Cell value or formula')}" autocomplete="off" spellcheck="false">
                </div>
                <div class="canvas-spreadsheet-grid-wrap">
                    <div class="canvas-spreadsheet-viewport" data-role="viewport" role="grid" tabindex="0" aria-label="${editorT('spreadsheet_grid_aria', 'Spreadsheet cells')}">
                        <div class="canvas-spreadsheet-canvas" data-role="canvas">
                            <div class="canvas-spreadsheet-header" data-role="header" role="row"></div>
                            <div class="canvas-spreadsheet-rows" data-role="rows"></div>
                            <div class="canvas-spreadsheet-selection-layer" data-role="selection" aria-hidden="true"></div>
                        </div>
                    </div>
                </div>
                <div class="canvas-spreadsheet-tabs-bar" data-role="tabs-bar">
                    <div class="canvas-spreadsheet-sheet-controls" data-role="sheet-controls">
                        <div class="canvas-spreadsheet-tabs" data-role="tabs" role="tablist" aria-label="${editorT('spreadsheet_sheets_aria', 'Workbook sheets')}"></div>
                        <button class="canvas-spreadsheet-mini-btn" data-action="add-sheet" type="button" aria-label="${editorT('spreadsheet_add_sheet', 'Add sheet')}" title="${editorT('spreadsheet_add_sheet', 'Add sheet')}">${icon('plus')}</button>
                    </div>
                    <span class="canvas-spreadsheet-toolbar-spacer"></span>
                    <button class="canvas-spreadsheet-mini-btn" data-action="add-row" type="button">${editorT('spreadsheet_add_row', '+ Row')}</button>
                    <button class="canvas-spreadsheet-mini-btn" data-action="add-column" type="button">${editorT('spreadsheet_add_column', '+ Column')}</button>
                </div>
                <div class="canvas-spreadsheet-status" role="status">
                    <span data-role="selection-name">A1</span>
                    <span aria-hidden="true">·</span>
                    <span data-role="dimensions"></span>
                    <span class="canvas-spreadsheet-toolbar-spacer"></span>
                    <span data-role="statistics"></span>
                </div>
            `;
            return root;
        }

        get viewport() { return this.element.querySelector('[data-role="viewport"]'); }
        get canvas() { return this.element.querySelector('[data-role="canvas"]'); }
        get headerLayer() { return this.element.querySelector('[data-role="header"]'); }
        get rowsLayer() { return this.element.querySelector('[data-role="rows"]'); }
        get selectionLayer() { return this.element.querySelector('[data-role="selection"]'); }
        get activeSheetName() { return this.workbook.SheetNames[this.activeSheetIndex]; }
        get activeSheet() { return this.workbook.Sheets[this.activeSheetName]; }

        displayCellValue(cell) {
            // Cached formula results become stale after any precedent changes.
            // Show the expression until Excel/LibreOffice performs the native
            // recalculation requested during export instead of presenting an
            // old number as though it were current.
            if (this.requiresRecalculation && cell?.f) return `=${cell.f}`;
            return displayCell(cell);
        }

        listen(target, eventName, handler, options) {
            target.addEventListener(eventName, handler, options);
            this.listeners.push(() => target.removeEventListener(eventName, handler, options));
        }

        bindEvents() {
            const viewport = this.viewport;
            this.listen(viewport, 'scroll', () => this.scheduleRender(), { passive: true });
            this.listen(window, 'resize', () => this.scheduleRender(), { passive: true });
            this.listen(viewport, 'mousedown', (event) => this.handlePointerDown(event));
            this.listen(viewport, 'dblclick', (event) => this.handleDoubleClick(event));
            this.listen(viewport, 'selectstart', (event) => {
                // Cell inputs need normal caret/text selection while editing;
                // every other part of the grid uses the editor's range model.
                if (!event.target.closest?.('.canvas-spreadsheet-cell-editor')) {
                    event.preventDefault();
                }
            });
            this.listen(window, 'mousemove', (event) => this.handlePointerMove(event));
            this.listen(window, 'mouseup', () => this.finishPointerInteraction());
            this.listen(viewport, 'keydown', (event) => this.handleKeyDown(event));
            this.listen(viewport, 'copy', (event) => this.handleCopy(event));
            this.listen(viewport, 'cut', (event) => this.handleCut(event));
            this.listen(viewport, 'paste', (event) => this.handlePaste(event));

            this.element.querySelectorAll('[data-action]').forEach((button) => {
                this.listen(button, 'click', () => this.handleAction(button.dataset.action));
            });
            const search = this.element.querySelector('[data-role="search"]');
            this.listen(search, 'input', () => {
                this.searchQuery = search.value;
                this.runSearch(true);
            });
            this.listen(search, 'keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    this.gotoSearchMatch(this.searchIndex + (event.shiftKey ? -1 : 1));
                }
            });
            const formula = this.element.querySelector('[data-role="formula"]');
            this.listen(formula, 'keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    this.commitFormulaBar();
                    viewport.focus();
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    this.updateFormulaBar();
                    viewport.focus();
                }
            });
            this.listen(formula, 'change', () => this.commitFormulaBar());
        }

        dimensions(sheet = this.activeSheet) {
            const decoded = sheet?.['!ref'] ? XLSX.utils.decode_range(sheet['!ref']) : { e: { r: 0, c: 0 } };
            const extras = this.extraDimensions.get(this.activeSheetName) || { rows: 0, cols: 0 };
            return {
                usedRows: Math.max(1, decoded.e.r + 1),
                usedCols: Math.max(1, decoded.e.c + 1),
                rows: Math.max(MIN_VISIBLE_ROWS + extras.rows, decoded.e.r + 1),
                cols: Math.max(MIN_VISIBLE_COLUMNS + extras.cols, decoded.e.c + 1),
            };
        }

        getColumnWidths() {
            if (!this.columnWidths.has(this.activeSheetName)) {
                const { cols } = this.dimensions();
                const source = this.activeSheet?.['!cols'] || [];
                const widths = Array.from({ length: cols }, (_, index) => {
                    const configured = Number(source[index]?.wpx || source[index]?.width * 7 || DEFAULT_COLUMN_WIDTH);
                    return Math.max(MIN_COLUMN_WIDTH, Math.min(MAX_COLUMN_WIDTH, configured));
                });
                this.columnWidths.set(this.activeSheetName, widths);
            }
            const widths = this.columnWidths.get(this.activeSheetName);
            const { cols } = this.dimensions();
            while (widths.length < cols) widths.push(DEFAULT_COLUMN_WIDTH);
            return widths;
        }

        columnGeometry() {
            const widths = this.getColumnWidths();
            const offsets = [ROW_NUMBER_WIDTH];
            widths.forEach((width) => offsets.push(offsets[offsets.length - 1] + width));
            return { widths, offsets, total: offsets[offsets.length - 1] };
        }

        refreshAll() {
            this.viewport.setAttribute(
                'aria-activedescendant',
                `canvas-spreadsheet-${this.instanceId}-cell-${this.activeSheetIndex}-${this.selection.ar}-${this.selection.ac}`,
            );
            this.renderTabs();
            this.updateCompatibilityNotice();
            this.updateFormulaBar();
            this.updateStatus();
            this.updateToolbar();
            this.scheduleRender();
        }

        scheduleRender() {
            if (this.renderQueued || this.destroyed) return;
            this.renderQueued = true;
            requestAnimationFrame(() => {
                this.renderQueued = false;
                this.renderGrid();
            });
        }

        renderGrid() {
            if (!this.workbook || !this.viewport) return;
            const { rows, cols } = this.dimensions();
            const { widths, offsets, total } = this.columnGeometry();
            const viewport = this.viewport;
            this.canvas.style.width = `${total}px`;
            this.canvas.style.height = `${HEADER_HEIGHT + rows * ROW_HEIGHT}px`;
            viewport.setAttribute('aria-rowcount', String(rows));
            viewport.setAttribute('aria-colcount', String(cols));

            const scrollLeft = viewport.scrollLeft;
            const scrollTop = viewport.scrollTop;
            const left = Math.max(0, scrollLeft - ROW_NUMBER_WIDTH);
            const right = scrollLeft + viewport.clientWidth + DEFAULT_COLUMN_WIDTH;
            let firstCol = 0;
            while (firstCol < cols - 1 && offsets[firstCol + 1] < left) firstCol += 1;
            let lastCol = firstCol;
            while (lastCol < cols - 1 && offsets[lastCol] < right) lastCol += 1;
            const firstRow = Math.max(0, Math.floor((scrollTop - HEADER_HEIGHT) / ROW_HEIGHT) - 2);
            const lastRow = Math.min(rows - 1, Math.ceil((scrollTop + viewport.clientHeight) / ROW_HEIGHT) + 2);
            const bounds = this.selectionBounds();
            const matchSet = new Set(this.searchMatches.map(([r, c]) => `${r},${c}`));

            this.headerLayer.innerHTML = '';
            const corner = document.createElement('button');
            corner.type = 'button';
            corner.className = 'canvas-spreadsheet-corner';
            corner.dataset.zone = 'corner';
            corner.setAttribute('aria-label', editorT('spreadsheet_select_all', 'Select all cells'));
            this.headerLayer.appendChild(corner);
            for (let c = firstCol; c <= lastCol; c += 1) {
                const header = document.createElement('button');
                header.type = 'button';
                header.className = 'canvas-spreadsheet-column-header';
                if (bounds && c >= bounds.c0 && c <= bounds.c1) header.classList.add('is-selected');
                header.dataset.zone = 'column';
                header.dataset.column = String(c);
                header.style.left = `${offsets[c]}px`;
                header.style.width = `${widths[c]}px`;
                const label = document.createElement('span');
                label.textContent = colName(c);
                header.appendChild(label);
                const resizeHandle = document.createElement('span');
                resizeHandle.className = 'canvas-spreadsheet-column-resize';
                resizeHandle.dataset.zone = 'column-resize';
                resizeHandle.dataset.column = String(c);
                resizeHandle.setAttribute('aria-hidden', 'true');
                header.appendChild(resizeHandle);
                header.setAttribute('aria-label', editorT('spreadsheet_column_aria', 'Column {column}', { column: colName(c) }));
                this.headerLayer.appendChild(header);
            }

            this.rowsLayer.innerHTML = '';
            for (let r = firstRow; r <= lastRow; r += 1) {
                const row = document.createElement('div');
                row.className = 'canvas-spreadsheet-row';
                row.style.top = `${HEADER_HEIGHT + r * ROW_HEIGHT}px`;
                row.style.width = `${total}px`;
                row.setAttribute('role', 'row');
                row.setAttribute('aria-rowindex', String(r + 1));

                const rowNumber = document.createElement('button');
                rowNumber.type = 'button';
                rowNumber.className = 'canvas-spreadsheet-row-number';
                if (bounds && r >= bounds.r0 && r <= bounds.r1) rowNumber.classList.add('is-selected');
                rowNumber.dataset.zone = 'row';
                rowNumber.dataset.row = String(r);
                rowNumber.textContent = String(r + 1);
                rowNumber.setAttribute('aria-label', editorT('spreadsheet_row_aria', 'Row {row}', { row: r + 1 }));
                row.appendChild(rowNumber);

                for (let c = firstCol; c <= lastCol; c += 1) {
                    const address = XLSX.utils.encode_cell({ r, c });
                    const cell = this.activeSheet[address];
                    const cellElement = document.createElement('div');
                    cellElement.className = 'canvas-spreadsheet-cell';
                    if (cell?.t === 'n') cellElement.classList.add('is-number');
                    if (matchSet.has(`${r},${c}`)) cellElement.classList.add('is-match');
                    cellElement.dataset.zone = 'cell';
                    cellElement.dataset.row = String(r);
                    cellElement.dataset.column = String(c);
                    cellElement.id = `canvas-spreadsheet-${this.instanceId}-cell-${this.activeSheetIndex}-${r}-${c}`;
                    cellElement.style.left = `${offsets[c]}px`;
                    cellElement.style.width = `${widths[c]}px`;
                    cellElement.textContent = this.displayCellValue(cell);
                    cellElement.title = this.displayCellValue(cell);
                    cellElement.setAttribute('role', 'gridcell');
                    cellElement.setAttribute('aria-colindex', String(c + 1));
                    cellElement.setAttribute('aria-selected', bounds && r >= bounds.r0 && r <= bounds.r1 && c >= bounds.c0 && c <= bounds.c1 ? 'true' : 'false');
                    row.appendChild(cellElement);
                }
                this.rowsLayer.appendChild(row);
            }
            this.renderSelection(offsets, widths);
        }

        renderSelection(offsets, widths) {
            const bounds = this.selectionBounds();
            this.selectionLayer.innerHTML = '';
            if (!bounds) return;
            const rectangle = document.createElement('div');
            rectangle.className = 'canvas-spreadsheet-selection';
            rectangle.style.left = `${offsets[bounds.c0]}px`;
            rectangle.style.top = `${HEADER_HEIGHT + bounds.r0 * ROW_HEIGHT}px`;
            rectangle.style.width = `${offsets[bounds.c1] + widths[bounds.c1] - offsets[bounds.c0]}px`;
            rectangle.style.height = `${(bounds.r1 - bounds.r0 + 1) * ROW_HEIGHT}px`;
            this.selectionLayer.appendChild(rectangle);
        }

        selectionBounds() {
            if (!this.selection) return null;
            return {
                r0: Math.min(this.selection.ar, this.selection.hr),
                r1: Math.max(this.selection.ar, this.selection.hr),
                c0: Math.min(this.selection.ac, this.selection.hc),
                c1: Math.max(this.selection.ac, this.selection.hc),
            };
        }

        setSelection(ar, ac, hr = ar, hc = ac) {
            const { rows, cols } = this.dimensions();
            this.selection = {
                ar: Math.max(0, Math.min(rows - 1, ar)),
                ac: Math.max(0, Math.min(cols - 1, ac)),
                hr: Math.max(0, Math.min(rows - 1, hr)),
                hc: Math.max(0, Math.min(cols - 1, hc)),
            };
            this.viewport.setAttribute(
                'aria-activedescendant',
                `canvas-spreadsheet-${this.instanceId}-cell-${this.activeSheetIndex}-${this.selection.ar}-${this.selection.ac}`,
            );
            this.updateFormulaBar();
            this.updateStatus();
            this.scheduleRender();
        }

        ensureVisible(row, column) {
            const { widths, offsets } = this.columnGeometry();
            const viewport = this.viewport;
            const top = HEADER_HEIGHT + row * ROW_HEIGHT;
            const bottom = top + ROW_HEIGHT;
            const left = offsets[column];
            const right = left + widths[column];
            if (top < viewport.scrollTop + HEADER_HEIGHT) viewport.scrollTop = Math.max(0, top - HEADER_HEIGHT);
            else if (bottom > viewport.scrollTop + viewport.clientHeight) viewport.scrollTop = bottom - viewport.clientHeight;
            if (left < viewport.scrollLeft + ROW_NUMBER_WIDTH) viewport.scrollLeft = Math.max(0, left - ROW_NUMBER_WIDTH);
            else if (right > viewport.scrollLeft + viewport.clientWidth) viewport.scrollLeft = right - viewport.clientWidth;
        }

        handlePointerDown(event) {
            const target = event.target.closest('[data-zone]');
            if (!target || event.button !== 0) return;

            // Prevent the browser's DOM text selection from competing with
            // the spreadsheet range selection. This is especially important
            // when the drag begins while a previous cell editor is active.
            event.preventDefault();
            window.getSelection?.()?.removeAllRanges?.();

            // A click outside an active cell editor means "commit and move".
            // Process both actions during this same pointer event so the first
            // drag after editing immediately selects the intended range.
            if (this.editing) {
                this.commitCellEdit(this.editing.input.value);
            }
            const zone = target.dataset.zone;
            const { rows, cols } = this.dimensions();
            if (zone === 'column-resize') {
                event.preventDefault();
                const column = Number(target.dataset.column);
                this.dragState = {
                    mode: 'resize',
                    column,
                    startX: event.clientX,
                    startWidth: this.getColumnWidths()[column],
                };
                document.body.classList.add('is-resizing-spreadsheet-column');
                return;
            }
            if (zone === 'corner') this.setSelection(0, 0, rows - 1, cols - 1);
            else if (zone === 'column') {
                const column = Number(target.dataset.column);
                this.setSelection(0, column, rows - 1, column);
                this.dragState = { mode: 'column', anchor: column };
            } else if (zone === 'row') {
                const row = Number(target.dataset.row);
                this.setSelection(row, 0, row, cols - 1);
                this.dragState = { mode: 'row', anchor: row };
            } else if (zone === 'cell') {
                const row = Number(target.dataset.row);
                const column = Number(target.dataset.column);
                if (event.shiftKey) this.setSelection(this.selection.ar, this.selection.ac, row, column);
                else this.setSelection(row, column);
                this.dragState = { mode: 'cell', anchorRow: this.selection.ar, anchorColumn: this.selection.ac };
            }
            this.viewport.focus();
        }

        handlePointerMove(event) {
            if (!this.dragState) return;
            event.preventDefault();
            if (this.dragState.mode === 'resize') {
                const width = Math.max(
                    MIN_COLUMN_WIDTH,
                    Math.min(MAX_COLUMN_WIDTH, this.dragState.startWidth + event.clientX - this.dragState.startX),
                );
                this.getColumnWidths()[this.dragState.column] = width;
                this.dragState.currentWidth = width;
                this.renderGrid();
                return;
            }
            if (event.buttons === 0) {
                this.finishPointerInteraction();
                return;
            }
            const target = document.elementFromPoint(event.clientX, event.clientY)?.closest?.('[data-zone]');
            if (!target) return;
            const { rows, cols } = this.dimensions();
            if (this.dragState.mode === 'cell' && target.dataset.zone === 'cell') {
                this.setSelection(
                    this.dragState.anchorRow,
                    this.dragState.anchorColumn,
                    Number(target.dataset.row),
                    Number(target.dataset.column),
                );
            } else if (this.dragState.mode === 'row' && target.dataset.row !== undefined) {
                this.setSelection(this.dragState.anchor, 0, Number(target.dataset.row), cols - 1);
            } else if (this.dragState.mode === 'column' && target.dataset.column !== undefined) {
                this.setSelection(0, this.dragState.anchor, rows - 1, Number(target.dataset.column));
            }
        }

        finishPointerInteraction() {
            if (!this.dragState) return;
            if (this.dragState.mode === 'resize' && this.dragState.currentWidth !== undefined) {
                this.recordColumnWidth(this.dragState.column, this.dragState.currentWidth);
            }
            this.dragState = null;
            document.body.classList.remove('is-resizing-spreadsheet-column');
        }

        recordColumnWidth(column, width) {
            const worksheet = this.activeSheet;
            if (!worksheet['!cols']) worksheet['!cols'] = [];
            worksheet['!cols'][column] = { ...(worksheet['!cols'][column] || {}), wpx: width };
            if (!this.editedColumnWidths.has(this.activeSheetName)) {
                this.editedColumnWidths.set(this.activeSheetName, new Map());
            }
            this.editedColumnWidths.get(this.activeSheetName).set(column, width);
            this.setDirty(true);
        }

        handleDoubleClick(event) {
            const resizeHandle = event.target.closest('[data-zone="column-resize"]');
            if (resizeHandle) {
                const column = Number(resizeHandle.dataset.column);
                const { usedRows } = this.dimensions();
                let longest = colName(column).length;
                for (let row = 0; row < usedRows; row += 1) {
                    const address = XLSX.utils.encode_cell({ r: row, c: column });
                    longest = Math.max(longest, this.displayCellValue(this.activeSheet[address]).length);
                }
                const width = Math.max(MIN_COLUMN_WIDTH, Math.min(MAX_COLUMN_WIDTH, longest * 7.5 + 22));
                this.getColumnWidths()[column] = width;
                this.recordColumnWidth(column, width);
                this.renderGrid();
                return;
            }
            const cell = event.target.closest('[data-zone="cell"]');
            if (!cell || !this.editable) return;
            this.startCellEdit(Number(cell.dataset.row), Number(cell.dataset.column));
        }

        handleKeyDown(event) {
            if (this.editing) return;
            const meta = event.metaKey || event.ctrlKey;
            const key = event.key;
            if (meta && key.toLowerCase() === 'z') {
                event.preventDefault();
                event.shiftKey ? this.redo() : this.undo();
                return;
            }
            if (meta && key.toLowerCase() === 'y') {
                event.preventDefault();
                this.redo();
                return;
            }
            if (meta && key.toLowerCase() === 'f') {
                event.preventDefault();
                this.element.querySelector('[data-role="search"]').focus();
                return;
            }
            const moves = {
                ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1],
            };
            if (moves[key]) {
                event.preventDefault();
                const [dr, dc] = moves[key];
                const row = event.shiftKey ? this.selection.hr + dr : this.selection.ar + dr;
                const column = event.shiftKey ? this.selection.hc + dc : this.selection.ac + dc;
                if (event.shiftKey) this.setSelection(this.selection.ar, this.selection.ac, row, column);
                else this.setSelection(row, column);
                this.ensureVisible(Math.max(0, row), Math.max(0, column));
                return;
            }
            if (key === 'Tab' || key === 'Enter') {
                event.preventDefault();
                const row = this.selection.ar + (key === 'Enter' ? (event.shiftKey ? -1 : 1) : 0);
                const column = this.selection.ac + (key === 'Tab' ? (event.shiftKey ? -1 : 1) : 0);
                this.setSelection(row, column);
                this.ensureVisible(row, column);
                return;
            }
            if ((key === 'Delete' || key === 'Backspace') && this.editable) {
                event.preventDefault();
                this.clearSelection();
                return;
            }
            if (key === 'F2' && this.editable) {
                event.preventDefault();
                this.startCellEdit(this.selection.ar, this.selection.ac);
                return;
            }
            if (key.length === 1 && !meta && !event.altKey && this.editable) {
                event.preventDefault();
                this.startCellEdit(this.selection.ar, this.selection.ac, key);
            }
        }

        startCellEdit(row, column, initialValue = null) {
            if (!this.editable || this.editing) return;
            const { widths, offsets } = this.columnGeometry();
            const address = XLSX.utils.encode_cell({ r: row, c: column });
            const input = document.createElement('input');
            input.className = 'canvas-spreadsheet-cell-editor';
            input.value = initialValue === null ? editCellValue(this.activeSheet[address]) : initialValue;
            input.style.left = `${offsets[column]}px`;
            input.style.top = `${HEADER_HEIGHT + row * ROW_HEIGHT}px`;
            input.style.width = `${widths[column]}px`;
            input.style.height = `${ROW_HEIGHT}px`;
            input.setAttribute('aria-label', editorT('spreadsheet_edit_cell_aria', 'Edit cell {cell}', { cell: address }));
            this.canvas.appendChild(input);
            this.editing = { row, column, address, input };
            input.focus();
            if (initialValue === null) input.select();
            const commit = (move = null) => {
                if (!this.editing) return;
                this.commitCellEdit(input.value);
                if (move) {
                    this.setSelection(row + move[0], column + move[1]);
                    this.ensureVisible(row + move[0], column + move[1]);
                }
                this.viewport.focus();
            };
            input.addEventListener('keydown', (event) => {
                event.stopPropagation();
                if (event.key === 'Enter') { event.preventDefault(); commit([event.shiftKey ? -1 : 1, 0]); }
                else if (event.key === 'Tab') { event.preventDefault(); commit([0, event.shiftKey ? -1 : 1]); }
                else if (event.key === 'Escape') {
                    event.preventDefault();
                    input.remove();
                    this.editing = null;
                    this.viewport.focus();
                }
            });
            input.addEventListener('blur', () => {
                if (this.editing?.input === input) this.commitCellEdit(input.value);
            });
        }

        commitCellEdit(value) {
            if (!this.editing) return;
            const { address, input } = this.editing;
            const previous = cloneCell(this.activeSheet[address]);
            const next = inferCell(value, previous);
            // Clear the edit state before removing the focused input. Removal
            // can synchronously dispatch blur in Chromium; leaving the state
            // set would make the blur handler attempt a second commit.
            this.editing = null;
            input.remove();
            this.applyCellChanges([{ sheetName: this.activeSheetName, address, previous, next }]);
        }

        commitPendingEdit() {
            if (!this.editing?.input) return;
            // Canvas teardown can happen before the focused input emits blur.
            // Commit synchronously so save-before-destroy serializes the value
            // currently visible to the user, not the prior workbook cell.
            this.commitCellEdit(this.editing.input.value);
        }

        commitFormulaBar() {
            if (!this.editable) return;
            const address = XLSX.utils.encode_cell({ r: this.selection.ar, c: this.selection.ac });
            const previous = cloneCell(this.activeSheet[address]);
            const input = this.element.querySelector('[data-role="formula"]');
            const next = inferCell(input.value, previous);
            this.applyCellChanges([{ sheetName: this.activeSheetName, address, previous, next }]);
        }

        applyCellChanges(changes, { recordUndo = true } = {}) {
            const effective = changes.filter((change) => !cellsEqual(change.previous, change.next));
            if (!effective.length) return;
            effective.forEach((change) => {
                const sheet = this.workbook.Sheets[change.sheetName];
                if (change.next) sheet[change.address] = cloneCell(change.next);
                else delete sheet[change.address];
                this.expandSheetRange(sheet, change.address);
                if (!this.editedCells.has(change.sheetName)) this.editedCells.set(change.sheetName, new Map());
                this.editedCells.get(change.sheetName).set(change.address, cloneCell(change.next));
            });
            // Any changed value can be a precedent for a workbook formula or
            // chart series, so request a full native recalculation even when
            // the edited cell itself is not a formula.
            this.requiresRecalculation = true;
            this.requestFormulaRecalculation();
            if (recordUndo) {
                this.undoStack.push(effective.map((change) => ({ ...change, previous: cloneCell(change.previous), next: cloneCell(change.next) })));
                if (this.undoStack.length > 100) this.undoStack.shift();
                this.redoStack = [];
            }
            this.setDirty(true);
            this.refreshAfterChange();
        }

        requestFormulaRecalculation() {
            // SheetJS CE preserves formula expressions but intentionally does
            // not calculate them. Mark the workbook so Excel/LibreOffice
            // refreshes edited formulas and dependent charts when it opens.
            if (!this.workbook.Workbook) this.workbook.Workbook = {};
            this.workbook.Workbook.CalcPr = {
                ...(this.workbook.Workbook.CalcPr || {}),
                calcMode: 'auto',
                fullCalcOnLoad: true,
                forceFullCalc: true,
            };
        }

        expandSheetRange(sheet, address) {
            const cell = XLSX.utils.decode_cell(address);
            const range = sheet['!ref'] ? XLSX.utils.decode_range(sheet['!ref']) : { s: { r: cell.r, c: cell.c }, e: { r: cell.r, c: cell.c } };
            range.s.r = Math.min(range.s.r, cell.r);
            range.s.c = Math.min(range.s.c, cell.c);
            range.e.r = Math.max(range.e.r, cell.r);
            range.e.c = Math.max(range.e.c, cell.c);
            sheet['!ref'] = XLSX.utils.encode_range(range);
        }

        refreshAfterChange() {
            this.runSearch(false);
            this.updateFormulaBar();
            this.updateStatus();
            this.updateToolbar();
            this.scheduleRender();
        }

        setDirty(dirty) {
            const changed = this.dirty !== Boolean(dirty);
            this.dirty = Boolean(dirty);
            if (changed || dirty) this.options.onChange?.({ dirty: this.dirty, editor: this });
        }

        undo() {
            const changes = this.undoStack.pop();
            if (!changes) return;
            const reversed = changes.map((change) => ({
                sheetName: change.sheetName,
                address: change.address,
                previous: cloneCell(change.next),
                next: cloneCell(change.previous),
            }));
            this.applyCellChanges(reversed, { recordUndo: false });
            this.redoStack.push(changes);
            this.updateToolbar();
        }

        redo() {
            const changes = this.redoStack.pop();
            if (!changes) return;
            this.applyCellChanges(changes, { recordUndo: false });
            this.undoStack.push(changes);
            this.updateToolbar();
        }

        clearSelection() {
            const bounds = this.selectionBounds();
            const changes = [];
            for (let r = bounds.r0; r <= bounds.r1; r += 1) {
                for (let c = bounds.c0; c <= bounds.c1; c += 1) {
                    const address = XLSX.utils.encode_cell({ r, c });
                    const previous = cloneCell(this.activeSheet[address]);
                    if (previous) changes.push({ sheetName: this.activeSheetName, address, previous, next: null });
                }
            }
            this.applyCellChanges(changes);
        }

        selectionText() {
            const bounds = this.selectionBounds();
            const lines = [];
            for (let r = bounds.r0; r <= bounds.r1; r += 1) {
                const row = [];
                for (let c = bounds.c0; c <= bounds.c1; c += 1) {
                    row.push(editCellValue(this.activeSheet[XLSX.utils.encode_cell({ r, c })]));
                }
                lines.push(row.map((value) => String(value).includes('\t') || String(value).includes('\n')
                    ? `"${String(value).replace(/"/g, '""')}"`
                    : value).join('\t'));
            }
            return lines.join('\n');
        }

        handleCopy(event) {
            event.preventDefault();
            event.clipboardData?.setData('text/plain', this.selectionText());
        }

        handleCut(event) {
            if (!this.editable) return;
            this.handleCopy(event);
            this.clearSelection();
        }

        handlePaste(event) {
            if (!this.editable) return;
            const text = event.clipboardData?.getData('text/plain');
            if (typeof text !== 'string') return;
            event.preventDefault();
            // SheetJS auto-detects tab-delimited string input. Resolve the
            // generated sheet by name because that name is not an API contract.
            const workbook = XLSX.read(text, { type: 'string', raw: true });
            const firstSheetName = workbook.SheetNames?.[0];
            const worksheet = firstSheetName ? workbook.Sheets?.[firstSheetName] : null;
            if (!worksheet) return;
            const matrix = XLSX.utils.sheet_to_json(worksheet, { header: 1, raw: true, defval: '' });
            const changes = [];
            matrix.forEach((values, rowOffset) => values.forEach((value, columnOffset) => {
                const r = this.selection.ar + rowOffset;
                const c = this.selection.ac + columnOffset;
                const address = XLSX.utils.encode_cell({ r, c });
                const previous = cloneCell(this.activeSheet[address]);
                const next = inferCell(String(value ?? ''), previous);
                changes.push({ sheetName: this.activeSheetName, address, previous, next });
            }));
            this.applyCellChanges(changes);
            if (matrix.length) {
                this.setSelection(
                    this.selection.ar,
                    this.selection.ac,
                    this.selection.ar + matrix.length - 1,
                    this.selection.ac + Math.max(0, ...matrix.map((row) => row.length - 1)),
                );
            }
        }

        runSearch(jump = true) {
            this.searchMatches = [];
            this.searchIndex = -1;
            const query = this.searchQuery.trim().toLocaleLowerCase();
            if (query) {
                const { usedRows, usedCols } = this.dimensions();
                outer: for (let r = 0; r < usedRows; r += 1) {
                    for (let c = 0; c < usedCols; c += 1) {
                        const value = this.displayCellValue(this.activeSheet[XLSX.utils.encode_cell({ r, c })]);
                        if (value.toLocaleLowerCase().includes(query)) {
                            this.searchMatches.push([r, c]);
                            if (this.searchMatches.length >= MAX_SEARCH_MATCHES) break outer;
                        }
                    }
                }
            }
            if (jump && this.searchMatches.length) this.gotoSearchMatch(0);
            this.updateSearchCount();
            this.scheduleRender();
        }

        gotoSearchMatch(index) {
            if (!this.searchMatches.length) return;
            this.searchIndex = ((index % this.searchMatches.length) + this.searchMatches.length) % this.searchMatches.length;
            const [row, column] = this.searchMatches[this.searchIndex];
            this.setSelection(row, column);
            this.ensureVisible(row, column);
            this.updateSearchCount();
        }

        updateSearchCount() {
            const output = this.element.querySelector('[data-role="search-count"]');
            if (!this.searchQuery.trim()) output.textContent = '';
            else if (!this.searchMatches.length) output.textContent = '0';
            else output.textContent = `${this.searchIndex + 1} / ${this.searchMatches.length}`;
        }

        handleAction(action) {
            if (action === 'undo') this.undo();
            else if (action === 'redo') this.redo();
            else if (action === 'add-row') {
                const extra = this.extraDimensions.get(this.activeSheetName) || { rows: 0, cols: 0 };
                extra.rows += 1;
                this.extraDimensions.set(this.activeSheetName, extra);
                this.scheduleRender();
            } else if (action === 'add-column') {
                const extra = this.extraDimensions.get(this.activeSheetName) || { rows: 0, cols: 0 };
                extra.cols += 1;
                this.extraDimensions.set(this.activeSheetName, extra);
                this.scheduleRender();
            } else if (action === 'add-sheet') this.addSheet();
        }

        addSheet() {
            if (!this.editable || this.compatibilityMode || ['csv', 'tsv'].includes(this.format)) return;
            let index = this.workbook.SheetNames.length + 1;
            let name = editorT('spreadsheet_new_sheet_name', 'Sheet {number}', { number: index });
            while (this.workbook.SheetNames.includes(name)) {
                index += 1;
                name = editorT('spreadsheet_new_sheet_name', 'Sheet {number}', { number: index });
            }
            XLSX.utils.book_append_sheet(this.workbook, XLSX.utils.aoa_to_sheet([[]]), name);
            this.activeSheetIndex = this.workbook.SheetNames.length - 1;
            this.selection = { ar: 0, ac: 0, hr: 0, hc: 0 };
            this.setDirty(true);
            this.refreshAll();
        }

        renderTabs() {
            const tabs = this.element.querySelector('[data-role="tabs"]');
            tabs.innerHTML = '';
            this.workbook.SheetNames.forEach((sheetName, index) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'canvas-spreadsheet-tab';
                button.classList.toggle('is-active', index === this.activeSheetIndex);
                button.setAttribute('role', 'tab');
                button.setAttribute('aria-selected', index === this.activeSheetIndex ? 'true' : 'false');
                button.textContent = sheetName;
                button.addEventListener('click', () => {
                    this.activeSheetIndex = index;
                    this.selection = { ar: 0, ac: 0, hr: 0, hc: 0 };
                    this.viewport.scrollTop = 0;
                    this.viewport.scrollLeft = 0;
                    this.runSearch(false);
                    this.refreshAll();
                    this.viewport.focus();
                });
                tabs.appendChild(button);
            });
        }

        updateFormulaBar() {
            if (!this.workbook) return;
            const address = XLSX.utils.encode_cell({ r: this.selection.ar, c: this.selection.ac });
            this.element.querySelector('[data-role="cell-name"]').textContent = address;
            this.element.querySelector('[data-role="selection-name"]').textContent = address;
            const formula = this.element.querySelector('[data-role="formula"]');
            if (document.activeElement !== formula) formula.value = editCellValue(this.activeSheet[address]);
            formula.readOnly = !this.editable;
        }

        updateStatus() {
            if (!this.workbook) return;
            const { usedRows, usedCols } = this.dimensions();
            this.element.querySelector('[data-role="dimensions"]').textContent = editorT(
                'spreadsheet_dimensions',
                '{rows} rows × {columns} columns',
                { rows: usedRows.toLocaleString(), columns: usedCols.toLocaleString() },
            );
            const bounds = this.selectionBounds();
            const numbers = [];
            const selectedCellCount = (bounds.r1 - bounds.r0 + 1) * (bounds.c1 - bounds.c0 + 1);
            // Statistics are a convenience, so never let a whole-column or
            // whole-sheet selection stall interaction with a large workbook.
            if (selectedCellCount <= MAX_STATISTICS_CELLS) {
                for (let r = bounds.r0; r <= bounds.r1; r += 1) {
                    for (let c = bounds.c0; c <= bounds.c1; c += 1) {
                        const cell = this.activeSheet[XLSX.utils.encode_cell({ r, c })];
                        if (cell?.t === 'n' && (!this.requiresRecalculation || !cell.f)
                            && Number.isFinite(Number(cell.v))) numbers.push(Number(cell.v));
                    }
                }
            }
            const statistics = this.element.querySelector('[data-role="statistics"]');
            statistics.textContent = numbers.length > 1
                ? editorT('spreadsheet_selection_statistics', 'Count {count} · Sum {sum} · Average {average}', {
                    count: numbers.length,
                    sum: numbers.reduce((total, value) => total + value, 0).toLocaleString(),
                    average: (numbers.reduce((total, value) => total + value, 0) / numbers.length).toLocaleString(undefined, { maximumFractionDigits: 6 }),
                })
                : '';
        }

        updateToolbar() {
            const undo = this.element.querySelector('[data-action="undo"]');
            const redo = this.element.querySelector('[data-action="redo"]');
            undo.disabled = !this.editable || !this.undoStack.length;
            redo.disabled = !this.editable || !this.redoStack.length;
            const isDelimited = ['csv', 'tsv'].includes(this.format);
            const addSheet = this.element.querySelector('[data-action="add-sheet"]');
            const canAddSheet = this.editable && !this.compatibilityMode && !isDelimited;
            addSheet.hidden = !canAddSheet;
            addSheet.disabled = !canAddSheet;

            // A single sheet is not navigation. Only retain the tab strip when
            // another sheet can be selected or the workbook can be expanded.
            // This avoids showing a dead Sheet1 tab for CSV/TSV, protected
            // compatibility-mode workbooks, and single-sheet read-only files.
            const sheetControls = this.element.querySelector('[data-role="sheet-controls"]');
            const hasSheetNavigation = this.workbook.SheetNames.length > 1;
            sheetControls.hidden = !hasSheetNavigation && !canAddSheet;

            const addRow = this.element.querySelector('[data-action="add-row"]');
            const addColumn = this.element.querySelector('[data-action="add-column"]');
            addRow.hidden = !this.editable;
            addColumn.hidden = !this.editable;
            addRow.disabled = !this.editable;
            addColumn.disabled = !this.editable;

            // Hide the entire footer if none of its controls can do anything.
            // Multi-sheet read-only workbooks retain it for sheet navigation.
            const tabsBar = this.element.querySelector('[data-role="tabs-bar"]');
            tabsBar.hidden = sheetControls.hidden && !this.editable;
        }

        updateCompatibilityNotice() {
            const notice = this.element.querySelector('[data-role="compatibility"]');
            if (this.format === 'xls') {
                notice.hidden = false;
                notice.textContent = editorT(
                    'spreadsheet_legacy_xls_notice',
                    'Legacy XLS mode · cells and formulas are editable; embedded objects may not be preserved.',
                );
                notice.title = editorT(
                    'spreadsheet_legacy_xls_help',
                    'Use XLSX for the safest editing of charts, drawings, and other native Excel objects.',
                );
                return;
            }
            if (!this.advancedFeatures.length) {
                notice.hidden = true;
                notice.removeAttribute('aria-label');
                notice.removeAttribute('title');
                return;
            }
            const labels = this.advancedFeatures.map((feature) => {
                const [key, fallback] = FEATURE_TRANSLATIONS[feature]
                    || ['spreadsheet_feature_uninspected_package', 'unverified workbook features'];
                return editorT(key, fallback);
            });
            const featureSummary = editorT(
                'spreadsheet_native_features',
                'Workbook features: {features}',
                { features: labels.join(', ') },
            );
            const compatibilityHelp = editorT(
                'spreadsheet_compatibility_help',
                'Native workbook features are preserved. Some structural editing is restricted to avoid damaging them.',
            );
            notice.hidden = false;
            notice.textContent = this.compatibilityMode
                ? editorT('spreadsheet_compatibility_mode', 'Compatibility mode')
                : featureSummary;
            // Keep the toolbar badge intentionally short. The complete,
            // localized feature list remains available as hover and
            // assistive-technology text without crowding the search bar.
            notice.title = this.compatibilityMode
                ? `${compatibilityHelp} ${featureSummary}`
                : featureSummary;
            notice.setAttribute('aria-label', notice.title);
        }

        sheetForDelimitedExport() {
            if (!this.requiresRecalculation) return this.activeSheet;
            const exportSheet = { ...this.activeSheet };
            Object.keys(exportSheet).forEach((address) => {
                const cell = exportSheet[address];
                if (address.startsWith('!') || !cell?.f) return;
                // A CSV/TSV file has nowhere to store both a formula and its
                // cached result. Once dependencies changed, keep the formula
                // expression instead of exporting a known-stale cached value.
                exportSheet[address] = { ...cell, v: undefined, w: undefined };
            });
            return exportSheet;
        }

        async serialize(targetFormat = this.format) {
            const format = normalizeFormat(targetFormat);
            if (format === 'csv' || format === 'tsv') {
                const separator = format === 'tsv' ? '\t' : ',';
                const text = XLSX.utils.sheet_to_csv(this.sheetForDelimitedExport(), {
                    FS: separator,
                    RS: '\n',
                    blankrows: true,
                });
                const prefix = format === 'csv' ? '\uFEFF' : '';
                return {
                    format,
                    bytes: new TextEncoder().encode(prefix + text).buffer,
                    blob: new Blob([prefix + text], { type: FORMAT_MIME_TYPES[format] }),
                    fileName: `${safeFileStem(this.fileName)}.${format}`,
                    requiresRecalculation: this.requiresRecalculation,
                };
            }

            let bytes;
            if (
                format === 'xlsx'
                && this.format === 'xlsx'
                && this.originalBytes
                && globalThis.JSZip
            ) {
                bytes = await this.patchOriginalXlsx();
            } else {
                bytes = XLSX.write(this.workbook, {
                    type: 'array',
                    bookType: format === 'xls' ? 'biff8' : 'xlsx',
                    cellStyles: true,
                    bookVBA: true,
                    compression: true,
                });
            }
            return {
                format,
                bytes,
                blob: new Blob([bytes], { type: FORMAT_MIME_TYPES[format] }),
                fileName: `${safeFileStem(this.fileName)}.${format}`,
                requiresRecalculation: this.requiresRecalculation,
            };
        }

        async patchOriginalXlsx() {
            const zip = await JSZip.loadAsync(this.originalBytes);
            const workbookXml = await zip.file('xl/workbook.xml')?.async('string');
            const relationshipsXml = await zip.file('xl/_rels/workbook.xml.rels')?.async('string');
            if (!workbookXml || !relationshipsXml) {
                throw new Error(editorT('spreadsheet_preservation_failed', 'Native workbook features could not be preserved safely.'));
            }
            const parser = new DOMParser();
            const workbookDocument = parser.parseFromString(workbookXml, 'application/xml');
            const relationshipsDocument = parser.parseFromString(relationshipsXml, 'application/xml');
            const relationshipTargets = new Map(
                xmlLocalElements(relationshipsDocument, 'Relationship').map((node) => [
                    node.getAttribute('Id'), node.getAttribute('Target'),
                ]),
            );
            const sheetPaths = new Map();
            xmlLocalElements(workbookDocument, 'sheet').forEach((sheetNode) => {
                const relationshipId = sheetNode.getAttribute('r:id')
                    || sheetNode.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships', 'id');
                const target = relationshipTargets.get(relationshipId);
                if (!target) return;
                const normalizedTarget = target.replace(/^\//, '').replace(/^xl\//, '');
                sheetPaths.set(sheetNode.getAttribute('name'), `xl/${normalizedTarget}`);
            });

            const editedSheetNames = new Set([
                ...this.editedCells.keys(),
                ...this.editedColumnWidths.keys(),
            ]);
            for (const sheetName of editedSheetNames) {
                const edits = this.editedCells.get(sheetName) || new Map();
                const columnEdits = this.editedColumnWidths.get(sheetName) || new Map();
                if (!edits.size && !columnEdits.size) continue;
                const path = sheetPaths.get(sheetName);
                const source = path ? await zip.file(path)?.async('string') : '';
                if (!path || !source) throw new Error(editorT('spreadsheet_preservation_failed', 'Native workbook features could not be preserved safely.'));
                const documentXml = parser.parseFromString(source, 'application/xml');
                const sheetData = xmlLocalElements(documentXml, 'sheetData')[0];
                if (!sheetData) throw new Error(editorT('spreadsheet_preservation_failed', 'Native workbook features could not be preserved safely.'));
                const namespace = documentXml.documentElement.namespaceURI;

                if (columnEdits.size) {
                    let columnsNode = xmlLocalElements(documentXml, 'cols')[0];
                    if (!columnsNode) {
                        columnsNode = documentXml.createElementNS(namespace, 'cols');
                        sheetData.parentNode.insertBefore(columnsNode, sheetData);
                    }
                    for (const [column, width] of columnEdits.entries()) {
                        const excelWidth = Math.max(0, (width - 5) / 7);
                        let columnNode = xmlLocalElements(columnsNode, 'col').find((node) => (
                            Number(node.getAttribute('min')) <= column + 1
                            && Number(node.getAttribute('max')) >= column + 1
                        ));
                        if (!columnNode || Number(columnNode.getAttribute('min')) !== Number(columnNode.getAttribute('max'))) {
                            columnNode = documentXml.createElementNS(namespace, 'col');
                            columnNode.setAttribute('min', String(column + 1));
                            columnNode.setAttribute('max', String(column + 1));
                            columnsNode.appendChild(columnNode);
                        }
                        columnNode.setAttribute('width', excelWidth.toFixed(2));
                        columnNode.setAttribute('customWidth', '1');
                    }
                }

                for (const [address, cell] of edits.entries()) {
                    const position = XLSX.utils.decode_cell(address);
                    let rowNode = xmlLocalElements(sheetData, 'row')
                        .find((node) => Number(node.getAttribute('r')) === position.r + 1);
                    let cellNode = rowNode
                        ? Array.from(rowNode.childNodes).find((node) => node.nodeType === 1 && node.localName === 'c' && node.getAttribute('r') === address)
                        : null;
                    if (!cell) {
                        cellNode?.remove();
                        continue;
                    }
                    if (!rowNode) {
                        rowNode = documentXml.createElementNS(namespace, 'row');
                        rowNode.setAttribute('r', String(position.r + 1));
                        const following = xmlLocalElements(sheetData, 'row')
                            .find((node) => Number(node.getAttribute('r')) > position.r + 1);
                        sheetData.insertBefore(rowNode, following || null);
                    }
                    // The worksheet XML stores a numeric index into styles.xml,
                    // while SheetJS exposes `cell.s` as a style object when
                    // `cellStyles` is enabled. Only an existing XML attribute
                    // is therefore safe to copy into the native package.
                    const style = cellNode?.getAttribute('s') || '';
                    const replacement = documentXml.createElementNS(namespace, 'c');
                    replacement.setAttribute('r', address);
                    if (style) replacement.setAttribute('s', style);
                    if (cell.f) {
                        const formula = documentXml.createElementNS(namespace, 'f');
                        formula.textContent = String(cell.f);
                        replacement.appendChild(formula);
                    }
                    if (cell.t === 's' || cell.t === 'str') {
                        replacement.setAttribute('t', 'inlineStr');
                        const inlineString = documentXml.createElementNS(namespace, 'is');
                        const text = documentXml.createElementNS(namespace, 't');
                        text.textContent = String(cell.v ?? '');
                        inlineString.appendChild(text);
                        replacement.appendChild(inlineString);
                    } else {
                        if (cell.t === 'b') replacement.setAttribute('t', 'b');
                        else if (cell.t === 'e') replacement.setAttribute('t', 'e');
                        else if (cell.t === 'd') replacement.setAttribute('t', 'd');
                        const value = documentXml.createElementNS(namespace, 'v');
                        if (cell.v instanceof Date) value.textContent = cell.v.toISOString();
                        else if (cell.t === 'b') value.textContent = cell.v ? '1' : '0';
                        else if (cell.v !== undefined && cell.v !== null) value.textContent = String(cell.v);
                        replacement.appendChild(value);
                    }
                    if (cellNode) rowNode.replaceChild(replacement, cellNode);
                    else {
                        const following = Array.from(rowNode.childNodes)
                            .find((node) => node.nodeType === 1 && node.localName === 'c'
                                && XLSX.utils.decode_cell(node.getAttribute('r')).c > position.c);
                        rowNode.insertBefore(replacement, following || null);
                    }
                }
                zip.file(path, new XMLSerializer().serializeToString(documentXml));
            }
            if (this.requiresRecalculation) {
                let calcProperties = xmlLocalElements(workbookDocument, 'calcPr')[0];
                if (!calcProperties) {
                    calcProperties = workbookDocument.createElementNS(workbookDocument.documentElement.namespaceURI, 'calcPr');
                    workbookDocument.documentElement.appendChild(calcProperties);
                }
                calcProperties.setAttribute('calcMode', 'auto');
                calcProperties.setAttribute('fullCalcOnLoad', '1');
                calcProperties.setAttribute('forceFullCalc', '1');
                zip.file('xl/workbook.xml', new XMLSerializer().serializeToString(workbookDocument));
            }
            return zip.generateAsync({ type: 'arraybuffer', compression: 'DEFLATE' });
        }

        markSaved(serialized) {
            if (serialized?.bytes && serialized.format === this.format) {
                this.originalBytes = serialized.bytes.slice(0);
            }
            this.editedCells.clear();
            this.editedColumnWidths.clear();
            // Saving requests recalculation in Excel/LibreOffice but does not
            // calculate formulas inside this browser process. Keep the flag so
            // rendering, statistics, and delimited exports never trust cached
            // values that are still stale in the in-memory workbook.
            this.undoStack = [];
            this.redoStack = [];
            this.setDirty(false);
            this.updateToolbar();
        }

        destroy() {
            this.destroyed = true;
            this.listeners.splice(0).forEach((remove) => remove());
            this.editing?.input?.remove();
            this.editing = null;
            this.dragState = null;
            document.body.classList.remove('is-resizing-spreadsheet-column');
            this.element.remove();
        }
    }

    window.ChatSpreadsheetEditor = {
        formats: Object.freeze(Object.keys(FORMAT_MIME_TYPES)),
        isSpreadsheetFormat(format) {
            return Object.prototype.hasOwnProperty.call(FORMAT_MIME_TYPES, normalizeFormat(format));
        },
        async create(options) {
            const editor = new SpreadsheetEditor(options);
            return editor.initialize();
        },
    };
})();
