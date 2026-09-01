(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};

    function createArgumentsModule({ normalizeContentType, hasExplicitCanvasContentType, contentTypes }) {
        function parseJsonSafe(raw) {
            if (raw == null) return null;
            if (typeof raw === 'object') return raw;
            if (typeof raw !== 'string') return null;
            const text = raw.trim();
            if (!text) return null;
            try {
                return JSON.parse(text);
            } catch (_) {
                return null;
            }
        }

        function extractCanvasArgs(rawArgs) {
            const args = parseJsonSafe(rawArgs) || (typeof rawArgs === 'object' ? rawArgs : null) || {};
            const content = args.content ?? args.markdown ?? args.text ?? '';
            const contentType = args.type ?? args.content_type ?? 'markdown';
            const fileId = args.file_id ?? args.fileId ?? args.id ?? '';
            const fileName = args.filename ?? args.file_name ?? args.fileName ?? '';
            return {
                content: typeof content === 'string' ? content : String(content ?? ''),
                contentType: normalizeContentType(contentType),
                fileId: fileId ? String(fileId) : '',
                fileName: fileName ? String(fileName) : '',
                hasContentType: hasExplicitCanvasContentType(args),
            };
        }

        function readJsonStringField(buffer, fieldName) {
            if (typeof buffer !== 'string' || !buffer || !fieldName) return null;
            const escapedName = String(fieldName).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const pattern = new RegExp(`"${escapedName}"\\s*:\\s*"`, 'i');
            const match = pattern.exec(buffer);
            if (!match) return null;

            let index = match.index + match[0].length;
            let value = '';
            let escaped = false;
            while (index < buffer.length) {
                const char = buffer[index];
                index += 1;
                if (escaped) {
                    switch (char) {
                        case 'n': value += '\n'; break;
                        case 'r': value += '\r'; break;
                        case 't': value += '\t'; break;
                        case 'b': value += '\b'; break;
                        case 'f': value += '\f'; break;
                        case '\\': value += '\\'; break;
                        case '/': value += '/'; break;
                        case '"': value += '"'; break;
                        case 'u': {
                            const hex = buffer.slice(index, index + 4);
                            if (/^[0-9a-fA-F]{4}$/.test(hex)) {
                                value += String.fromCharCode(parseInt(hex, 16));
                                index += 4;
                            } else {
                                value += 'u';
                            }
                            break;
                        }
                        default: value += char; break;
                    }
                    escaped = false;
                    continue;
                }
                if (char === '\\') {
                    escaped = true;
                    continue;
                }
                if (char === '"') return { value, complete: true };
                value += char;
            }
            if (escaped) value += '\\';
            return { value, complete: false };
        }

        function extractCanvasArgsFromBuffer(buffer) {
            const contentField = readJsonStringField(buffer, 'content') || readJsonStringField(buffer, 'markdown') || readJsonStringField(buffer, 'text');
            const typeField = readJsonStringField(buffer, 'type') || readJsonStringField(buffer, 'content_type');
            const fileIdField = readJsonStringField(buffer, 'file_id') || readJsonStringField(buffer, 'fileId') || readJsonStringField(buffer, 'id');
            const fileNameField = readJsonStringField(buffer, 'filename') || readJsonStringField(buffer, 'file_name') || readJsonStringField(buffer, 'fileName');
            const rawContentType = String(typeField?.value || '').toLowerCase().trim();
            const hasCompleteContentType = contentTypes.includes(rawContentType);
            return {
                content: contentField?.value || '',
                contentType: normalizeContentType(hasCompleteContentType ? rawContentType : ''),
                rawType: rawContentType,
                fileId: fileIdField?.value || '',
                fileName: fileNameField?.value || '',
                hasContentType: hasCompleteContentType,
            };
        }

        function hasCanvasContentArgument(args) {
            if (!args || typeof args !== 'object' || Array.isArray(args)) return false;
            return ['content', 'markdown', 'text'].some((key) => Object.prototype.hasOwnProperty.call(args, key));
        }

        function classifyCanvasResultKind(args, extracted = {}) {
            const rawType = String(args?.type || args?.content_type || '').trim().toLowerCase();
            if (rawType === 'view') return 'view';
            if (String(extracted.fileId || '').trim()) return 'edit';
            if (hasCanvasContentArgument(args)) return 'create';
            return 'unknown';
        }

        return Object.freeze({ parseJsonSafe, extractCanvasArgs, readJsonStringField, extractCanvasArgsFromBuffer, hasCanvasContentArgument, classifyCanvasResultKind });
    }

    modules.arguments = Object.freeze({ create: createArgumentsModule });
})(globalThis);
