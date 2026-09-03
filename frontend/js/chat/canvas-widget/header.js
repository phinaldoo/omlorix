(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};

    function getPreviewHeaderIcon(name) {
        const iconSet = typeof Icons === 'object' ? Icons : (globalThis.Icons || {});
        return iconSet?.[name] || '';
    }

    function ensureCanvasPreviewHeader(panel) {
        if (!panel || panel.querySelector('.canvas-markdown-preview-header')) return;

        const resizer = document.createElement('div');
        resizer.className = 'canvas-markdown-preview-resizer';
        resizer.id = 'canvas-markdown-PreviewResizer';
        resizer.setAttribute('role', 'separator');
        resizer.setAttribute('tabindex', '0');
        resizer.setAttribute('aria-orientation', 'vertical');
        resizer.setAttribute('aria-label', 'Resize canvas preview');
        resizer.setAttribute('title', 'Resize canvas preview');
        resizer.setAttribute('data-i18n-attr', 'aria-label:canvas_resize_preview_aria;title:canvas_resize_preview_aria');

        const header = document.createElement('div');
        header.className = 'canvas-markdown-preview-header';
        header.innerHTML = `
            <div class="canvas-markdown-preview-header-left">
                <button class="om-button" id="canvas-markdown-PreviewClose" type="button" aria-label="Close canvas preview" title="Close canvas preview" data-i18n-attr="aria-label:canvas_close_preview_aria;title:canvas_close_preview_aria">
                    ${getPreviewHeaderIcon('close')}
                </button>
                <div class="canvas-markdown-preview-title-wrap">
                    <span class="canvas-markdown-preview-title" id="canvas-markdown-PreviewTitle" data-i18n="canvas_preview_title">Canvas Preview</span>
                    <span class="canvas-markdown-preview-status" id="canvas-markdown-PreviewStatus" data-i18n="canvas_preview_waiting">Waiting for canvas tool…</span>
                </div>
            </div>
            <div class="canvas-markdown-preview-header-right">
                <div class="canvas-html-view-toggle canvas-markdown-editor-view-toggle" id="canvas-html-ViewToggle" role="tablist" aria-label="Canvas view" data-i18n-attr="aria-label:canvas_view_mode_label">
                    <button class="canvas-html-view-toggle-btn canvas-markdown-editor-view-btn" id="canvas-html-ViewCodeBtn" type="button" role="tab" aria-selected="false" aria-label="Edit source" title="Edit source" data-i18n-attr="aria-label:canvas_edit_source_aria;title:canvas_edit_source_aria">
                        <span class="canvas-markdown-editor-view-btn-icon" aria-hidden="true">${getPreviewHeaderIcon('code')}</span>
                        <span class="canvas-markdown-editor-view-btn-label" data-i18n="code_block_tab_code">Code</span>
                    </button>
                    <button class="canvas-html-view-toggle-btn canvas-markdown-editor-view-btn active" id="canvas-html-ViewPreviewBtn" type="button" role="tab" aria-selected="true" aria-label="View preview" title="View preview" data-i18n-attr="aria-label:canvas_view_preview_aria;title:canvas_view_preview_aria">
                        <span class="canvas-markdown-editor-view-btn-icon" aria-hidden="true">${getPreviewHeaderIcon('eye')}</span>
                        <span class="canvas-markdown-editor-view-btn-label" data-i18n="code_block_tab_preview">Preview</span>
                    </button>
                </div>
                <div class="canvas-markdown-editor-header-controls" id="canvas-markdown-EditorControls" role="toolbar" aria-label="Document view" data-i18n-attr="aria-label:markdown_editor_document_view">
                    <div class="canvas-markdown-editor-view-toggle" role="tablist" aria-label="Document view" data-i18n-attr="aria-label:markdown_editor_document_view">
                        <button class="canvas-markdown-editor-view-btn" id="canvas-markdown-MarkdownTab" type="button" role="tab" aria-selected="false" aria-label="Markdown" title="Markdown" data-i18n-attr="aria-label:markdown_editor_tab_markdown;title:markdown_editor_tab_markdown">
                            <span class="canvas-markdown-editor-view-btn-icon" aria-hidden="true">${getPreviewHeaderIcon('list')}</span>
                            <span class="canvas-markdown-editor-view-btn-label" data-i18n="markdown_editor_tab_markdown">Markdown</span>
                        </button>
                        <button class="canvas-markdown-editor-view-btn active" id="canvas-markdown-EditorTab" type="button" role="tab" aria-selected="true" aria-label="Editor" title="Editor" data-i18n-attr="aria-label:markdown_editor_tab_editor;title:markdown_editor_tab_editor">
                            <span class="canvas-markdown-editor-view-btn-icon" aria-hidden="true">${getPreviewHeaderIcon('edit')}</span>
                            <span class="canvas-markdown-editor-view-btn-label" data-i18n="markdown_editor_tab_editor">Editor</span>
                        </button>
                    </div>
                </div>
                <div class="canvas-html-settings" id="canvas-html-Settings">
                    <button class="om-button canvas-html-settings-trigger" id="canvas-html-SettingsBtn" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="canvas-html-SettingsMenu" aria-label="HTML preview settings" title="HTML preview settings" data-i18n-attr="aria-label:canvas_html_preview_settings;title:canvas_html_preview_settings">
                        ${getPreviewHeaderIcon('settings')}
                    </button>
                    <div class="canvas-html-settings-menu" id="canvas-html-SettingsMenu" role="dialog" aria-label="HTML preview settings" data-i18n-attr="aria-label:canvas_html_preview_settings" hidden>
                        <label class="canvas-html-settings-menu-item" for="canvas-html-ExternalContentBtn">
                            <span class="canvas-html-settings-menu-icon" aria-hidden="true">${getPreviewHeaderIcon('globe')}</span>
                            <span class="canvas-html-settings-menu-label" data-i18n="canvas_html_external_content">External content</span>
                            <span class="toggle-switch">
                                <input class="toggle-input" id="canvas-html-ExternalContentBtn" type="checkbox" role="switch">
                                <span class="toggle-slider" aria-hidden="true"></span>
                            </span>
                        </label>
                        <label class="canvas-html-settings-menu-item" for="canvas-html-ScriptsBtn">
                            <span class="canvas-html-settings-menu-icon" aria-hidden="true">${getPreviewHeaderIcon('play')}</span>
                            <span class="canvas-html-settings-menu-label" data-i18n="canvas_html_interactions">Interactions (requires external content)</span>
                            <span class="toggle-switch">
                                <input class="toggle-input" id="canvas-html-ScriptsBtn" type="checkbox" role="switch" disabled>
                                <span class="toggle-slider" aria-hidden="true"></span>
                            </span>
                        </label>
                    </div>
                </div>
                    <button class="om-button canvas-html-reload-btn" id="canvas-html-ReloadBtn" type="button" aria-label="Reload preview" title="Reload preview" data-i18n-attr="aria-label:code_block_reload_preview;title:code_block_reload_preview">
                    ${getPreviewHeaderIcon('redo_circle')}
                </button>
                    <button class="om-button canvas-markdown-copy-btn is-disabled" id="canvas-markdown-CopyBtn" type="button" aria-label="Copy raw code" title="Copy raw code" data-i18n-attr="aria-label:canvas_copy_raw_code_aria;title:canvas_copy_raw_code_aria" aria-disabled="true" disabled>
                    ${getPreviewHeaderIcon('copy')}
                </button>
                    <button class="om-button canvas-html-fullscreen-btn" id="canvas-html-FullscreenBtn" type="button" aria-label="Fullscreen preview" title="Fullscreen preview" data-i18n-attr="aria-label:canvas_fullscreen_preview_aria;title:canvas_fullscreen_preview_aria">
                    ${getPreviewHeaderIcon('expand')}
                </button>
                    <button class="om-button canvas-markdown-share-btn is-disabled" id="canvas-markdown-ShareBtn" type="button" aria-label="Share canvas" title="Share canvas" data-i18n-attr="aria-label:canvas_share_button_enabled;title:canvas_share_button_enabled" hidden>
                    ${getPreviewHeaderIcon('share')}
                </button>
                <div class="slide-presentation-preview-download-controls">
                    <select class="slide-presentation-preview-download-select" id="canvas-markdown-DownloadFormat" aria-label="Download format" data-i18n-attr="aria-label:canvas_download_format_aria" hidden disabled>
                        <option value="md" data-i18n="canvas_markdown_download_md">MD</option>
                        <option value="pdf" data-i18n="canvas_markdown_download_pdf">PDF</option>
                    </select>
                    <a class="om-button disabled" id="canvas-markdown-PreviewDownload" href="#" aria-label="Download" title="Download" data-i18n-attr="aria-label:files_preview_download;title:files_preview_download" aria-disabled="true" tabindex="-1">
                        ${getPreviewHeaderIcon('download')}
                    </a>
                </div>
            </div>
        `;

        const previewTrack = panel.querySelector('#canvas-markdown-PreviewTrack');
        panel.insertBefore(resizer, previewTrack || panel.firstChild);
        panel.insertBefore(header, previewTrack || panel.firstChild);
    }

    modules.header = Object.freeze({ ensureCanvasPreviewHeader, getPreviewHeaderIcon });
})(globalThis);
