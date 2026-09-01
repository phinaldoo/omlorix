(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};
    const CANVAS_FILE_PREVIEW_MAX_BYTES = 1024 * 1024;
    const CODE_EXECUTION_HTML_PREVIEW_MAX_BYTES = 8 * 1024 * 1024;
    const SPREADSHEET_PREVIEW_MAX_BYTES = 25 * 1024 * 1024;

    function createFileLoadingModule({ t, formatT, hasHtmlFileExtension }) {
        class CanvasPreviewTooLargeError extends Error {
            constructor(maxBytes = CANVAS_FILE_PREVIEW_MAX_BYTES) {
                const size = typeof Utils === 'object' && Utils && typeof Utils.formatFileSize === 'function'
                    ? Utils.formatFileSize(maxBytes)
                    : '1 MB';
                super(formatT('files_preview_too_large_limit', 'This file is too large to preview. Previewing is limited to {size}.', { size }));
                this.name = 'CanvasPreviewTooLargeError';
                this.maxBytes = maxBytes;
            }
        }

        function getCanvasFilePreviewMaxBytes(contentType, fileRecord) {
            const normalizedType = String(contentType || '').trim().toLowerCase();
            const meta = fileRecord?.meta && typeof fileRecord.meta === 'object' ? fileRecord.meta : {};
            return normalizedType === 'html' && meta.origin === 'assistant' && meta.code_execution === true
                ? CODE_EXECUTION_HTML_PREVIEW_MAX_BYTES
                : CANVAS_FILE_PREVIEW_MAX_BYTES;
        }

        function getCanvasFileResponseTotalBytes(response) {
            const match = String(response?.headers?.get('Content-Range') || '').match(/\/(\d+)$/);
            if (!match) return null;
            const total = Number(match[1]);
            return Number.isSafeInteger(total) && total >= 0 ? total : null;
        }

        function getCanvasFileResponseLength(response) {
            const rawLength = response?.headers?.get('Content-Length');
            if (rawLength === null || rawLength === undefined || rawLength === '') return null;
            const length = Number(rawLength);
            return Number.isSafeInteger(length) && length >= 0 ? length : null;
        }

        function cancelCanvasFileResponse(response) {
            try {
                const cancellation = response?.body?.cancel?.();
                cancellation?.catch?.(() => {});
            } catch (_) {}
        }

        function enforceCanvasFileResponseHeaders(response, maxBytes) {
            if (getCanvasFileResponseTotalBytes(response) > maxBytes || getCanvasFileResponseLength(response) > maxBytes) {
                cancelCanvasFileResponse(response);
                throw new CanvasPreviewTooLargeError(maxBytes);
            }
        }

        async function readCanvasFileText(response, maxBytes = CANVAS_FILE_PREVIEW_MAX_BYTES) {
            enforceCanvasFileResponseHeaders(response, maxBytes);
            const reader = response?.body?.getReader?.();
            if (!reader) {
                cancelCanvasFileResponse(response);
                throw new Error(t('files_preview_load_error', 'Failed to load preview'));
            }
            const decoder = new TextDecoder('utf-8', { fatal: false });
            let bytesRead = 0;
            let text = '';
            try {
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) {
                        text += decoder.decode();
                        return text;
                    }
                    const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
                    if (bytesRead + chunk.byteLength > maxBytes) {
                        try { await reader.cancel(); } catch (_) {}
                        throw new CanvasPreviewTooLargeError(maxBytes);
                    }
                    bytesRead += chunk.byteLength;
                    text += decoder.decode(chunk, { stream: true });
                }
            } finally {
                try { reader.releaseLock?.(); } catch (_) {}
            }
        }

        async function loadContentFromFile(fileId, maxBytes = CANVAS_FILE_PREVIEW_MAX_BYTES) {
            if (!fileId || typeof window.authedFetch !== 'function') return '';
            const boundedMaxBytes = Number.isSafeInteger(maxBytes) && maxBytes > 0 ? maxBytes : CANVAS_FILE_PREVIEW_MAX_BYTES;
            const response = await window.authedFetch(
                `/api/v1/files/download?file_id=${encodeURIComponent(fileId)}&inline=true`,
                {
                    method: 'GET', cache: 'no-store',
                    headers: { 'accept': '*/*', 'Content-Type': null, 'Cache-Control': 'no-cache', Range: `bytes=0-${boundedMaxBytes}` },
                },
            );
            if (response.status === 416 && getCanvasFileResponseTotalBytes(response) === 0) {
                cancelCanvasFileResponse(response);
                return '';
            }
            if (!response.ok) {
                cancelCanvasFileResponse(response);
                throw new Error(t('files_preview_unavailable', 'This file is no longer available.'));
            }
            return readCanvasFileText(response, boundedMaxBytes);
        }

        async function loadSpreadsheetFromFile(fileId) {
            if (!fileId || typeof window.authedFetch !== 'function') {
                return { bytes: new ArrayBuffer(0), canvasRevision: 0, requiresRecalculation: false };
            }
            const response = await window.authedFetch(
                `/api/v1/files/canvas/spreadsheet/content?file_id=${encodeURIComponent(fileId)}`,
                { method: 'GET', cache: 'no-store', headers: { accept: '*/*', 'Content-Type': null, 'Cache-Control': 'no-cache' } },
            );
            if (!response.ok) {
                const detail = String((await response.json().catch(() => null))?.detail || '').trim();
                if (detail === 'spreadsheet_archive_too_complex') {
                    throw new Error(t('spreadsheet_archive_too_complex', 'This workbook is too large or complex to edit safely in the browser.'));
                }
                if (detail === 'spreadsheet_preview_too_large' || response.status === 413) {
                    throw new Error(t('spreadsheet_preview_too_large', 'This spreadsheet is too large to edit in the browser. Download it to continue.'));
                }
                throw new Error(t('files_preview_unavailable', 'This file is no longer available.'));
            }
            const declaredSize = Number(response.headers.get('Content-Length')) || 0;
            if (declaredSize > SPREADSHEET_PREVIEW_MAX_BYTES) {
                cancelCanvasFileResponse(response);
                throw new Error(formatT('spreadsheet_preview_too_large', 'This spreadsheet is too large to edit in the browser. Download it to continue.', {}));
            }
            const bytes = await response.arrayBuffer();
            if (bytes.byteLength > SPREADSHEET_PREVIEW_MAX_BYTES) {
                throw new Error(t('spreadsheet_preview_too_large', 'This spreadsheet is too large to edit in the browser. Download it to continue.'));
            }
            return {
                bytes,
                canvasRevision: Number(response.headers.get('X-Canvas-Revision')) || 0,
                requiresRecalculation: response.headers.get('X-Spreadsheet-Requires-Recalculation') === 'true',
            };
        }

        async function loadCanvasFileRecord(fileId) {
            if (!fileId || typeof window.authedFetch !== 'function') return null;
            const response = await window.authedFetch(`/api/v1/files/${encodeURIComponent(fileId)}`, {
                method: 'GET', credentials: 'include', cache: 'no-store', headers: { 'Cache-Control': 'no-cache' },
            });
            if (!response.ok) return null;
            try { return await response.json(); } catch (_) { return null; }
        }

        function getCanvasFileLoadFailureStatus(error, fallbackKey, fallback) {
            return error instanceof CanvasPreviewTooLargeError ? error.message : t(fallbackKey, fallback);
        }

        function detectContentTypeFromFileName(fileName) {
            const name = String(fileName || '').toLowerCase();
            if (name.endsWith('.csv')) return 'csv';
            if (name.endsWith('.tsv')) return 'tsv';
            if (name.endsWith('.xlsx')) return 'xlsx';
            if (name.endsWith('.xls')) return 'xls';
            if (name.endsWith('.mmd') || name.endsWith('.mermaid')) return 'mermaid';
            if (hasHtmlFileExtension(name)) return 'html';
            if (name.endsWith('.pdf')) return 'pdf';
            if (name.endsWith('.tex')) return 'latex';
            return 'markdown';
        }

        return Object.freeze({
            CANVAS_FILE_PREVIEW_MAX_BYTES,
            CanvasPreviewTooLargeError,
            getCanvasFilePreviewMaxBytes,
            loadContentFromFile,
            loadSpreadsheetFromFile,
            loadCanvasFileRecord,
            getCanvasFileLoadFailureStatus,
            detectContentTypeFromFileName,
        });
    }

    modules.fileLoading = Object.freeze({ create: createFileLoadingModule });
})(globalThis);
