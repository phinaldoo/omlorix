(function () {
    'use strict';

    /**
     * Ordered visual profiles for transcript attachment cards.
     *
     * Extension matches intentionally take precedence over MIME matches because
     * browsers and storage providers often report generic MIME metadata. Keep
     * the generic file profile last so it remains the unambiguous fallback.
     */
    const FILE_TYPE_PROFILES = Object.freeze([
        {
            id: 'pdf',
            extensions: ['pdf'],
            mimeTypes: ['application/pdf'],
            accentLight: '#E5484D',
            accentDark: '#FF8589',
        },
        {
            id: 'word',
            extensions: ['doc', 'docx', 'docm', 'dot', 'dotx', 'odt', 'rtf'],
            mimeTypes: [
                'application/msword',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'application/vnd.oasis.opendocument.text',
            ],
            accentLight: '#3B82F6',
            accentDark: '#8AB4F8',
        },
        {
            id: 'presentation',
            extensions: ['ppt', 'pptx', 'pptm', 'pot', 'potx', 'odp', 'key'],
            mimeTypes: [
                'application/vnd.ms-powerpoint',
                'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                'application/vnd.oasis.opendocument.presentation',
            ],
            accentLight: '#F76B15',
            accentDark: '#FFA057',
        },
        {
            id: 'spreadsheet',
            extensions: ['xls', 'xlsx', 'xlsm', 'xlsb', 'ods', 'numbers', 'csv', 'tsv'],
            mimeTypes: [
                'application/vnd.ms-excel',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'application/vnd.oasis.opendocument.spreadsheet',
                'text/csv',
                'text/tab-separated-values',
            ],
            accentLight: '#30A46C',
            accentDark: '#63D297',
        },
        {
            id: 'archive',
            extensions: ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'tgz'],
            mimeTypes: [
                'application/zip',
                'application/x-7z-compressed',
                'application/x-rar-compressed',
                'application/x-tar',
                'application/gzip',
            ],
            accentLight: '#8E6BBE',
            accentDark: '#C8A4F7',
        },
        {
            id: 'data',
            extensions: ['json', 'jsonl', 'xml', 'yaml', 'yml', 'sql', 'db', 'sqlite'],
            mimeTypes: ['application/json', 'application/xml', 'application/yaml', 'text/yaml', 'application/x-sql'],
            accentLight: '#0D9488',
            accentDark: '#5EEAD4',
        },
        {
            id: 'code',
            extensions: [
                'js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx', 'py', 'rb', 'php', 'java', 'kt',
                'swift', 'go', 'rs', 'c', 'h', 'cpp', 'cs', 'sh', 'ps1', 'css', 'scss',
                'sass', 'html', 'vue',
            ],
            mimeTypes: [
                'application/javascript',
                'text/javascript',
                'text/css',
                'text/html',
                'text/x-python',
                'text/x-shellscript',
            ],
            accentLight: '#6366F1',
            accentDark: '#A5B4FC',
        },
        {
            id: 'text',
            extensions: ['txt', 'md', 'markdown', 'log', 'tex', 'ini', 'cfg'],
            mimeTypes: ['text/plain', 'text/markdown', 'application/rtf'],
            accentLight: '#64748B',
            accentDark: '#CBD5E1',
        },
        {
            id: 'image',
            extensions: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'avif', 'heic', 'tif', 'tiff', 'psd'],
            mimeTypes: ['image/*'],
            accentLight: '#C026D3',
            accentDark: '#F0ABFC',
        },
        {
            id: 'audio',
            extensions: ['mp3', 'wav', 'aac', 'flac', 'ogg', 'oga', 'opus', 'm4a', 'wma'],
            mimeTypes: ['audio/*'],
            accentLight: '#7C3AED',
            accentDark: '#C4B5FD',
        },
        {
            id: 'video',
            extensions: ['mp4', 'webm', 'mov', 'avi', 'mkv', 'mpeg', 'mpg', 'wmv', 'ogv'],
            mimeTypes: ['video/*'],
            accentLight: '#E11D48',
            accentDark: '#FDA4AF',
        },
        {
            id: 'file',
            fallback: true,
            extensions: [],
            mimeTypes: [],
            accentLight: '#8B8D98',
            accentDark: '#A2A4AD',
        },
    ]);

    const DEFAULT_PROFILE = FILE_TYPE_PROFILES[FILE_TYPE_PROFILES.length - 1];
    const enhancedCards = new WeakMap();

    /** Extract a safe, lower-case extension from a user-visible filename. */
    function getExtension(fileName) {
        const normalized = String(fileName || '').trim().split(/[?#]/, 1)[0];
        const leaf = normalized.split(/[\\/]/).pop() || '';
        const dotIndex = leaf.lastIndexOf('.');
        return dotIndex > 0 && dotIndex < leaf.length - 1
            ? leaf.slice(dotIndex + 1).toLowerCase()
            : '';
    }

    /** Match exact MIME types and simple top-level wildcards such as image/*. */
    function matchesMimeType(pattern, mimeType) {
        if (!pattern || !mimeType) return false;
        if (pattern === mimeType) return true;
        return pattern.endsWith('/*') && mimeType.startsWith(pattern.slice(0, -1));
    }

    /** Resolve one profile, preferring filename extensions because upload MIME metadata may be generic. */
    function resolveProfile(fileName, rawMimeType) {
        const extension = getExtension(fileName);
        const mimeType = String(rawMimeType || '').trim().toLowerCase().split(';')[0];
        return FILE_TYPE_PROFILES.find((profile) => !profile.fallback && profile.extensions.includes(extension))
            || FILE_TYPE_PROFILES.find((profile) => (
                !profile.fallback
                && profile.mimeTypes.some((pattern) => matchesMimeType(pattern, mimeType))
            ))
            || DEFAULT_PROFILE;
    }

    /** Apply the active light/dark accent pair to an already-rendered card. */
    function applyProfile(card, descriptor) {
        const profile = resolveProfile(descriptor.fileName, descriptor.mimeType);
        card.dataset.fileCardType = profile.id;
        card.style.setProperty('--chat-file-accent-light', profile.accentLight);
        card.style.setProperty('--chat-file-accent-dark', profile.accentDark);
    }

    /** Create the five abstract lines used by option 9's portrait mini-preview. */
    function populateMiniPreview(preview) {
        const lines = Array.from({ length: 5 }, () => {
            const line = document.createElement('span');
            line.className = 'chat-file-card-preview-line';
            return line;
        });
        preview.replaceChildren(...lines);
        preview.setAttribute('aria-hidden', 'true');
    }

    /**
     * Upgrade a transcript-only attachment tile to the option 9 card.
     * The caller receives the dedicated preview button so preview and download
     * remain separate, valid interactive controls for keyboard users.
     */
    function enhance(card, options = {}) {
        if (!card) return card;
        const existing = enhancedCards.get(card);
        if (existing) return existing.openButton;

        const fileName = String(options.fileName || 'File');
        const descriptor = {
            fileName,
            mimeType: String(options.mimeType || ''),
        };
        const preview = card.querySelector('.inline-files-element-icon');
        const content = card.querySelector('.inline-files-element-content');
        if (!preview || !content) return card;

        card.classList.add('chat-file-card');
        preview.classList.add('chat-file-card-preview');
        content.classList.add('chat-file-card-content');
        populateMiniPreview(preview);

        const typeLabel = content.querySelector('.inline-files-element-content-bottom > :first-child');
        typeLabel?.classList.add('chat-file-card-type');

        const openButton = document.createElement('button');
        openButton.type = 'button';
        openButton.className = 'chat-file-card-open';
        openButton.append(preview, content);
        card.appendChild(openButton);

        const downloadButton = document.createElement('button');
        downloadButton.type = 'button';
        downloadButton.className = 'chat-file-card-download';
        downloadButton.setAttribute('aria-label', String(options.downloadLabel || `Download ${fileName}`));
        downloadButton.title = String(options.downloadTitle || 'Download');
        downloadButton.innerHTML = window.Icons?.download || '';
        card.appendChild(downloadButton);

        downloadButton.addEventListener('click', async (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (downloadButton.disabled || typeof options.onDownload !== 'function') return;

            downloadButton.disabled = true;
            downloadButton.setAttribute('aria-busy', 'true');
            card.classList.add('is-downloading');
            try {
                await options.onDownload();
            } catch (error) {
                options.onDownloadError?.(error);
            } finally {
                downloadButton.disabled = false;
                downloadButton.removeAttribute('aria-busy');
                card.classList.remove('is-downloading');
            }
        });

        enhancedCards.set(card, { descriptor, openButton, downloadButton });
        applyProfile(card, descriptor);
        return openButton;
    }

    window.ChatFileCards = Object.freeze({
        enhance,
        // Pure helpers are exposed for focused regression tests and future tooling.
        getExtension,
        resolveProfile,
    });
})();
