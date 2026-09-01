/* ==========================================================================
   Slide Presentation — Chat Integration
   ========================================================================== */

(function () {
    'use strict';

    // ── DOM refs ──────────────────────────────────────────────────────────
    const previewPanel          = document.getElementById('slide-presentation-PreviewPanel');
    const previewResizer        = document.getElementById('slide-presentation-PreviewResizer');
    const previewTitle          = document.getElementById('slide-presentation-PreviewTitle');
    const previewStatus         = document.getElementById('slide-presentation-PreviewStatus');
    const previewClose          = document.getElementById('slide-presentation-PreviewClose');
    const previewGenerating     = document.getElementById('slide-presentation-PreviewGenerating');
    const previewGeneratingText = document.getElementById('slide-presentation-PreviewGeneratingText');
    const previewMain           = document.getElementById('slide-presentation-PreviewMain');
    const previewUpdating       = document.getElementById('slide-presentation-PreviewUpdating');
    const previewUpdatingSpinner = document.getElementById('slide-presentation-PreviewUpdatingSpinner');
    const previewUpdatingText   = document.getElementById('slide-presentation-PreviewUpdatingText');
    const previewUpdateRetry    = document.getElementById('slide-presentation-PreviewUpdateRetry');
    const previewSlidesTrack    = document.getElementById('slide-presentation-PreviewSlidesTrack');
    const previewNav            = document.getElementById('slide-presentation-PreviewNav');
    const previewCounter        = document.getElementById('slide-presentation-PreviewCounter');
    const previewThumbnails     = document.getElementById('slide-presentation-PreviewThumbnails');
    const previewDownloadBtn    = document.getElementById('slide-presentation-PreviewDownloadBtn');
    const previewDownloadFormat = document.getElementById('slide-presentation-PreviewDownloadFormat');
    const previewDownloadBtnDefaultHtml = previewDownloadBtn ? previewDownloadBtn.innerHTML : '';
    const previewSidebarToggle  = document.getElementById('slide-presentation-PreviewSidebarToggle');
    const previewPresent        = document.getElementById('slide-presentation-PreviewPresent');
    const previewSidebar        = document.getElementById('slide-presentation-PreviewSidebar');
    const previewEdit           = document.getElementById('slide-presentation-PreviewEdit');
    const editorOverlay         = document.getElementById('slide-presentation-EditorOverlay');
    const editorHost            = document.getElementById('slide-presentation-EditorHost');
    const editorFallbackClose   = document.getElementById('slide-presentation-EditorFallbackClose');

    function t(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function tf(key, fallback, vars) {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(fallback || key).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars && Object.prototype.hasOwnProperty.call(vars, token) ? vars[token] : '';
            return value == null ? '' : String(value);
        });
    }

    // Slideshow overlay refs
    const ssOverlay     = document.getElementById('slide-presentation-SlideshowOverlay');
    const ssLoader      = document.getElementById('slide-presentation-SlideshowLoader');
    const ssLoaderText  = ssLoader?.querySelector('.slide-presentation-slideshow-loader-text');
    const ssLoaderBar   = document.getElementById('slide-presentation-SlideshowLoaderBar');
    const ssLoaderCount = document.getElementById('slide-presentation-SlideshowLoaderCount');
    const ssStage       = document.getElementById('slide-presentation-SlideshowStage');
    const ssImg         = document.getElementById('slide-presentation-SlideshowImg');
    const ssClose       = document.getElementById('slide-presentation-SsClose');
    const ssPrev        = document.getElementById('slide-presentation-SsPrev');
    const ssNext        = document.getElementById('slide-presentation-SsNext');
    const ssFullscreen  = document.getElementById('slide-presentation-SsFullscreen');
    const ssCurrent     = document.getElementById('slide-presentation-SsCurrent');
    const ssTotal       = document.getElementById('slide-presentation-SsTotal');
    const ssBackdrop    = document.getElementById('slide-presentation-SlideshowBackdrop');

    // ── State ─────────────────────────────────────────────────────────────
    let slidePresentationSlides = [];          // completed slide HTML strings
    let slidePresentationStyles = '';
    let slidePresentationCurrentIndex = 0;     // index of the slide currently centered
    let slidePresentationHtmlBuffer = '';
    let slidePresentationPreviewVisible = false;
    let slidePresentationFileId = null;
    let slidePresentationPresentationId = null;
    let _slideItems = [];         // DOM .slide-presentation-preview-slide-item elements
    let _programmaticScrollActive = false;
    let _programmaticScrollClearTimer = null;
    let _previewTrackScrollRafId = null;
    let _previewTrackScrollHandlerAttached = false;
    let _previewAutoFollowGeneration = true;
    let _previewResizeActive = false;
    let _previewResizePointerId = null;
    let _previewResizeScaleRafId = null;
    let _previewSidebarUserInteracted = false;
    let _previewSidebarAutoOpened = false;
    let _generationInProgress = false;
    let _generationPreviewInitialized = false;
    let _pendingHtmlDelta = '';
    let _htmlDeltaRafId = null;
    let _editorOpen = false;
    let _editorReturnFocus = null;
    let _editorOpenToken = 0;
    let _editorInertElements = [];
    let _editorPreviewRefreshToken = 0;
    let _editorPreviewRetry = null;

    // ResizeObserver-driven scaling to avoid race conditions during panel/sidebar transitions
    let _scaleObservers = [];

    // Layout/scale retry tuning
    const _IFRAME_SCALE_RETRY_MAX = 12;
    const _IFRAME_SCALE_RETRY_DELAY_MS = 50;
    const _SLIDE_IMAGE_DECODE_TIMEOUT_MS = 15000;
    const _SLIDE_PREVIEW_CSP = "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none'; frame-src 'none'; object-src 'none'; script-src 'none'";
    const _SLIDESHOW_SWITCH_ANIMATION_MS = 400;
    const _SLIDE_SELECTION_HYSTERESIS_RATIO = 0.035;
    const _SLIDE_SELECTION_HYSTERESIS_MIN_PX = 16;
    const _SLIDE_SELECTION_HYSTERESIS_MAX_PX = 40;
    const _PREVIEW_DESKTOP_BREAKPOINT = 900;
    const _PREVIEW_RESIZE_STEP = 16;
    const _PREVIEW_RESIZE_LARGE_STEP = 48;

    // Slideshow state
    let ssIndex = 0;
    let ssImages = [];   // URLs used by the slideshow (real image URLs once available)
    let ssOpen = false;
    let ssNavigationToken = 0;
    let ssPreviouslyFocused = null;
    let _ssHideTimer = null;
    let _ssSwitchAnimationTimer = null;

    // Rendered slide images (from backend after export)
    let slidePresentationSlideImages = [];  // array of blob URLs generated from authenticated fetches
    let slidePresentationSlideImageRevokers = [];
    let slidePresentationImageLoadToken = 0;
    let slidePresentationRenderedRevision = 0;

    // Generating card tracking
    let _generatingCard = null;

    let _activeMessageId = null;

    // ══════════════════════════════════════════════════════════════════════
    function _getAssistantMessageIdForElement(element) {
        const messageEl = element && typeof element.closest === 'function'
            ? element.closest('[id^="a-"]')
            : null;
        if (!messageEl || !messageEl.id) {
            return '';
        }
        return messageEl.id.startsWith('a-') ? messageEl.id.slice(2) : '';
    }

    /**
     * Return the transient card only while it still belongs to the live
     * transcript and, when known, the assistant message receiving the event.
     * Chat navigation replaces the transcript DOM wholesale, so retaining a
     * truthy reference is not enough to prove that upgrading the card will be
     * visible to the user.
     */
    function _getConnectedGeneratingCard(messageId = '') {
        if (!_generatingCard) return null;

        const expectedMessageId = String(messageId || '').trim();
        const cardMessageId = _getAssistantMessageIdForElement(_generatingCard);
        const trackedMessageId = String(_activeMessageId || '').trim();
        const belongsToAnotherMessage = Boolean(
            expectedMessageId
            && (
                (cardMessageId && cardMessageId !== expectedMessageId)
                || (!cardMessageId && trackedMessageId && trackedMessageId !== expectedMessageId)
            )
        );

        if (belongsToAnotherMessage) return null;
        if (!_generatingCard.isConnected) {
            _generatingCard = null;
            _activeMessageId = null;
            return null;
        }
        return _generatingCard;
    }

    function _setAssistantMessageListVisible(messageRef, visible) {
        let messageEl = null;
        if (typeof messageRef === 'string' && messageRef) {
            messageEl = document.getElementById('a-' + messageRef);
        } else if (messageRef && typeof messageRef.closest === 'function') {
            messageEl = messageRef.closest('[id^="a-"]');
        }
        if (!messageEl) {
            return;
        }
        const listEl = messageEl.querySelector('.assistant-message-list');
        if (listEl) {
            listEl.hidden = !visible;
            listEl.style.display = visible ? '' : 'none';
            listEl.setAttribute('aria-hidden', visible ? 'false' : 'true');
        }
    }

    function _rwEsc(str) {
        const d = document.createElement('div');
        d.textContent = str;
        return d.innerHTML;
    }

    function _normalizePresentationContext(options = {}) {
        return {
            fileId: String(options.fileId || options.file_id || '').trim(),
            presentationId: String(options.presentationId || options.presentation_id || '').trim(),
            title: String(options.title || t('slide_presentation_default_title', 'Presentation')).trim() || t('slide_presentation_default_title', 'Presentation'),
            slideCount: Number(options.slideCount ?? options.slide_count) || 0,
            operation: String(options.operation || 'created').trim() || 'created',
        };
    }

    function _setPreviewDownloadBusy(isBusy) {
        window.chatDownloadControls?.setDownloadBusy?.({
            button: previewDownloadBtn,
            select: previewDownloadFormat,
            busy: isBusy,
            enabled: Boolean(slidePresentationFileId || slidePresentationPresentationId),
            defaultHtml: previewDownloadBtnDefaultHtml,
            disabledClass: 'disabled',
            manageTabIndex: true,
            busyLabel: t('slide_presentation_downloading', 'Downloading...'),
            idleLabel: t('files_preview_download', 'Download'),
        });
    }

    function _setPreviewDownloadEnabled(enabled) {
        window.chatDownloadControls?.setDownloadControlsEnabled?.({
            button: previewDownloadBtn,
            select: previewDownloadFormat,
            enabled,
            defaultHtml: previewDownloadBtnDefaultHtml,
            disabledClass: 'disabled',
            manageTabIndex: true,
            label: t('files_preview_download', 'Download'),
        });
    }

    function _setPreviewEditEnabled(enabled) {
        if (!previewEdit) return;
        previewEdit.disabled = !enabled;
        previewEdit.setAttribute('aria-disabled', enabled ? 'false' : 'true');
    }

    /**
     * Centralize reduced-motion checks so preview and slideshow transitions both
     * respect the browser's operating-system preference.
     */
    function _shouldReduceMotion() {
        try {
            return Boolean(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
        } catch (_) {
            return false;
        }
    }

    /**
     * Crossfades presenter-mode slide changes after the new asset has been
     * swapped in. This keeps the transition smooth without showing a blank frame
     * while images are preloading.
     */
    function _playSlideshowSwitchAnimation(targetEl, nextIndex, previousIndex) {
        if (!targetEl || _shouldReduceMotion()) return;

        if (_ssSwitchAnimationTimer) {
            clearTimeout(_ssSwitchAnimationTimer);
            _ssSwitchAnimationTimer = null;
        }

        targetEl.classList.remove('switching-in', 'switching-back');
        if (Number(nextIndex) < Number(previousIndex)) {
            targetEl.classList.add('switching-back');
        }

        void targetEl.offsetWidth;
        targetEl.classList.add('switching-in');

        _ssSwitchAnimationTimer = setTimeout(() => {
            targetEl.classList.remove('switching-in', 'switching-back');
            _ssSwitchAnimationTimer = null;
        }, _SLIDESHOW_SWITCH_ANIMATION_MS);
    }

    /**
     * Remove transient transition classes before rebuilding the preview panel or
     * closing presenter mode.
     */
    function _clearSlideSwitchAnimations() {
        if (_ssSwitchAnimationTimer) {
            clearTimeout(_ssSwitchAnimationTimer);
            _ssSwitchAnimationTimer = null;
        }

        if (ssOverlay) {
            ssOverlay.querySelectorAll('.switching-in, .switching-back').forEach((el) => {
                el.classList.remove('switching-in', 'switching-back');
            });
        }
    }

    function _setCompletionCardContext(card, options = {}) {
        if (!card) return null;
        const context = _normalizePresentationContext(options);

        if (context.fileId) card.dataset.fileId = context.fileId;
        else delete card.dataset.fileId;

        if (context.presentationId) card.dataset.presentationId = context.presentationId;
        else delete card.dataset.presentationId;

        card.dataset.title = context.title;
        card.dataset.slideCount = String(context.slideCount || 0);
        card.dataset.operation = context.operation;
        return context;
    }

    function _getCompletionCardContext(card) {
        if (!card) return null;
        return _normalizePresentationContext({
            fileId: card.dataset.fileId || '',
            presentationId: card.dataset.presentationId || '',
            title: card.dataset.title || t('slide_presentation_default_title', 'Presentation'),
            slideCount: card.dataset.slideCount || 0,
            operation: card.dataset.operation || 'created',
        });
    }

    /** Refresh every in-memory card for a deck after its editable source changes. */
    function _refreshStoredPresentationContext(options = {}) {
        const updated = _normalizePresentationContext(options);
        if (!updated.presentationId) return;

        document.querySelectorAll('.slide-presentation-completion-card').forEach(card => {
            const current = _getCompletionCardContext(card);
            if (current?.presentationId !== updated.presentationId) return;
            const merged = {
                ...updated,
                operation: current.operation,
            };
            _setCompletionCardContext(card, merged);
            const title = card.querySelector('.slide-presentation-completion-title');
            if (title && current.operation !== 'updated') {
                title.textContent = tf('slide_presentation_ready_title', '{title} ready', {
                    title: updated.title,
                });
            }
        });
    }

    /** Resolve mutable title, artifact identity, and slide count from the server. */
    async function _resolveLatestPresentationContext(options = {}) {
        const fallback = _normalizePresentationContext(options);
        const lookupId = fallback.fileId || fallback.presentationId;
        if (!lookupId || typeof window.authedFetch !== 'function') return fallback;
        try {
            const response = await window.authedFetch(
                `/api/v1/presentations/by-file/${encodeURIComponent(lookupId)}`
            );
            if (!response?.ok) return fallback;
            const payload = await response.json();
            return _normalizePresentationContext({
                // Normalize the API's snake_case response explicitly.  A
                // simple object spread would leave stale fallback camelCase
                // fields in place, and the normalizer intentionally checks
                // those fields first.
                fileId: payload.file_id || fallback.fileId,
                presentationId: payload.presentation_id || fallback.presentationId,
                title: payload.title || fallback.title,
                slideCount: payload.slide_count ?? fallback.slideCount,
                operation: fallback.operation,
            });
        } catch (error) {
            console.warn('[slide-presentation] Could not refresh presentation context', error);
            return fallback;
        }
    }

    function _isActivePresentationContext(context) {
        const normalized = _normalizePresentationContext(context || {});
        if (normalized.presentationId) {
            return normalized.presentationId === String(slidePresentationPresentationId || '').trim();
        }
        if (normalized.fileId) {
            return normalized.fileId === String(slidePresentationFileId || '').trim();
        }
        return false;
    }

    async function _openPresentationContext(options = {}) {
        const context = _normalizePresentationContext(options);
        if (!context.presentationId && !context.fileId) {
            toggleSlidePresentationPreview();
            _syncAllCompletionCardTexts();
            return;
        }

        if (_isActivePresentationContext(context)) {
            if (slidePresentationPreviewVisible) {
                hidePreviewPanel();
            } else if (_slideItems.length > 0 || slidePresentationSlideImages.length > 0 || (_generationInProgress && _generationPreviewInitialized)) {
                _setPanelVisible(true);
            } else if (context.presentationId) {
                await openExistingPresentationPreview(context);
            }
            _syncAllCompletionCardTexts();
            return;
        }

        await openExistingPresentationPreview(context);
        _syncAllCompletionCardTexts();
    }

    /**
     * Render presentation artifacts with the same DOM primitives as Canvas
     * files. Presentation-specific classes remain as state hooks, while the
     * shared Canvas classes own the card's sizing, spacing, typography, and
     * responsive button layout.
     */
    function _renderCompletionCardState(card, options = {}) {
        if (!card) return;

        const status = String(options.status || 'ready');
        const icon = status === 'error'
            ? (Icons.error || Icons.warning || Icons.file)
            : (Icons.desktop || Icons.file);
        const buttonLabel = status === 'generating'
            ? t('slide_presentation_view_preview', 'View Preview')
            : t('slide_presentation_view_presentation', 'View Presentation');

        card.className = 'slide-presentation-completion-card canvas-markdown-result-widget';
        if (status !== 'ready') card.classList.add(status);
        card.dataset.presentationStatus = status;
        card.innerHTML =
            '<div class="slide-presentation-completion-header canvas-markdown-result-header">' +
                '<div class="slide-presentation-completion-icon canvas-markdown-result-icon" aria-hidden="true">' +
                    icon +
                '</div>' +
                '<div class="slide-presentation-completion-info canvas-markdown-result-meta">' +
                    '<div class="slide-presentation-completion-title canvas-markdown-result-title">' + _rwEsc(options.title || '') + '</div>' +
                    '<div class="slide-presentation-completion-sub canvas-markdown-result-sub">' + _rwEsc(options.subtitle || '') + '</div>' +
                '</div>' +
            '</div>' +
            (status === 'error' ? '' :
                '<button class="slide-presentation-completion-view-btn canvas-markdown-result-open-btn" type="button" aria-pressed="false">' +
                    Icons.eye +
                    '<span class="slide-presentation-completion-view-text canvas-markdown-result-open-label">' + _rwEsc(buttonLabel) + '</span>' +
                '</button>');
    }

    /** Insert result cards through the same assistant-widget shell as Canvas. */
    function _insertCompletionCard(parent, card) {
        if (!parent || !card) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'assistant-widget';
        wrapper.dataset.widgetType = 'slide_presentation_result';
        wrapper.appendChild(card);

        if (typeof appendBeforeAssistantList === 'function') {
            appendBeforeAssistantList(parent, wrapper);
            return;
        }
        const listDiv = parent.querySelector('.assistant-message-list');
        if (listDiv && listDiv.parentNode === parent) parent.insertBefore(wrapper, listDiv);
        else parent.appendChild(wrapper);
    }

    /**
     * Remove only the transient file box owned by the failed generation.
     * Completed presentation cards in the same assistant message are left
     * untouched, which matters when one response creates multiple artifacts.
     */
    function _removeGeneratingCard(messageId) {
        const cards = new Set();
        if (_generatingCard) cards.add(_generatingCard);

        const message = messageId ? document.getElementById('a-' + messageId) : null;
        message?.querySelectorAll(
            '.slide-presentation-completion-card[data-presentation-status="generating"]'
        ).forEach(card => cards.add(card));

        cards.forEach(card => {
            const wrapper = card.closest('.assistant-widget[data-widget-type="slide_presentation_result"]');
            (wrapper || card).remove();
        });
        _generatingCard = null;
    }

    function _updateCompletionCardText(card) {
        if (!card) return;
        const btn = card.querySelector('.slide-presentation-completion-view-btn');
        const textEl = card.querySelector('.slide-presentation-completion-view-text');
        if (!btn || !textEl) return;
        const context = _getCompletionCardContext(card);
        const hasBoundContext = context && (context.fileId || context.presentationId);
        const shouldHide = hasBoundContext
            ? (_isActivePresentationContext(context) && slidePresentationPreviewVisible)
            : slidePresentationPreviewVisible;
        const isGenerating = card.dataset.presentationStatus === 'generating';
        textEl.textContent = shouldHide
            ? t('slide_presentation_hide_preview', 'Hide Preview')
            : (isGenerating
                ? t('slide_presentation_view_preview', 'View Preview')
                : t('slide_presentation_view_presentation', 'View Presentation'));
        btn.classList.toggle('is-open', shouldHide);
        btn.setAttribute('aria-pressed', shouldHide ? 'true' : 'false');
    }

    function _makeCompletionCardBtn(card, options = {}) {
        const btn = card.querySelector('.slide-presentation-completion-view-btn');
        if (btn) {
            btn.addEventListener('click', async () => {
                try {
                    // Card datasets are refreshed after editor renders. Read
                    // them at click time instead of retaining creation-time
                    // title and slide-count values in this closure.
                    await _openPresentationContext(_getCompletionCardContext(card) || options);
                } catch (error) {
                    console.error('[slide-presentation] Failed to open presentation preview from completion card', error);
                    if (typeof window.notifyError === 'function') {
                        window.notifyError(error?.message || t('slide_presentation_preview_open_failed', 'Failed to open presentation preview.'));
                    }
                } finally {
                    _syncAllCompletionCardTexts();
                }
            });
        }
    }

    function _addGeneratingCard(messageId, options = {}) {
        let parent = null;
        if (messageId) parent = document.getElementById('a-' + messageId);
        if (!parent) parent = document.getElementById('chatAreaContainer');
        if (!parent) return;

        const card = document.createElement('div');
        _renderCompletionCardState(card, {
            status: 'generating',
            title: options.title || t('slide_presentation_default_title', 'Presentation'),
            subtitle: t('slide_presentation_generating_title', 'Generating presentation...'),
        });

        _makeCompletionCardBtn(card);
        _insertCompletionCard(parent, card);

        _generatingCard = card;
        _activeMessageId = String(messageId || _getAssistantMessageIdForElement(card) || '') || null;
    }

    function _updateGeneratingCard(options = {}) {
        const card = _getConnectedGeneratingCard();
        if (!card) return;
        const title = card.querySelector('.slide-presentation-completion-title');
        const subtitle = card.querySelector('.slide-presentation-completion-sub');
        if (title && options.title) title.textContent = String(options.title);
        if (subtitle && options.subtitle) subtitle.textContent = String(options.subtitle);
    }

    function _isActiveGenerationForMessage(messageId) {
        const card = _getConnectedGeneratingCard(messageId);
        if (!_generationInProgress && !card) return false;
        const activeMessageId = String(
            _activeMessageId || _getAssistantMessageIdForElement(card) || ''
        );
        const candidateMessageId = String(messageId || '');
        return Boolean(activeMessageId) && activeMessageId === candidateMessageId;
    }

    function _addCompletionCard(messageId, options = {}) {
        const context = _normalizePresentationContext(options);
        const completionTitle = context.operation === 'updated'
            ? t('slide_presentation_updated', 'Presentation updated')
            : tf('slide_presentation_ready_title', '{title} ready', {
                title: context.title || t('slide_presentation_default_title', 'Presentation'),
            });
        const completionSub = context.operation === 'updated'
            ? t('slide_presentation_updated_ready_desc', 'The latest version is ready in the preview panel.')
            : t('slide_presentation_ready_desc', 'Download or view from the preview panel.');

        // Upgrade only a card that still lives in this message's transcript.
        // A detached card retained across navigation must never consume the
        // completion event, because changing it would update invisible DOM.
        const generatingCard = _getConnectedGeneratingCard(messageId);
        if (generatingCard) {
            const card = generatingCard;
            _generatingCard = null;
            _renderCompletionCardState(card, {
                status: 'ready',
                title: completionTitle,
                subtitle: completionSub,
            });
            _setCompletionCardContext(card, context);
            _makeCompletionCardBtn(card, context);
            _updateCompletionCardText(card);
            _syncAllCompletionCardTexts();
            return;
        }

        let parent = null;
        if (messageId) parent = document.getElementById('a-' + messageId);
        if (!parent) parent = document.getElementById('chatAreaContainer');
        if (!parent) return;

        const card = document.createElement('div');
        _renderCompletionCardState(card, {
            status: 'ready',
            title: completionTitle,
            subtitle: completionSub,
        });

        _setCompletionCardContext(card, context);
        _makeCompletionCardBtn(card, context);
        _updateCompletionCardText(card);

        _insertCompletionCard(parent, card);
    }

    function renderSlidePresentationErrorBlock(messageId, meta) {
        if (!meta) {
            return;
        }
        // History restoration can render an old error while a newer message is
        // actively generating. Only the matching live generation owns the
        // partial preview and may discard it.
        if (!_isActiveGenerationForMessage(messageId)) return;
        _discardFailedGenerationPreview();
        _removeGeneratingCard(messageId);
        _finishGenerationTracking();
        _setAssistantMessageListVisible(messageId, true);
    }

    /**
     * Clean up a live presentation when the surrounding response stream ends
     * unexpectedly before its feature-specific terminal event arrives. The
     * guard preserves an already completed presentation preview when a later,
     * unrelated part of the assistant response fails.
     */
    function handleStreamEnd(messageId) {
        if (!_isActiveGenerationForMessage(messageId)) return;
        _discardFailedGenerationPreview();
        _removeGeneratingCard(messageId);
        _finishGenerationTracking();
        _setAssistantMessageListVisible(messageId, true);
        _activeMessageId = null;
    }

    // ══════════════════════════════════════════════════════════════════════
    // Preview Panel
    // ══════════════════════════════════════════════════════════════════════

    // ── Phase-aware generating state helpers ─────────────────────────────
    let _genPhase = 'styles'; // 'styles' | 'slides' | 'finalizing'

    function _setPreviewBusy(busy) {
        if (previewMain) previewMain.setAttribute('aria-busy', busy ? 'true' : 'false');
        if (previewPanel) previewPanel.classList.toggle('is-generating', Boolean(busy));
    }

    /**
     * Present close-time editor rendering as a non-destructive overlay. The
     * last complete slide revision stays visible until every replacement image
     * has loaded, while actions that could export or present stale derivatives
     * are temporarily disabled.
     */
    function _setEditorPreviewRefreshState(state = 'idle', message = '') {
        const isBusy = state === 'busy';
        const isError = state === 'error';
        const text = message || (isError
            ? t('slide_presentation_editor_render_failed', 'Preview update failed')
            : t('slide_presentation_editor_rendering', 'Updating preview…'));

        previewMain?.classList.toggle('is-updating', isBusy);
        previewUpdating?.classList.toggle('hidden', state === 'idle');
        previewUpdating?.classList.toggle('error', isError);
        previewUpdating?.setAttribute('aria-hidden', state === 'idle' ? 'true' : 'false');
        if (previewUpdating) previewUpdating.hidden = state === 'idle';
        if (previewUpdatingText) previewUpdatingText.textContent = text;
        if (previewUpdatingSpinner) previewUpdatingSpinner.hidden = !isBusy;
        if (previewUpdateRetry) previewUpdateRetry.classList.toggle('hidden', !isError);
        if (isBusy) updatePreviewStatus(text, false, 'rendering');
        if (isError) updatePreviewStatus(text, false, 'warning');
        _setPreviewBusy(isBusy);

        if (isBusy) {
            if (previewPresent) previewPresent.disabled = true;
            _setPreviewEditEnabled(false);
            _setPreviewDownloadEnabled(false);
            return;
        }

        const hasPreview = _slideItems.length > 0;
        // A failed refresh leaves the last-good preview visible so the user
        // can retry without a blank sidebar. That artifact is deliberately
        // non-exportable/non-presentable because it may predate editor changes.
        // Editing remains available so the source can still be corrected.
        if (previewPresent) previewPresent.disabled = isError || !hasPreview;
        _setPreviewEditEnabled(Boolean(slidePresentationPresentationId));
        _setPreviewDownloadEnabled(!isError && Boolean(slidePresentationFileId));
    }

    /**
     * Batch token-level HTML updates to one mutation pass per animation frame.
     * Provider streams can deliver dozens of chunks per second; rebuilding a
     * sandboxed iframe for every chunk would make the progressive preview less
     * responsive than the loading state it replaces.
     */
    function _flushPendingHtmlDelta() {
        if (_htmlDeltaRafId) {
            cancelAnimationFrame(_htmlDeltaRafId);
            _htmlDeltaRafId = null;
        }
        const delta = _pendingHtmlDelta;
        _pendingHtmlDelta = '';
        if (delta) appendHtmlDelta(delta);
    }

    function _queueHtmlDelta(delta) {
        if (!delta) return;
        _pendingHtmlDelta += delta;
        if (_htmlDeltaRafId) return;
        _htmlDeltaRafId = requestAnimationFrame(() => {
            _htmlDeltaRafId = null;
            const pending = _pendingHtmlDelta;
            _pendingHtmlDelta = '';
            if (pending) appendHtmlDelta(pending);
        });
    }

    function _resetPendingHtmlDelta() {
        if (_htmlDeltaRafId) cancelAnimationFrame(_htmlDeltaRafId);
        _htmlDeltaRafId = null;
        _pendingHtmlDelta = '';
    }

    function _setGenPhase(phase) {
        _genPhase = phase;
        const label = document.getElementById('slide-presentation-PreviewGeneratingText');
        const icon = document.getElementById('slide-presentation-GenIcon');
        if (phase === 'styles') {
            if (label) label.textContent = t('slide_presentation_generating_design_system', 'Generating design system…');
            if (icon) icon.innerHTML = Icons.loading_circle;
        } else if (phase === 'slides') {
            if (label) label.textContent = t('slide_presentation_building_slides_progress', 'Building slides…');
            if (icon) icon.innerHTML = Icons.desktop;
        } else if (phase === 'finalizing') {
            if (label) label.textContent = t('slide_presentation_finalizing_progress', 'Finalizing presentation…');
            if (icon) icon.innerHTML = Icons.check;
        }
    }

    function showPreviewPanel(title) {
        if (_editorOpen) closePresentationEditor();
        _editorPreviewRefreshToken += 1;
        _editorPreviewRetry = null;
        _clearSlideSwitchAnimations();
        slidePresentationSlides = [];
        slidePresentationStyles = '';
        slidePresentationCurrentIndex = 0;
        slidePresentationHtmlBuffer = '';
        _resetPendingHtmlDelta();
        slidePresentationFileId = null;
        slidePresentationPresentationId = null;
        slidePresentationRenderedRevision = 0;
        _revokeSlideImages();
        _disconnectScaleObservers();
        _slideItems = [];
        _destroySlideObserver();
        _previewSidebarUserInteracted = false;
        _previewSidebarAutoOpened = false;
        _previewAutoFollowGeneration = true;

        if (previewTitle) previewTitle.textContent = title || t('slide_presentation_generating', 'Generating...');
        if (previewStatus) {
            previewStatus.textContent = t('slide_presentation_preparing_styles', 'Preparing styles');
            previewStatus.className = 'slide-presentation-preview-panel-status canvas-markdown-preview-status generating';
        }
        if (previewThumbnails) previewThumbnails.innerHTML = '';
        if (previewSlidesTrack) previewSlidesTrack.innerHTML = '';
        if (previewGenerating) previewGenerating.classList.remove('hidden');
        if (previewNav) previewNav.classList.remove('visible');
        if (previewDownloadBtn) previewDownloadBtn.removeAttribute('href');
        _setPreviewDownloadEnabled(false);
        _setPreviewEditEnabled(false);
        if (previewSidebarToggle) previewSidebarToggle.disabled = true;
        if (previewPresent) previewPresent.disabled = true;
        _setPreviewSidebarCollapsed(true);
        _setEditorPreviewRefreshState('idle');

        _setGenPhase('styles');
        _setPreviewBusy(true);
        _setPanelVisible(true);
    }

    function _disconnectScaleObservers() {
        _scaleObservers.forEach(o => {
            try {
                o?.disconnect?.();
            } catch (e) {
                // ignore
            }
        });
        _scaleObservers = [];
    }

    function hidePreviewPanel() {
        _setPanelVisible(false);
        _syncAllCompletionCardTexts();
    }

    function isPreviewPanelVisible() {
        return slidePresentationPreviewVisible;
    }

    function getActivePreviewFileId() {
        return slidePresentationFileId;
    }

    function _syncAllCompletionCardTexts() {
        document.querySelectorAll('.slide-presentation-completion-card').forEach(card => {
            _updateCompletionCardText(card);
        });
    }

    function _setPreviewSidebarCollapsed(collapsed, options = {}) {
        const nextCollapsed = Boolean(collapsed);

        if (previewSidebar) {
            previewSidebar.classList.toggle('collapsed', nextCollapsed);
        }

        if (previewSidebarToggle) {
            const isOpen = !nextCollapsed;
            previewSidebarToggle.classList.toggle('active', isOpen);
            previewSidebarToggle.setAttribute('aria-pressed', String(isOpen));
        }

        if (options.userAction) {
            _previewSidebarUserInteracted = true;
        }

        // If we just opened the sidebar, thumbnails may have been rendered
        // while the sidebar width was 0. Rescale after layout.
        if (!nextCollapsed) {
            requestAnimationFrame(() => {
                _rescaleAllPreviewIframes();
                _updateThumbnails();
            });
        }
    }

    function _syncPreviewSidebarToggleState() {
        if (!previewSidebarToggle) return;
        const isCollapsed = previewSidebar ? previewSidebar.classList.contains('collapsed') : true;
        _setPreviewSidebarCollapsed(isCollapsed);
    }

    function _beginGenerationPreview(title) {
        if (!_generationInProgress) {
            _generationInProgress = true;
            _generationPreviewInitialized = false;
        }

        if (!_generationPreviewInitialized) {
            showPreviewPanel(title || t('slide_presentation_default_title', 'Presentation'));
            _generationPreviewInitialized = true;
        }
    }

    function _finishGenerationTracking() {
        _generationInProgress = false;
        _generationPreviewInitialized = false;
    }

    /** Close and clear a partial deck so the global preview toggle cannot
     * reopen failed output after the terminal tool error. */
    function _discardFailedGenerationPreview() {
        hidePreviewPanel();
        _clearSlideSwitchAnimations();
        _revokeSlideImages();
        _disconnectScaleObservers();
        _destroySlideObserver();
        slidePresentationSlides = [];
        slidePresentationStyles = '';
        slidePresentationHtmlBuffer = '';
        _resetPendingHtmlDelta();
        slidePresentationCurrentIndex = 0;
        slidePresentationFileId = null;
        slidePresentationPresentationId = null;
        slidePresentationRenderedRevision = 0;
        _slideItems = [];
        if (previewThumbnails) previewThumbnails.innerHTML = '';
        if (previewSlidesTrack) previewSlidesTrack.innerHTML = '';
        if (previewGenerating) previewGenerating.classList.add('hidden');
        if (previewNav) previewNav.classList.remove('visible');
        _setPreviewDownloadEnabled(false);
        _setPreviewEditEnabled(false);
        if (previewSidebarToggle) previewSidebarToggle.disabled = true;
        if (previewPresent) previewPresent.disabled = true;
        _setPreviewSidebarCollapsed(true);
        _setPreviewBusy(false);
    }

    /**
     * Clear every chat-scoped presentation reference before another transcript
     * is mounted. In-flight image loads use a generation token, so advancing it
     * also prevents a late response from repopulating the new chat's sidebar.
     */
    function reset() {
        slidePresentationImageLoadToken += 1;
        _editorPreviewRefreshToken += 1;
        _editorPreviewRetry = null;
        closePresentationEditor();
        // Do not exit an unrelated fullscreen surface merely because a chat
        // changed while the presentation slideshow itself was closed.
        if (ssOpen || ssOverlay?.classList.contains('open')) {
            closeSlideshow();
        } else {
            ssNavigationToken += 1;
        }
        _removeGeneratingCard(_activeMessageId);
        _activeMessageId = null;
        _finishGenerationTracking();
        _discardFailedGenerationPreview();
        _setEditorPreviewRefreshState('idle');
        _genPhase = 'styles';
    }

    function _setPanelVisible(visible) {
        slidePresentationPreviewVisible = visible;
        if (visible) {
            // All artifact panels share Canvas' persisted split width. Applying
            // it before the panel becomes visible avoids a one-frame width jump.
            _canvasSizingController()?.applyPreviewWidthRatio?.();
            _updatePreviewResizerA11y();
        } else {
            _stopPreviewResize();
        }
        if (previewPanel) {
            previewPanel.classList.toggle('visible', visible);
            previewPanel.setAttribute('aria-hidden', visible ? 'false' : 'true');
        }
        document.body.classList.toggle('slide-presentation-preview-open', visible);
        _syncAllCompletionCardTexts();
        if (typeof window.setMainSidebarAutoCollapsed === 'function') {
            // Presentation previews temporarily reserve the left sidebar's
            // space without changing the user's saved open/collapsed choice.
            window.setMainSidebarAutoCollapsed('slide-presentation-preview', visible);
        } else if (visible && typeof closeSidebar === 'function') {
            closeSidebar({ persist: false });
        }
        if (visible) {
            if (typeof window.closeOtherArtifactPreviews === 'function') {
                window.closeOtherArtifactPreviews('slide-presentation-preview');
            }
            // The first slide is often appended while the panel is still
            // transitioning into view. Rescale once we have real widths.
            requestAnimationFrame(() => {
                _rescaleAllSlideItems();
                _updateThumbnails();
            });
            setTimeout(() => {
                _rescaleAllSlideItems();
                _updateThumbnails();
            }, 250);
        }
    }

    function updatePreviewStatus(text, isComplete = false, state = 'generating') {
        if (previewStatus) {
            previewStatus.textContent = text;
            const statusClass = isComplete ? 'complete' : String(state || 'generating');
            previewStatus.className = 'slide-presentation-preview-panel-status canvas-markdown-preview-status ' + statusClass;
        }
        if (previewGeneratingText && !isComplete) {
            previewGeneratingText.textContent = text;
        }
    }

    function _extractStylesFromHtml(html) {
        const styleMatch = html.match(/<style[^>]*>([\s\S]*?)<\/style>/i);
        return styleMatch ? styleMatch[1] : '';
    }

    /**
     * Remove active content before a generated or partially generated slide is
     * placed in a same-origin preview document. The server performs the same
     * sanitization before persistence; this browser-side pass also protects the
     * live streaming preview before the final server document exists.
     */
    function _sanitizeSlideFrameHtml(bodyHtml) {
        const template = document.createElement('template');
        template.innerHTML = String(bodyHtml || '');
        template.content.querySelectorAll(
            'script, noscript, iframe, frame, frameset, object, embed, form, input, button, select, textarea, audio, video, source, track, link, meta, base'
        ).forEach(element => element.remove());
        template.content.querySelectorAll('*').forEach(element => {
            [...element.attributes].forEach(attribute => {
                const name = attribute.name.toLowerCase();
                if (name.startsWith('on') || name === 'srcdoc' || name === 'autofocus' || name === 'contenteditable') {
                    element.removeAttribute(attribute.name);
                }
            });
        });
        return template.innerHTML;
    }

    function _slideHtmlDoc(bodyHtml) {
        const sanitizedBodyHtml = _sanitizeSlideFrameHtml(bodyHtml);
        return `<!DOCTYPE html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${_SLIDE_PREVIEW_CSP}"><style>*{box-sizing:border-box;}body{margin:0;padding:0;background:#fff;overflow:hidden;}${slidePresentationStyles}.slide{transform-origin:top left;}</style></head><body>${sanitizedBodyHtml}</body></html>`;
    }

    function _writeIframe(iframe, bodyHtml) {
        // Prefer srcdoc because it remains reliable when the iframe is created
        // while its container has 0 width (e.g. first slide + collapsed sidebar).
        // Keep model-generated preview documents in an opaque origin. The CSP
        // and sanitizer remain important secondary defenses, but neither may
        // grant a generated deck access to the authenticated Omlorix document.
        iframe.setAttribute('sandbox', '');

        const html = _slideHtmlDoc(bodyHtml);

        if ('srcdoc' in iframe) {
            iframe.srcdoc = html;
            return;
        }
    }

    function _scaleIframe(iframe, containerEl) {
        if (!containerEl) return;
        const w = containerEl.offsetWidth;
        if (w > 0) iframe.style.transform = `scale(${w / 1920})`;
    }

    function _scaleIframeWithRetry(iframe, containerEl, attempt = 0) {
        if (!iframe || !containerEl) return;

        const w = containerEl.offsetWidth;
        if (w > 0) {
            iframe.style.transform = `scale(${w / 1920})`;
            return;
        }

        if (attempt >= _IFRAME_SCALE_RETRY_MAX) return;
        setTimeout(() => _scaleIframeWithRetry(iframe, containerEl, attempt + 1), _IFRAME_SCALE_RETRY_DELAY_MS);
    }

    function _scaleThumbnailIframeWithRetry(iframe, thumbEl, attempt = 0) {
        if (!iframe || !thumbEl) return;

        const thumbWidth = thumbEl.offsetWidth;
        if (thumbWidth > 0) {
            iframe.style.transform = `scale(${thumbWidth / 1920})`;
            return;
        }

        if (attempt >= _IFRAME_SCALE_RETRY_MAX) {
            const fallbackWidth = 148;
            iframe.style.transform = `scale(${fallbackWidth / 1920})`;
            return;
        }

        setTimeout(() => _scaleThumbnailIframeWithRetry(iframe, thumbEl, attempt + 1), _IFRAME_SCALE_RETRY_DELAY_MS);
    }

    function _observeMainIframeScale(iframe, containerEl) {
        if (!iframe || !containerEl) return;

        // Initial pass
        _scaleIframeWithRetry(iframe, containerEl);

        // Deterministic rescale whenever container size changes
        if ('ResizeObserver' in window) {
            const ro = new ResizeObserver(() => {
                _scaleIframeWithRetry(iframe, containerEl);
            });
            ro.observe(containerEl);
            _scaleObservers.push(ro);
        }

        // Backstop: some browsers only finalize iframe sizing after load
        iframe.addEventListener('load', () => {
            _scaleIframeWithRetry(iframe, containerEl);
        }, { once: true });
    }

    function _observeThumbIframeScale(iframe, thumbEl) {
        if (!iframe || !thumbEl) return;

        _scaleThumbnailIframeWithRetry(iframe, thumbEl);

        if ('ResizeObserver' in window) {
            const ro = new ResizeObserver(() => {
                _scaleThumbnailIframeWithRetry(iframe, thumbEl);
            });
            ro.observe(thumbEl);
            _scaleObservers.push(ro);
        }

        iframe.addEventListener('load', () => {
            _scaleThumbnailIframeWithRetry(iframe, thumbEl);
        }, { once: true });
    }

    function _rescaleAllPreviewIframes() {
        // Main slides
        _slideItems.forEach(item => {
            const iframe = item.querySelector('iframe');
            if (iframe) _scaleIframeWithRetry(iframe, item);
        });

        // Thumbnails (only if still iframes; images are handled elsewhere)
        if (previewThumbnails) {
            Array.from(previewThumbnails.children).forEach(thumb => {
                const iframe = thumb.querySelector('iframe');
                if (!iframe) return;
                _scaleThumbnailIframeWithRetry(iframe, thumb);
            });
        }
    }

    // ── Track-local scrolling and single-slide selection ───────────────────
    function _destroySlideObserver() {
        _detachPreviewTrackScrollHandler();
        _clearProgrammaticScrollLock();
    }

    function _ensureSelectedThumbnailVisible() {
        if (!previewSidebar || !previewThumbnails) return;
        const thumb = previewThumbnails.children[slidePresentationCurrentIndex];
        if (!thumb || previewSidebar.classList.contains('collapsed')) return;

        const sidebarRect = previewSidebar.getBoundingClientRect();
        const thumbRect = thumb.getBoundingClientRect();
        const outsideViewport = thumbRect.top < sidebarRect.top || thumbRect.bottom > sidebarRect.bottom;
        if (!outsideViewport) return;

        // Keep outline navigation isolated from the page. Ancestor-aware DOM
        // scrolling previously moved the chat itself along with the outline.
        const targetTop = Math.max(
            0,
            previewSidebar.scrollTop
                + (thumbRect.top - sidebarRect.top)
                - ((sidebarRect.height - thumbRect.height) / 2),
        );
        previewSidebar.scrollTo({
            top: targetTop,
            behavior: _shouldReduceMotion() ? 'auto' : 'smooth',
        });
    }

    function _syncSelectedSlideState({ ensureThumbnailVisible = true } = {}) {
        _slideItems.forEach((item, idx) => {
            item.classList.toggle('active', idx === slidePresentationCurrentIndex);
        });
        if (previewThumbnails) {
            Array.from(previewThumbnails.children).forEach((thumb, idx) => {
                const selected = idx === slidePresentationCurrentIndex;
                thumb.classList.toggle('active', selected);
                if (selected) thumb.setAttribute('aria-current', 'true');
                else thumb.removeAttribute('aria-current');
            });
        }
        if (ensureThumbnailVisible) _ensureSelectedThumbnailVisible();
    }

    function _setCurrentSlideIndex(nextIndex, options = {}) {
        const idx = Number(nextIndex);
        if (!Number.isFinite(idx)) return;
        if (idx < 0 || idx >= _slideItems.length) return;
        slidePresentationCurrentIndex = idx;
        _updateCounter();
        _syncSelectedSlideState(options);
    }

    function _getFocusedSlideIndexFromScroll() {
        if (!previewSlidesTrack || !_slideItems.length) return slidePresentationCurrentIndex;

        const maxScrollTop = Math.max(0, previewSlidesTrack.scrollHeight - previewSlidesTrack.clientHeight);
        const scrollTop = previewSlidesTrack.scrollTop;

        // Edge cases: first/last slide can never be fully centered; treat the boundaries
        // as explicit focus states.
        if (scrollTop <= 2) return 0;
        if (maxScrollTop > 0 && scrollTop >= maxScrollTop - 2) return _slideItems.length - 1;

        const trackRect = previewSlidesTrack.getBoundingClientRect();
        const focusY = trackRect.top + trackRect.height / 2;
        const currentItem = _slideItems[slidePresentationCurrentIndex];
        const currentRect = currentItem?.getBoundingClientRect();
        const currentDistance = currentRect
            ? Math.abs((currentRect.top + currentRect.height / 2) - focusY)
            : Infinity;
        let closestIndex = slidePresentationCurrentIndex;
        let closestDistance = currentDistance;

        // Compare slide centers instead of switching whenever the track center
        // merely touches a slide. A small hysteresis band makes the current
        // selection sticky around the midpoint between two slides, preventing
        // touch inertia and scroll-snap corrections from toggling it repeatedly.
        _slideItems.forEach((item, index) => {
            const rect = item.getBoundingClientRect();
            const distance = Math.abs((rect.top + rect.height / 2) - focusY);
            if (distance < closestDistance) {
                closestDistance = distance;
                closestIndex = index;
            }
        });

        if (closestIndex === slidePresentationCurrentIndex) return closestIndex;

        const hysteresisPx = Math.min(
            _SLIDE_SELECTION_HYSTERESIS_MAX_PX,
            Math.max(
                _SLIDE_SELECTION_HYSTERESIS_MIN_PX,
                trackRect.height * _SLIDE_SELECTION_HYSTERESIS_RATIO,
            ),
        );
        return currentDistance - closestDistance >= hysteresisPx
            ? closestIndex
            : slidePresentationCurrentIndex;
    }

    function _clearProgrammaticScrollLock() {
        _programmaticScrollActive = false;
        if (_programmaticScrollClearTimer) {
            clearTimeout(_programmaticScrollClearTimer);
            _programmaticScrollClearTimer = null;
        }
    }

    function _scheduleProgrammaticScrollSettled() {
        if (!previewSlidesTrack) {
            _clearProgrammaticScrollLock();
            return;
        }
        if (_programmaticScrollClearTimer) clearTimeout(_programmaticScrollClearTimer);
        // Native smooth-scroll duration varies by browser. Settle after the
        // final scroll event instead of guessing from a few animation frames.
        _programmaticScrollClearTimer = setTimeout(() => {
            if (!_programmaticScrollActive) return;
            const closestIdx = _getFocusedSlideIndexFromScroll();
            _clearProgrammaticScrollLock();
            if (closestIdx !== slidePresentationCurrentIndex) {
                _setCurrentSlideIndex(closestIdx);
            }
        }, 160);
    }

    function _onPreviewTrackScroll() {
        if (!previewSlidesTrack) return;
        if (_programmaticScrollActive) {
            _scheduleProgrammaticScrollSettled();
            return;
        }
        if (_previewTrackScrollRafId) return;

        _previewTrackScrollRafId = requestAnimationFrame(() => {
            _previewTrackScrollRafId = null;
            if (_programmaticScrollActive) return;
            const closestIdx = _getFocusedSlideIndexFromScroll();
            if (closestIdx !== slidePresentationCurrentIndex) {
                _setCurrentSlideIndex(closestIdx);
            }
        });
    }

    function _handleManualPreviewScrollIntent() {
        // Once the reader moves the deck themselves, live generation must not
        // keep pulling them to newly appended slides.
        _previewAutoFollowGeneration = false;
        if (_programmaticScrollActive) _clearProgrammaticScrollLock();
    }

    function _attachPreviewTrackScrollHandler() {
        if (!previewSlidesTrack) return;
        if (_previewTrackScrollHandlerAttached) return;
        previewSlidesTrack.addEventListener('scroll', _onPreviewTrackScroll, { passive: true });
        previewSlidesTrack.addEventListener('wheel', _handleManualPreviewScrollIntent, { passive: true });
        previewSlidesTrack.addEventListener('touchstart', _handleManualPreviewScrollIntent, { passive: true });
        previewSlidesTrack.addEventListener('pointerdown', _handleManualPreviewScrollIntent, { passive: true });
        _previewTrackScrollHandlerAttached = true;

        // Ensure the outline is synced immediately (e.g., after restore/open)
        // even before the user scrolls.
        _onPreviewTrackScroll();
    }

    function _detachPreviewTrackScrollHandler() {
        if (!previewSlidesTrack) return;
        if (!_previewTrackScrollHandlerAttached) return;
        previewSlidesTrack.removeEventListener('scroll', _onPreviewTrackScroll);
        previewSlidesTrack.removeEventListener('wheel', _handleManualPreviewScrollIntent);
        previewSlidesTrack.removeEventListener('touchstart', _handleManualPreviewScrollIntent);
        previewSlidesTrack.removeEventListener('pointerdown', _handleManualPreviewScrollIntent);
        _previewTrackScrollHandlerAttached = false;
        if (_previewTrackScrollRafId) {
            cancelAnimationFrame(_previewTrackScrollRafId);
            _previewTrackScrollRafId = null;
        }
    }

    function _buildSlideObserver() {
        _destroySlideObserver();
        if (!previewSlidesTrack) return;
        _attachPreviewTrackScrollHandler();
        _syncSelectedSlideState({ ensureThumbnailVisible: false });
    }

    // ── Core: append a new completed slide item to the track ───────────────
    function _appendSlideItem(slideHtml, idx) {
        if (!previewSlidesTrack) return;

        const item = document.createElement('div');
        item.className = 'slide-presentation-preview-slide-item';
        item.dataset.slideIndex = idx;

        const iframe = document.createElement('iframe');
        iframe.setAttribute('scrolling', 'no');
        item.appendChild(iframe);
        previewSlidesTrack.appendChild(item);
        _slideItems.push(item);
        _syncSelectedSlideState({ ensureThumbnailVisible: false });

        // Scale iframe after layout
        requestAnimationFrame(() => {
            _observeMainIframeScale(iframe, item);
            _writeIframe(iframe, slideHtml);
        });

        return item;
    }

    // ── Live update: write partial HTML into the last (in-progress) slide ──
    function _updateLastSlideItem(partialHtml) {
        if (!_slideItems.length) return;
        const lastItem = _slideItems[_slideItems.length - 1];
        const iframe = lastItem.querySelector('iframe');
        if (iframe) _writeIframe(iframe, partialHtml);
    }

    // ── Replace iframes with <img> tags once real images are available ──────
    function _replaceIframesWithImages() {
        if (!slidePresentationSlideImages.length) return;

        const markRevisionUpdated = (element) => {
            if (!element) return;
            element.classList.remove('revision-updated');
            // Force a new transition even when two refinement revisions arrive
            // close together.
            void element.offsetWidth;
            element.classList.add('revision-updated');
            setTimeout(() => element.classList.remove('revision-updated'), 650);
        };

        // Replace live HTML frames on the first render, then update existing
        // image elements in place for later refinement revisions.  Keeping the
        // DOM nodes stable preserves scroll position and selected-slide state.
        _slideItems.forEach((item, idx) => {
            const imgUrl = slidePresentationSlideImages[idx];
            if (!imgUrl) return;
            const existingImg = item.querySelector('img');
            if (existingImg) {
                existingImg.src = imgUrl;
                markRevisionUpdated(item);
                return;
            }
            const iframe = item.querySelector('iframe');
            if (!iframe) return;
            const img = document.createElement('img');
            img.src = imgUrl;
            img.alt = tf('slide_presentation_slide_number', 'Slide {number}', { number: idx + 1 });
            iframe.replaceWith(img);
            markRevisionUpdated(item);
        });

        // Replace thumbnail iframes
        if (previewThumbnails) {
            Array.from(previewThumbnails.children).forEach((thumb, idx) => {
                const imgUrl = slidePresentationSlideImages[idx];
                if (!imgUrl) return;
                const existingImg = thumb.querySelector('img');
                if (existingImg) {
                    existingImg.src = imgUrl;
                    markRevisionUpdated(thumb);
                    return;
                }
                const iframe = thumb.querySelector('iframe');
                if (!iframe) return;
                const img = document.createElement('img');
                img.src = imgUrl;
                img.alt = tf('slide_presentation_slide_number', 'Slide {number}', { number: idx + 1 });
                iframe.replaceWith(img);
                markRevisionUpdated(thumb);
            });
        }
    }

    // ── Load slide images from backend and replace iframes ──────────────────
    async function _fetchSlideImageWithRetry(endpoint, loadToken, maxAttempts = 5) {
        let lastError = null;
        for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
            if (loadToken !== slidePresentationImageLoadToken) return null;
            try {
                return await window.fetchAuthedBlobUrl(endpoint);
            } catch (error) {
                lastError = error;
                if (attempt + 1 < maxAttempts) {
                    // A first-pass draft writes directly into the live image
                    // directory, so a draft slide can legitimately return 404
                    // until that individual file exists. Refinements swap a
                    // complete directory, while remote stores can also lag.
                    // Treat every transient fetch failure as not-ready here;
                    // revision_ready never converts it into a permanent error.
                    await new Promise(resolve => setTimeout(resolve, 250 * (2 ** attempt)));
                }
            }
        }
        console.error('Failed to fetch slide image after retries', endpoint, lastError);
        return null;
    }

    /**
     * Decode the complete raster revision before touching visible slide nodes.
     * A fetched blob URL can still require an expensive first decode, which
     * would otherwise expose a blank frame immediately after the swap.
     */
    async function _preloadSlideImageUrls(urls, loadToken) {
        const decoded = await Promise.all(urls.map(url => new Promise(resolve => {
            const image = new Image();
            let settled = false;
            const finish = (loaded) => {
                if (settled) return;
                settled = true;
                clearTimeout(timeoutId);
                image.onload = null;
                image.onerror = null;
                resolve(loaded);
            };
            const timeoutId = setTimeout(() => finish(false), _SLIDE_IMAGE_DECODE_TIMEOUT_MS);
            image.onload = async () => {
                try {
                    if (typeof image.decode === 'function') await image.decode();
                    finish(true);
                } catch (_) {
                    finish(image.complete && image.naturalWidth > 0);
                }
            };
            image.onerror = () => finish(false);
            image.src = url;
        })));
        return loadToken === slidePresentationImageLoadToken && decoded.every(Boolean);
    }

    async function _loadSlideImages(presentationId, count, revision = 0, options = {}) {
        if (typeof window.fetchAuthedBlobUrl !== 'function') {
            console.warn('Slide image export requires fetchAuthedBlobUrl helper');
            return false;
        }

        const boundedCount = Math.max(0, Math.min(Number(count) || 0, 50));
        if (!boundedCount) return false;
        slidePresentationPresentationId = presentationId;
        const loadToken = ++slidePresentationImageLoadToken;
        const results = new Array(boundedCount).fill(null);
        const endpointCollection = options.draft ? 'draft-slides' : 'slides';
        let nextSlide = 1;
        const worker = async () => {
            while (nextSlide <= boundedCount) {
                const slideNumber = nextSlide++;
                const endpoint = `/api/v1/presentations/${encodeURIComponent(presentationId)}/${endpointCollection}/${slideNumber}?revision=${encodeURIComponent(revision || Date.now())}`;
                results[slideNumber - 1] = await _fetchSlideImageWithRetry(endpoint, loadToken);
            }
        };
        await Promise.all(Array.from({ length: Math.min(4, boundedCount) }, worker));
        if (loadToken !== slidePresentationImageLoadToken) {
            results.filter(Boolean).forEach((result) => result.revoke?.());
            return false;
        }
        const valid = results.filter(Boolean);
        if (valid.length !== boundedCount) {
            valid.forEach((result) => result.revoke?.());
            console.warn('The complete slide image set could not be loaded');
            return false;
        }

        const nextImageUrls = results.map(result => result.url);
        if (!await _preloadSlideImageUrls(nextImageUrls, loadToken)) {
            valid.forEach((result) => result.revoke?.());
            return false;
        }

        const previousRevokers = slidePresentationSlideImageRevokers;
        slidePresentationSlideImages = nextImageUrls;
        slidePresentationSlideImageRevokers = valid.map(res => res.revoke);
        slidePresentationRenderedRevision = Math.max(
            slidePresentationRenderedRevision,
            Number(revision) || 0
        );

        // If slide items already exist (live generation), replace their iframes.
        // If not (history reload), build the preview panel from scratch.
        if (options.forceRebuild) {
            _restorePreviewFromImages(false, {
                preserveIndex: options.preserveIndex,
            });
        } else if (_slideItems.length > 0) {
            _replaceIframesWithImages();
        } else {
            _restorePreviewFromImages();
        }
        previousRevokers.forEach(revoke => revoke());

        if (ssOpen) {
            ssImages = _ssUseImages();
            _ssGoTo(ssIndex);
        }
        return true;
    }

    async function _fetchSlideCountAndLoad(presentationId, knownCount) {
        if (typeof window.authedFetch !== 'function') return 0;
        let count = knownCount || 0;
        if (!count) {
            try {
                const res = await window.authedFetch(
                    `/api/v1/presentations/${encodeURIComponent(presentationId)}/slides/count`
                );
                if (res && res.ok) {
                    const json = await res.json();
                    count = json.count || 0;
                }
            } catch (err) {
                console.warn('Could not fetch slide count', err);
            }
        }
        if (count > 0) {
            await _loadSlideImages(presentationId, count);
        }
        return count;
    }

    function appendHtmlDelta(delta) {
        slidePresentationHtmlBuffer += delta;

        // Extract styles as they arrive
        if (slidePresentationHtmlBuffer.includes('</style>') && !slidePresentationStyles) {
            slidePresentationStyles = _extractStylesFromHtml(slidePresentationHtmlBuffer);
            if (slidePresentationStyles && _genPhase === 'styles') {
                _setGenPhase('slides');
            }
        }

        // ── Find all COMPLETED slides ──────────────────────────────────────
        const slideRegex = /<section[^>]*class\s*=\s*["'][^"']*slide[^"']*["'][^>]*>[\s\S]*?<\/section>/gi;
        const completedSlides = [];
        let m;
        while ((m = slideRegex.exec(slidePresentationHtmlBuffer)) !== null) {
            completedSlides.push(m[0]);
        }

        // ── Ensure panel is visible once we have any HTML ──────────────────
        if (completedSlides.length > 0 || slidePresentationHtmlBuffer.includes('<section')) {
            if (previewGenerating) previewGenerating.classList.add('hidden');
            if (previewNav) previewNav.classList.add('visible');
            if (previewSidebarToggle) previewSidebarToggle.disabled = false;
            if (!_previewSidebarAutoOpened && !_previewSidebarUserInteracted) {
                _setPreviewSidebarCollapsed(false);
                _previewSidebarAutoOpened = true;
            }
            if (!_previewTrackScrollHandlerAttached) _buildSlideObserver();
        }

        // ── Handle newly completed slides ──────────────────────────────────
        if (completedSlides.length > slidePresentationSlides.length) {
            for (let i = slidePresentationSlides.length; i < completedSlides.length; i++) {
                slidePresentationSlides.push(completedSlides[i]);

                if (i < _slideItems.length) {
                    // A partial item already exists for this index — update it with final HTML
                    const item = _slideItems[i];
                    item.dataset.slideIndex = i;
                    const iframe = item.querySelector('iframe');
                    if (iframe) _writeIframe(iframe, completedSlides[i]);
                    // Update thumbnail too
                    const thumb = previewThumbnails ? previewThumbnails.children[i] : null;
                    if (thumb) {
                        const tIframe = thumb.querySelector('iframe');
                        if (tIframe) _writeIframe(tIframe, completedSlides[i]);
                    }
                } else {
                    // No item yet — create fresh
                    _appendSlideItem(completedSlides[i], i);
                    _appendThumbnail(completedSlides[i], i);
                    if (_previewAutoFollowGeneration) {
                        _goToSlide(i, { preserveAutoFollow: true });
                    }
                }
            }

            updatePreviewStatus(tf(
                'slide_presentation_generating_slide',
                'Generating slide {number}…',
                { number: slidePresentationSlides.length + 1 }
            ));
            _updateCounter();
        }

        // ── Live-update the PARTIAL slide being generated ──────────────────
        const lastSectionStart = slidePresentationHtmlBuffer.lastIndexOf('<section');
        if (lastSectionStart !== -1) {
            const afterLast = slidePresentationHtmlBuffer.slice(lastSectionStart);
            // Only treat as partial if it has no closing tag yet
            if (!afterLast.includes('</section>') && afterLast.includes('class=')) {
                const partialHtml = afterLast + '</section>';
                const partialIdx = slidePresentationSlides.length; // index of the in-progress slide

                if (_slideItems.length <= partialIdx) {
                    // Create a new item for this partial slide
                    _appendSlideItem(partialHtml, partialIdx);
                    _appendThumbnail(partialHtml, partialIdx);
                    _updateCounter();
                    if (_previewAutoFollowGeneration) {
                        _goToSlide(partialIdx, { preserveAutoFollow: true });
                    }
                } else {
                    // Update the existing partial slide item live
                    _updateLastSlideItem(partialHtml);
                    _updateLastThumbnail(partialHtml);
                }
            }
        }
    }

    function _scrollTrackToSlide(index, behavior = 'smooth') {
        if (!previewSlidesTrack || !_slideItems[index]) return;
        const item = _slideItems[index];
        const maxScrollTop = Math.max(0, previewSlidesTrack.scrollHeight - previewSlidesTrack.clientHeight);
        const trackRect = previewSlidesTrack.getBoundingClientRect();
        const itemRect = item.getBoundingClientRect();
        const centeredTop = previewSlidesTrack.scrollTop
            + (itemRect.top - trackRect.top)
            - ((previewSlidesTrack.clientHeight - itemRect.height) / 2);
        const targetTop = Math.min(maxScrollTop, Math.max(0, centeredTop));
        const resolvedBehavior = _shouldReduceMotion() ? 'auto' : behavior;

        if (typeof previewSlidesTrack.scrollTo === 'function') {
            previewSlidesTrack.scrollTo({ top: targetTop, behavior: resolvedBehavior });
        } else {
            previewSlidesTrack.scrollTop = targetTop;
        }
    }

    function _goToSlide(index, { preserveAutoFollow = false, behavior = 'smooth' } = {}) {
        if (index < 0 || index >= _slideItems.length) return;
        if (!preserveAutoFollow) _previewAutoFollowGeneration = false;

        _programmaticScrollActive = true;
        if (_programmaticScrollClearTimer) {
            clearTimeout(_programmaticScrollClearTimer);
            _programmaticScrollClearTimer = null;
        }

        // Update outline immediately to reflect the user's selection.
        _setCurrentSlideIndex(index);
        _scrollTrackToSlide(index, behavior);
        _scheduleProgrammaticScrollSettled();
    }

    function _updateCounter() {
        const total = Math.max(_slideItems.length, slidePresentationSlides.length);
        if (previewCounter) {
            previewCounter.textContent = `${slidePresentationCurrentIndex + 1} / ${total}`;
        }
    }

    function _appendThumbnail(slideHtml, idx) {
        if (!previewThumbnails) return;
        const thumb = document.createElement('button');
        thumb.type = 'button';
        thumb.className = 'slide-presentation-preview-thumbnail' + (idx === slidePresentationCurrentIndex ? ' active' : '');
        thumb.dataset.slideIndex = idx;
        thumb.setAttribute('aria-label', tf('slide_presentation_slide_number', 'Slide {number}', { number: idx + 1 }));
        thumb.innerHTML = '<iframe></iframe><span class="slide-presentation-preview-thumbnail-number">' + (idx + 1) + '</span>';
        thumb.addEventListener('click', () => _goToSlide(idx));
        previewThumbnails.appendChild(thumb);
        _syncSelectedSlideState({ ensureThumbnailVisible: false });

        const iframe = thumb.querySelector('iframe');
        if (iframe) {
            requestAnimationFrame(() => {
                _observeThumbIframeScale(iframe, thumb);
                _writeIframe(iframe, slideHtml);
            });
        }
    }

    function _updateLastThumbnail(slideHtml) {
        if (!previewThumbnails || !previewThumbnails.lastElementChild) return;
        const thumb = previewThumbnails.lastElementChild;
        const iframe = thumb.querySelector('iframe');
        if (iframe) {
            _scaleThumbnailIframeWithRetry(iframe, thumb);
            _writeIframe(iframe, slideHtml);
        }
    }

    function _updateThumbnails() {
        if (!previewThumbnails) return;
        slidePresentationSlides.forEach((slideHtml, idx) => {
            const thumb = previewThumbnails.children[idx];
            if (!thumb) return;
            // If we already have a real image, update src; otherwise update iframe
            const img = thumb.querySelector('img');
            const imageUrl = slidePresentationSlideImages[idx];
            if (img && imageUrl) {
                img.hidden = false;
                img.src = imageUrl;
                return;
            }
            if (img && imageUrl === null) {
                img.removeAttribute('src');
                img.hidden = true;
            }
            const iframe = thumb.querySelector('iframe');
            if (iframe) {
                _scaleThumbnailIframeWithRetry(iframe, thumb);
                _writeIframe(iframe, slideHtml);
            }
        });
        _selectThumbnail();
    }

    function _selectThumbnail() {
        _syncSelectedSlideState();
    }

    function completePreview(fileId, presentationId, title = null, slideCount = 0, operation = 'created') {
        _flushPendingHtmlDelta();
        slidePresentationFileId = fileId;
        slidePresentationPresentationId = presentationId;

        _setGenPhase('finalizing');
        _setPreviewBusy(false);
        updatePreviewStatus(t('slide_presentation_complete', 'Complete'), true);

        // Set the presentation title if provided
        if (title && previewTitle) {
            previewTitle.textContent = title;
        }

        if (previewDownloadBtn) {
            previewDownloadBtn.removeAttribute('href');
            if (fileId) {
                previewDownloadBtn.setAttribute('data-file-id', fileId);
            } else {
                previewDownloadBtn.removeAttribute('data-file-id');
            }
            if (presentationId) {
                previewDownloadBtn.setAttribute('data-presentation-id', presentationId);
            } else {
                previewDownloadBtn.removeAttribute('data-presentation-id');
            }
            previewDownloadBtn.setAttribute('download', '');
        }
        _setPreviewDownloadEnabled(Boolean(fileId || presentationId));
        if (previewDownloadFormat) {
            if (!fileId && previewDownloadFormat.value === 'pptx') {
                previewDownloadFormat.value = 'pdf';
            }
        }
        if (previewPresent) previewPresent.disabled = false;
        _setPreviewEditEnabled(Boolean(presentationId));

        // Register in header canvas dropdown
        const _regId = presentationId || fileId;
        if (_regId && window.canvasFilesDropdown) {
            const _regTitle = (previewTitle && previewTitle.textContent)
                ? previewTitle.textContent
                : t('slide_presentation_default_title', 'Presentation');
            const context = _normalizePresentationContext({
                fileId,
                presentationId,
                title: _regTitle,
                slideCount,
                operation,
            });
            window.canvasFilesDropdown.registerFile(_regId, _regTitle, 'slide-presentation', function () {
                _openPresentationContext(context).catch((error) => {
                    console.error('[slide-presentation] Failed to open presentation from dropdown', error);
                });
            });
        }
        if (!_slideItems.length && slidePresentationSlideImages.length) {
            _restorePreviewFromImages(true);
        }
    }

    // ── Native full-site editor integration ──────────────────────────────

    async function _editorFetchJson(url, options = {}) {
        if (typeof window.authedFetch !== 'function') {
            throw new Error(t('slide_presentation_editor_unavailable', 'The presentation editor is not available.'));
        }
        const response = await window.authedFetch(url, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...(options.headers || {}),
            },
        });
        let payload = {};
        try {
            payload = await response.json();
        } catch (_) {
            payload = {};
        }
        if (!response.ok) {
            const detail = typeof payload.detail === 'string'
                ? payload.detail
                : String(payload?.detail?.message || '');
            const error = new Error(detail || tf(
                'slide_presentation_editor_request_failed_status',
                'Presentation editor request failed ({status}).',
                { status: response.status }
            ));
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    function _isEditorPreviewRefreshCurrent(token, presentationId) {
        return token === _editorPreviewRefreshToken
            && String(slidePresentationPresentationId || '') === String(presentationId || '');
    }

    /**
     * Reconcile the canonical source revision after the editor closes. Any
     * render already in progress is allowed to finish first; metadata is then
     * read back from the server so an older in-flight render can never strand
     * the sidebar on a stale revision.
     */
    function _queueEditorClosePreviewRefresh(presentationId, closeContext = {}) {
        const expectedRevision = Math.max(0, Number(closeContext.canvasRevision) || 0);
        if (!presentationId || closeContext.discardedUnsavedChanges) {
            _setEditorPreviewRefreshState('idle');
            return;
        }
        if (
            expectedRevision > 0
            && Number(closeContext.renderRevision) >= expectedRevision
            && (
                closeContext.sourceChanged === false
                || slidePresentationRenderedRevision >= expectedRevision
            )
        ) {
            _setEditorPreviewRefreshState('idle');
            return;
        }

        const refreshToken = ++_editorPreviewRefreshToken;
        const retry = () => _queueEditorClosePreviewRefresh(presentationId, {
            ...closeContext,
            renderPromise: null,
        });
        _editorPreviewRetry = retry;
        _setEditorPreviewRefreshState('busy');

        Promise.resolve().then(async () => {
            // The promise belongs to the editor session that just closed. It
            // remains safe to await after cancel() resets that session because
            // the parent render request and its authenticated fetch are already
            // independently owned by this widget.
            if (closeContext.renderPromise) {
                await Promise.resolve(closeContext.renderPromise).catch(() => false);
            }
            if (!_isEditorPreviewRefreshCurrent(refreshToken, presentationId)) return;

            const editorState = await _editorFetchJson(
                `/api/v1/presentations/${encodeURIComponent(presentationId)}/editor`
            );
            if (!_isEditorPreviewRefreshCurrent(refreshToken, presentationId)) return;

            const canvasRevision = Math.max(0, Number(editorState.canvas_revision) || expectedRevision);
            let renderPayload = editorState;
            if (Number(editorState.render_revision) < canvasRevision) {
                renderPayload = await _editorFetchJson(
                    `/api/v1/presentations/${encodeURIComponent(presentationId)}/editor/render`,
                    { method: 'POST', body: JSON.stringify({ expected_revision: canvasRevision }) }
                );
            }
            if (!_isEditorPreviewRefreshCurrent(refreshToken, presentationId)) return;
            if (Number(renderPayload.render_revision) < canvasRevision) {
                throw new Error(t(
                    'slide_presentation_editor_render_failed',
                    'Preview update failed'
                ));
            }

            if (slidePresentationRenderedRevision < Number(renderPayload.render_revision)) {
                const refreshed = await _refreshPreviewAfterEditorRender(renderPayload, refreshToken);
                if (!refreshed) return;
            }
            if (!_isEditorPreviewRefreshCurrent(refreshToken, presentationId)) return;
            _editorPreviewRetry = null;
            _setEditorPreviewRefreshState('idle');
        }).catch((error) => {
            if (!_isEditorPreviewRefreshCurrent(refreshToken, presentationId)) return;
            console.error('[slide-presentation] Failed to refresh edited preview', error);
            _setEditorPreviewRefreshState(
                'error',
                t('slide_presentation_editor_render_failed', 'Preview update failed')
            );
        });
    }

    function closePresentationEditor(options = {}) {
        if (!_editorOpen) return;
        const presentationId = String(options.presentationId || slidePresentationPresentationId || '');
        const refreshContext = options.refreshContext || null;
        const preserveNativeSession = options.preserveNativeSession === true;
        _editorOpen = false;
        _editorOpenToken += 1;
        _editorInertElements.forEach(element => element.removeAttribute('inert'));
        _editorInertElements = [];
        document.body.classList.remove('slide-presentation-editor-open');
        editorOverlay?.classList.remove('open', 'ready');
        editorOverlay?.setAttribute('aria-hidden', 'true');
        if (!preserveNativeSession) {
            window.slidePresentationNativeEditor?.cancel?.();
        }
        const focusTarget = _editorReturnFocus;
        if (preserveNativeSession) {
            // Focus the underlying preview before opening the slideshow so its
            // own focus restoration never targets a now-hidden editor control.
            focusTarget?.focus?.();
        } else {
            _editorReturnFocus = null;
            requestAnimationFrame(() => focusTarget?.focus?.());
        }
        if (refreshContext) {
            _queueEditorClosePreviewRefresh(presentationId, refreshContext);
        }
    }

    /**
     * Load the current canvas document and mount the editor directly inside
     * Omlorix. A monotonically increasing token prevents a slow response from
     * reopening an editor the user has already closed.
     */
    async function openPresentationEditor() {
        const presentationId = String(slidePresentationPresentationId || '').trim();
        const nativeEditor = window.slidePresentationNativeEditor;
        if (!presentationId || !editorOverlay || !editorHost || !nativeEditor?.open) {
            window.notifyError?.(t(
                'slide_presentation_editor_unavailable',
                'The presentation editor is not available.'
            ));
            return;
        }

        _editorReturnFocus = document.activeElement;
        const openToken = ++_editorOpenToken;
        _editorOpen = true;
        editorOverlay.classList.add('open');
        editorOverlay.classList.remove('ready');
        editorOverlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('slide-presentation-editor-open');

        // Keep keyboard and assistive-technology focus inside the editor while
        // it covers the Omlorix workspace. Only attributes added here are later
        // removed, preserving any pre-existing inert application regions.
        _editorInertElements = [...document.body.children].filter(element => (
            // The editor overlay currently lives inside Omlorix's main shell,
            // rather than as a direct child of <body>. Applying `inert` to
            // that ancestor also makes the complete editor (including its
            // slide iframe) unable to receive pointer or keyboard events.
            // Leave the overlay's entire ancestor branch interactive while
            // making every unrelated top-level application region inert.
            element !== editorOverlay
            && !element.contains(editorOverlay)
            && !element.hasAttribute('inert')
        ));
        _editorInertElements.forEach(element => element.setAttribute('inert', ''));

        try {
            const payload = await _editorFetchJson(
                `/api/v1/presentations/${encodeURIComponent(presentationId)}/editor`
            );
            if (!_editorOpen || openToken !== _editorOpenToken) return;

            nativeEditor.open({
                payload,
                exportFormat: previewDownloadFormat?.value || 'pptx',
                save: async changes => {
                    try {
                        const result = await _editorFetchJson(
                            `/api/v1/presentations/${encodeURIComponent(presentationId)}/editor`,
                            { method: 'PUT', body: JSON.stringify(changes || {}) }
                        );
                        // The request belongs to the deck captured when this
                        // editor session opened. It may finish after another
                        // deck has replaced the shared preview.
                        if (String(slidePresentationPresentationId || '') === presentationId) {
                            slidePresentationFileId = String(result.file_id || slidePresentationFileId || '') || null;
                            if (previewTitle && result.title) previewTitle.textContent = result.title;
                        }
                        return result;
                    } catch (error) {
                        if (error?.status === 409) {
                            error.message = t(
                                'slide_presentation_editor_conflict',
                                'This presentation changed elsewhere. Reload it before saving.'
                            );
                        }
                        throw error;
                    }
                },
                render: async changes => {
                    const refreshToken = ++_editorPreviewRefreshToken;
                    _editorPreviewRetry = null;
                    _setEditorPreviewRefreshState('busy');
                    updatePreviewStatus(t('slide_presentation_editor_rendering', 'Updating preview…'), false);
                    try {
                        const result = await _editorFetchJson(
                            `/api/v1/presentations/${encodeURIComponent(presentationId)}/editor/render`,
                            { method: 'POST', body: JSON.stringify(changes || {}) }
                        );
                        if (_isEditorPreviewRefreshCurrent(refreshToken, presentationId)) {
                            await _refreshPreviewAfterEditorRender(result, refreshToken);
                        }
                        if (_isEditorPreviewRefreshCurrent(refreshToken, presentationId)) {
                            _setEditorPreviewRefreshState('idle');
                        }
                        return result;
                    } catch (error) {
                        if (_isEditorPreviewRefreshCurrent(refreshToken, presentationId)) {
                            _editorPreviewRetry = () => _queueEditorClosePreviewRefresh(
                                presentationId,
                                {
                                    canvasRevision: Number(changes?.expected_revision) || 0,
                                    renderRevision: slidePresentationRenderedRevision,
                                    sourceChanged: true,
                                }
                            );
                            updatePreviewStatus(t('slide_presentation_editor_render_failed', 'Preview update failed'), false);
                            _setEditorPreviewRefreshState(
                                'error',
                                t('slide_presentation_editor_render_failed', 'Preview update failed')
                            );
                        }
                        throw error;
                    }
                },
                // The editor owns no slideshow or exporter. Both actions enter
                // the shared canvas paths; Present hands off its render promise
                // so the slideshow can display progress, while Export waits for
                // the final derivative before starting the download.
                present: async ({ slideIndex, renderPromise } = {}) => {
                    if (_slideItems.length) {
                        _setCurrentSlideIndex(Math.max(0, Math.min(Number(slideIndex) || 0, _slideItems.length - 1)));
                    }
                    // Hide the editor without cancelling its save/render
                    // session. The slideshow becomes visible immediately and
                    // owns the wait, so the user sees useful progress instead
                    // of an apparently frozen editor.
                    closePresentationEditor({ preserveNativeSession: true });
                    openSlideshow({ deferContent: true, slideIndex });

                    const rendered = await Promise.resolve(renderPromise).catch(() => false);
                    nativeEditor.cancel?.();
                    _editorReturnFocus = null;
                    if (!ssOpen) return;
                    if (!rendered) {
                        closeSlideshow();
                        return;
                    }
                    _showSlideshowContent(slideIndex);
                },
                export: async ({ format } = {}) => {
                    await downloadPresentation(format);
                },
                onReady: () => {
                    if (!_editorOpen || openToken !== _editorOpenToken) return;
                    editorOverlay.classList.add('ready');
                    nativeEditor.focus?.();
                },
                onClose: refreshContext => closePresentationEditor({
                    presentationId,
                    refreshContext,
                }),
            });
        } catch (error) {
            if (!_editorOpen || openToken !== _editorOpenToken) return;
            console.error('[slide-presentation] Failed to open native editor', error);
            window.notifyError?.(t(
                'slide_presentation_editor_load_failed',
                'Failed to open the presentation editor.'
            ));
            closePresentationEditor();
        }
    }

    async function _refreshPreviewAfterEditorRender(payload, refreshToken = 0) {
        const presentationId = String(payload.presentation_id || slidePresentationPresentationId || '');
        const slideCount = Number(payload.slide_count) || 0;
        if (!presentationId || slideCount <= 0) return;
        if (String(slidePresentationPresentationId || '') !== presentationId) return;
        if (refreshToken && !_isEditorPreviewRefreshCurrent(refreshToken, presentationId)) return;

        const previousIndex = slidePresentationCurrentIndex;
        slidePresentationFileId = String(payload.file_id || slidePresentationFileId || '') || null;
        slidePresentationPresentationId = presentationId;
        if (previewTitle && payload.title) previewTitle.textContent = payload.title;
        const requiresRebuild = _slideItems.length !== slideCount
            || Number(previewThumbnails?.children.length || 0) !== slideCount;
        const imagesLoaded = await _loadSlideImages(
            presentationId,
            slideCount,
            Number(payload.render_revision) || Date.now(),
            {
                forceRebuild: requiresRebuild,
                preserveIndex: previousIndex,
            }
        );
        // Loading the derivative images is asynchronous. Recheck ownership in
        // case another deck opened while those requests were in flight.
        if (String(slidePresentationPresentationId || '') !== presentationId) return;
        if (refreshToken && !_isEditorPreviewRefreshCurrent(refreshToken, presentationId)) return;
        if (!imagesLoaded) {
            throw new Error(t('slide_presentation_editor_render_failed', 'Preview update failed'));
        }
        if (_slideItems.length && !requiresRebuild) {
            _setCurrentSlideIndex(Math.min(previousIndex, _slideItems.length - 1), {
                ensureThumbnailVisible: false,
            });
            requestAnimationFrame(() => _scrollTrackToSlide(slidePresentationCurrentIndex, 'auto'));
        }
        completePreview(
            slidePresentationFileId,
            presentationId,
            String(payload.title || previewTitle?.textContent || ''),
            slideCount,
            'updated'
        );
        _refreshStoredPresentationContext({
            fileId: slidePresentationFileId,
            presentationId,
            title: String(payload.title || previewTitle?.textContent || ''),
            slideCount,
            operation: 'updated',
        });
        return true;
    }

    // ── Shared Canvas split-panel sizing ──────────────────────────────────
    function _canvasSizingController() {
        return window.canvasMarkdownWidget || null;
    }

    function _updatePreviewResizerA11y() {
        if (!previewResizer) return;
        const controller = _canvasSizingController();
        const bounds = controller?.getPreviewWidthBounds?.();
        const ratio = Number(controller?.getPreviewWidthRatio?.() || 0.5);
        if (!bounds?.viewportWidth) return;
        previewResizer.setAttribute('aria-valuemin', String(Math.round((bounds.minWidth / bounds.viewportWidth) * 100)));
        previewResizer.setAttribute('aria-valuemax', String(Math.round((bounds.maxWidth / bounds.viewportWidth) * 100)));
        previewResizer.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
    }

    function _schedulePreviewRescale() {
        if (_previewResizeScaleRafId) return;
        _previewResizeScaleRafId = requestAnimationFrame(() => {
            _previewResizeScaleRafId = null;
            _rescaleAllSlideItems();
            _updateThumbnails();
            // Slide height changes with the panel width; keep the selected
            // slide centered while the user drags the shared split handle.
            _scrollTrackToSlide(slidePresentationCurrentIndex, 'auto');
        });
    }

    function _setPreviewWidthFromPointer(clientX, { persist = false } = {}) {
        _canvasSizingController()?.setPreviewWidthFromPointerX?.(clientX, { persist });
        _updatePreviewResizerA11y();
        _schedulePreviewRescale();
    }

    function _stopPreviewResize() {
        if (!_previewResizeActive) return;
        _previewResizeActive = false;
        document.body.classList.remove('canvas-markdown-preview-resizing');
        if (_previewResizePointerId !== null) {
            previewResizer?.releasePointerCapture?.(_previewResizePointerId);
        }
        _previewResizePointerId = null;

        // Pointer movement is intentionally cheap and only persisted once the
        // gesture ends, matching Canvas' split-panel behavior.
        const controller = _canvasSizingController();
        const bounds = controller?.getPreviewWidthBounds?.();
        const ratio = Number(controller?.getPreviewWidthRatio?.() || 0.5);
        if (bounds?.viewportWidth) {
            controller?.setPreviewWidthFromPixels?.(bounds.viewportWidth * ratio, { persist: true });
        }
        _updatePreviewResizerA11y();
        _schedulePreviewRescale();
    }

    function _handlePreviewResizerPointerDown(event) {
        if (window.innerWidth <= _PREVIEW_DESKTOP_BREAKPOINT) return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        event.preventDefault();
        _previewResizeActive = true;
        _previewResizePointerId = event.pointerId;
        document.body.classList.add('canvas-markdown-preview-resizing');
        previewResizer?.setPointerCapture?.(event.pointerId);
        _setPreviewWidthFromPointer(event.clientX);
    }

    function _handlePreviewResizerPointerMove(event) {
        if (!_previewResizeActive) return;
        event.preventDefault();
        _setPreviewWidthFromPointer(event.clientX);
    }

    function _handlePreviewResizerKeydown(event) {
        if (window.innerWidth <= _PREVIEW_DESKTOP_BREAKPOINT) return;
        const controller = _canvasSizingController();
        const bounds = controller?.getPreviewWidthBounds?.();
        if (!bounds?.viewportWidth) return;
        const currentWidth = bounds.viewportWidth * Number(controller.getPreviewWidthRatio?.() || 0.5);
        const step = event.shiftKey ? _PREVIEW_RESIZE_LARGE_STEP : _PREVIEW_RESIZE_STEP;
        let nextWidth = null;

        if (event.key === 'ArrowLeft') nextWidth = currentWidth + step;
        else if (event.key === 'ArrowRight') nextWidth = currentWidth - step;
        else if (event.key === 'Home') nextWidth = bounds.minWidth;
        else if (event.key === 'End') nextWidth = bounds.maxWidth;
        else if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            controller.resetPreviewWidth?.();
            _updatePreviewResizerA11y();
            _schedulePreviewRescale();
            return;
        }

        if (nextWidth === null) return;
        event.preventDefault();
        controller.setPreviewWidthFromPixels?.(nextWidth, { persist: true });
        _updatePreviewResizerA11y();
        _schedulePreviewRescale();
    }

    // ── Preview panel event listeners ─────────────────────────────────────
    if (previewClose) previewClose.addEventListener('click', hidePreviewPanel);

    if (previewResizer) {
        previewResizer.addEventListener('pointerdown', _handlePreviewResizerPointerDown);
        previewResizer.addEventListener('pointermove', _handlePreviewResizerPointerMove);
        previewResizer.addEventListener('pointerup', _stopPreviewResize);
        previewResizer.addEventListener('pointercancel', _stopPreviewResize);
        previewResizer.addEventListener('dblclick', () => {
            _canvasSizingController()?.resetPreviewWidth?.();
            _updatePreviewResizerA11y();
            _schedulePreviewRescale();
        });
        previewResizer.addEventListener('keydown', _handlePreviewResizerKeydown);
    }

    window.addEventListener('blur', _stopPreviewResize);

    let _sidebarRafId = null;

    function _rescaleDuringTransition() {
        _rescaleAllSlideItems();
        _sidebarRafId = requestAnimationFrame(_rescaleDuringTransition);
    }

    if (previewSidebarToggle) {
        previewSidebarToggle.addEventListener('click', () => {
            if (!previewSidebar) return;
            const isCollapsed = previewSidebar.classList.contains('collapsed');
            _setPreviewSidebarCollapsed(!isCollapsed, { userAction: true });
            // Continuously rescale during the transition
            if (_sidebarRafId) cancelAnimationFrame(_sidebarRafId);
            _sidebarRafId = requestAnimationFrame(_rescaleDuringTransition);
            const onEnd = (e) => {
                if (e.propertyName !== 'width') return;
                cancelAnimationFrame(_sidebarRafId);
                _sidebarRafId = null;
                _rescaleAllSlideItems();
                previewSidebar.removeEventListener('transitionend', onEnd);
            };
            previewSidebar.addEventListener('transitionend', onEnd);
        });
    }

    if (previewPresent) previewPresent.addEventListener('click', openSlideshow);
    if (previewEdit) {
        const icon = previewEdit.querySelector('.slide-presentation-preview-edit-icon');
        if (icon) icon.innerHTML = Icons.expand || Icons.edit || '';
        previewEdit.addEventListener('click', openPresentationEditor);
    }
    if (editorFallbackClose) {
        const icon = editorFallbackClose.querySelector('span');
        if (icon) icon.innerHTML = Icons.close || '';
        editorFallbackClose.addEventListener('click', closePresentationEditor);
    }
    if (previewUpdateRetry) {
        previewUpdateRetry.addEventListener('click', () => {
            const retry = _editorPreviewRetry;
            if (typeof retry === 'function') retry();
        });
    }

    /** Download through the single sidebar export implementation. */
    async function downloadPresentation(formatOverride = '') {
            const fileId = previewDownloadBtn.getAttribute('data-file-id');
            const presentationId = previewDownloadBtn.getAttribute('data-presentation-id') || slidePresentationPresentationId;
            if (previewDownloadBtn.classList.contains('disabled')) {
                return;
            }

            try {
                _setPreviewDownloadBusy(true);

                // Get selected format and presentation title
                let format = String(formatOverride || '').trim() || (window.chatDownloadControls
                    ? window.chatDownloadControls.getSelectedDownloadFormat(previewDownloadFormat, 'pptx')
                    : (previewDownloadFormat && previewDownloadFormat.value ? previewDownloadFormat.value : 'pptx'));
                if (!format || format === 'slide-presentation-') {
                    format = 'pptx';
                }
                const presentationTitle = (previewTitle && previewTitle.textContent) ? previewTitle.textContent : 'presentation';
                
                // Sanitize filename: remove special characters and limit length
                let sanitizedTitle = presentationTitle
                    .replace(/[^\w\s-]/g, '') // Remove special characters
                    .replace(/\s+/g, '-') // Replace spaces with hyphens
                    .substring(0, 50); // Limit to 50 characters
                if (!sanitizedTitle) {
                    sanitizedTitle = 'presentation';
                }

                let downloadUrl = '';
                let outputFilename = `${sanitizedTitle}.pptx`;

                if (format === 'slides_zip') {
                    if (!presentationId) {
                        throw new Error(t('slide_presentation_archive_unavailable', 'Slide image archive is not available for this presentation.'));
                    }
                    downloadUrl = `/api/v1/presentations/${encodeURIComponent(presentationId)}/slides/archive`;
                    outputFilename = `${sanitizedTitle}-images.zip`;
                } else if (format === 'pdf') {
                    if (!presentationId) {
                        throw new Error(t('slide_presentation_pdf_unavailable', 'PDF download is not available for this presentation.'));
                    }
                    downloadUrl = `/api/v1/presentations/${encodeURIComponent(presentationId)}/slides/pdf`;
                    outputFilename = `${sanitizedTitle}.pdf`;
                } else {
                    if (!fileId) {
                        throw new Error(t('slide_presentation_pptx_unavailable', 'PPTX download is not available for this presentation.'));
                    }
                    downloadUrl = `/api/v1/files/download?file_id=${encodeURIComponent(fileId)}`;
                    outputFilename = `${sanitizedTitle}.pptx`;
                }

                await window.chatDownloadControls.downloadBlobFromUrl(downloadUrl, outputFilename, {
                    errorMessage: (response) => tf('slide_presentation_download_status_failed', 'Download failed: {status} {statusText}', {
                        status: response?.status || '',
                        statusText: response?.statusText || '',
                    }),
                });

            } catch (error) {
                console.error('PowerPoint download failed:', error);
                // Show error message to user
                if (typeof window.notifyError === 'function') {
                    window.notifyError(t('slide_presentation_download_failed_retry', 'Failed to download presentation. Please try again.'));
                } else {
                    console.error(t('slide_presentation_download_failed_retry', 'Failed to download presentation. Please try again.'));
                }
            } finally {
                _setPreviewDownloadBusy(false);
            }
    }

    // Both the sidebar button and editor export control call the same helper.
    if (previewDownloadBtn) {
        previewDownloadBtn.addEventListener('click', async (e) => {
            e.preventDefault();
            await downloadPresentation();
        });
    }

    window.addEventListener('resize', () => {
        _updatePreviewResizerA11y();
        if (_slideItems.length > 0) {
            _schedulePreviewRescale();
        }
    });

    function _rescaleAllSlideItems() {
        _slideItems.forEach(item => {
            const iframe = item.querySelector('iframe');
            if (iframe) _scaleIframeWithRetry(iframe, item);
        });
    }

    function _revokeSlideImages() {
        slidePresentationSlideImageRevokers.forEach(revoke => {
            try {
                revoke?.();
            } catch (err) {
                console.warn('Failed to revoke slide image URL', err);
            }
        });
        slidePresentationSlideImageRevokers = [];
        slidePresentationSlideImages = [];
        
        // Also clear slideshow images to prevent cross-contamination
        ssImages = [];
    }

    // ── Slideshow functions ────────────────────────────────────────────────
    function _ssUpdateCounter() {
        if (ssCurrent) ssCurrent.textContent = ssIndex + 1;
        if (ssTotal)   ssTotal.textContent   = ssImages.length;
        if (ssPrev)    ssPrev.disabled  = ssIndex === 0;
        if (ssNext)    ssNext.disabled  = ssIndex >= ssImages.length - 1;
        if (ssOverlay) {
            ssOverlay.querySelectorAll('.slide-presentation-ss-dot').forEach((d, i) =>
                d.classList.toggle('active', i === ssIndex));
        }
    }

    function _ssGoTo(index) {
        if (!ssImages.length) return;
        const previousIndex = ssIndex;
        ssIndex = Math.max(0, Math.min(index, ssImages.length - 1));
        const url = ssImages[ssIndex] || '';
        const ssImgEl = ssOverlay ? ssOverlay.querySelector('.slide-presentation-ss-img-real') : null;
        const ssIframe = ssOverlay ? ssOverlay.querySelector('.slide-presentation-ss-iframe') : null;
        const shouldAnimateSwitch = ssIndex !== previousIndex;
        const navigationToken = ++ssNavigationToken;

        if (ssImgEl) {
            // Preload the image, then swap — no black flash
            const preload = new Image();
            let didSwap = false;
            const swapPreloadedImage = () => {
                if (didSwap || navigationToken !== ssNavigationToken || !ssOpen) return;
                didSwap = true;
                ssImgEl.src = url;
                if (shouldAnimateSwitch) {
                    _playSlideshowSwitchAnimation(ssImgEl, ssIndex, previousIndex);
                }
            };
            preload.onload = swapPreloadedImage;
            preload.src = url;
            // If already cached, swap immediately
            if (preload.complete) {
                swapPreloadedImage();
            }
        } else if (ssIframe) {
            ssIframe.src = url;
            if (shouldAnimateSwitch) {
                _playSlideshowSwitchAnimation(ssIframe, ssIndex, previousIndex);
            }
        }
        _ssUpdateCounter();
    }

    function _buildProgressDots() {
        if (!ssOverlay) return;
        const container = ssOverlay.querySelector('.slide-presentation-slideshow-container');
        if (!container) return;
        let prog = container.querySelector('.slide-presentation-slideshow-progress');
        if (!prog) {
            prog = document.createElement('div');
            prog.className = 'slide-presentation-slideshow-progress';
            container.appendChild(prog);
        }
        prog.innerHTML = '';
        if (ssImages.length <= 30) {
            ssImages.forEach((_, i) => {
                const dot = document.createElement('button');
                dot.type = 'button';
                dot.className = 'slide-presentation-ss-dot' + (i === ssIndex ? ' active' : '');
                dot.setAttribute(
                    'aria-label',
                    tf('slide_presentation_go_to_slide', 'Go to slide {number}', { number: i + 1 })
                );
                dot.addEventListener('click', () => _ssGoTo(i));
                prog.appendChild(dot);
            });
        }
    }

    function _ssUseImages() {
        // Prefer real PNG images; fall back to blob URLs from HTML
        if (slidePresentationSlideImages.length > 0) {
            // Retain slide indices even when one backend image is temporarily
            // unavailable, but never hand a null URL to the slideshow image.
            return slidePresentationSlideImages.map((url, idx) => {
                if (url) return url;
                const label = tf('slide_presentation_slide_number', 'Slide {number}', { number: idx + 1 });
                const svg = `${Icons.createSlidePlaceholder(label)}`;
                return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
            });
        }
        // Fallback: build blob URLs from HTML
        const blobs = [];
        for (let i = 0; i < slidePresentationSlides.length; i++) {
            const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${_SLIDE_PREVIEW_CSP}"><style>*{margin:0;padding:0;box-sizing:border-box;}body{width:1920px;height:1080px;overflow:hidden;background:#fff;}${slidePresentationStyles}</style></head><body>${_sanitizeSlideFrameHtml(slidePresentationSlides[i])}</body></html>`;
            const blob = new Blob([html], { type: 'text/html' });
            blobs.push(URL.createObjectURL(blob));
        }
        return blobs;
    }

    function _showSlideshowControls() {
        if (!ssOverlay) return;
        ssOverlay.classList.add('show-controls');
        if (_ssHideTimer) {
            clearTimeout(_ssHideTimer);
        }
        _ssHideTimer = setTimeout(() => {
            ssOverlay.classList.remove('show-controls');
        }, 3000);
    }

    function _hideSlideshowControlsImmediately() {
        if (!ssOverlay) return;
        if (_ssHideTimer) {
            clearTimeout(_ssHideTimer);
            _ssHideTimer = null;
        }
        ssOverlay.classList.remove('show-controls');
    }

    /**
     * Populate an already-open slideshow from the newest available slide
     * images. Kept separate from opening the shell so editor-triggered preview
     * can reveal its loading state before backend rendering has completed.
     */
    function _showSlideshowContent(requestedIndex = ssIndex) {
        if ((!slidePresentationSlides.length && !slidePresentationSlideImages.length) || !ssOverlay || !ssOpen) {
            return false;
        }

        ssOverlay.classList.remove('is-rerendering');
        if (ssLoaderText) {
            ssLoaderText.textContent = t('slide_presentation_loading_slides', 'Loading slides…');
        }
        const useRealImages = slidePresentationSlideImages.length > 0;
        ssImages = _ssUseImages();
        ssIndex = Math.max(0, Math.min(Number(requestedIndex) || 0, ssImages.length - 1));

        if (ssLoaderBar) ssLoaderBar.style.width = '100%';
        if (ssLoaderCount) ssLoaderCount.textContent = `${ssImages.length} / ${ssImages.length}`;

        _buildProgressDots();

        if (useRealImages) {
            // Use <img> for real PNG images.
            let ssImgEl = ssOverlay.querySelector('.slide-presentation-ss-img-real');
            const existingIframe = ssOverlay.querySelector('.slide-presentation-ss-iframe');
            if (existingIframe) existingIframe.remove();
            if (!ssImgEl) {
                ssImgEl = document.createElement('img');
                ssImgEl.className = 'slide-presentation-ss-img-real';
                ssImgEl.style.cssText = 'width:100%;height:100%;object-fit:contain;border-radius:6px;box-shadow:0 8px 60px rgba(0,0,0,0.6);display:block;';
                if (ssImg?.isConnected) ssImg.replaceWith(ssImgEl);
                else if (ssStage) ssStage.appendChild(ssImgEl);
            }
            ssImgEl.src = ssImages[ssIndex] || '';
        } else {
            // Fallback to the live HTML deck when no rendered PNGs exist.
            let ssIframe = ssOverlay.querySelector('.slide-presentation-ss-iframe');
            const existingImage = ssOverlay.querySelector('.slide-presentation-ss-img-real');
            if (existingImage) existingImage.remove();
            if (!ssIframe) {
                ssIframe = document.createElement('iframe');
                ssIframe.className = 'slide-presentation-ss-iframe';
                ssIframe.setAttribute('sandbox', '');
                ssIframe.style.cssText = 'width:100%;height:100%;border:none;border-radius:6px;box-shadow:0 8px 60px rgba(0,0,0,0.6);';
                if (ssImg?.isConnected) ssImg.replaceWith(ssIframe);
                else if (ssStage) ssStage.appendChild(ssIframe);
            }
            ssIframe.setAttribute('sandbox', '');
            ssIframe.src = ssImages[ssIndex] || '';
        }

        _ssUpdateCounter();
        if (ssLoader) ssLoader.classList.add('hidden');
        if (ssStage) ssStage.classList.add('visible');
        _hideSlideshowControlsImmediately();
        requestAnimationFrame(() => ssClose?.focus());
        return true;
    }

    function openSlideshow(options = {}) {
        const deferContent = options.deferContent === true;
        if ((!deferContent && !slidePresentationSlides.length && !slidePresentationSlideImages.length) || !ssOverlay) return;
        ssOpen = true;
        ssPreviouslyFocused = document.activeElement;
        ssIndex = Math.max(0, Number(options.slideIndex ?? slidePresentationCurrentIndex) || 0);

        ssOverlay.classList.add('open');
        ssOverlay.classList.toggle('is-rerendering', deferContent);
        ssOverlay.setAttribute('aria-hidden', 'false');
        if (ssLoader) ssLoader.classList.remove('hidden');
        if (ssStage)  ssStage.classList.remove('visible');
        if (ssTotal)  ssTotal.textContent = slidePresentationSlides.length;

        if (deferContent) {
            ssImages = [];
            if (ssLoaderText) {
                ssLoaderText.textContent = t('slide_presentation_editor_rendering', 'Updating preview…');
            }
            if (ssLoaderBar) ssLoaderBar.style.width = '0%';
            if (ssLoaderCount) ssLoaderCount.textContent = '';
            _buildProgressDots();
            requestAnimationFrame(() => ssOverlay.focus());
            return;
        }

        _showSlideshowContent(ssIndex);
    }

    function closeSlideshow() {
        ssOpen = false;
        ssNavigationToken += 1;
        _clearSlideSwitchAnimations();
        if (ssOverlay) {
            ssOverlay.classList.remove('open');
            ssOverlay.classList.remove('is-fullscreen');
            ssOverlay.classList.remove('is-rerendering');
            ssOverlay.classList.remove('show-controls');
            ssOverlay.setAttribute('aria-hidden', 'true');
        }
        // Only revoke blob URLs (real image URLs are permanent API endpoints)
        if (slidePresentationSlideImages.length === 0) {
            ssImages.forEach(url => { if (url && url.startsWith('blob:')) URL.revokeObjectURL(url); });
        }
        ssImages = [];
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
        if (ssPreviouslyFocused && typeof ssPreviouslyFocused.focus === 'function') {
            ssPreviouslyFocused.focus();
        }
        ssPreviouslyFocused = null;
    }

    function _toggleSsFullscreen() {
        if (!ssOverlay) return;
        if (!document.fullscreenElement) {
            ssOverlay.requestFullscreen().then(() => ssOverlay.classList.add('is-fullscreen')).catch(() => {});
        } else {
            document.exitFullscreen().then(() => ssOverlay.classList.remove('is-fullscreen')).catch(() => {});
        }
    }

    // ── Slideshow event listeners ──────────────────────────────────────────
    if (ssClose)      ssClose.addEventListener('click', closeSlideshow);
    if (ssPrev)       ssPrev.addEventListener('click', () => _ssGoTo(ssIndex - 1));
    if (ssNext)       ssNext.addEventListener('click', () => _ssGoTo(ssIndex + 1));
    if (ssFullscreen) ssFullscreen.addEventListener('click', _toggleSsFullscreen);

    if (ssOverlay) {
        if (ssBackdrop) {
            ssBackdrop.addEventListener('click', closeSlideshow);
        }
        ssOverlay.addEventListener('mousemove', () => {
            if (!ssOpen) return;
            _showSlideshowControls();
        });
        ssOverlay.addEventListener('mouseleave', () => {
            if (!ssOpen) return;
            _hideSlideshowControlsImmediately();
        });
    }

    document.addEventListener('keydown', (e) => {
        if (!ssOpen) return;
        if (e.key === 'Tab' && ssOverlay) {
            const controls = Array.from(ssOverlay.querySelectorAll('button:not(:disabled)'))
                .filter(button => !button.hidden && button.getClientRects().length > 0);
            if (controls.length) {
                const first = controls[0];
                const last = controls[controls.length - 1];
                if (e.shiftKey && document.activeElement === first) {
                    e.preventDefault();
                    last.focus();
                } else if (!e.shiftKey && document.activeElement === last) {
                    e.preventDefault();
                    first.focus();
                }
            }
            return;
        }
        const isArrow = (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === 'ArrowLeft' || e.key === 'ArrowUp');
        if (isArrow) {
            e.preventDefault();
            const delta = (e.key === 'ArrowRight' || e.key === 'ArrowDown') ? 1 : -1;
            _ssGoTo(ssIndex + delta);
            if (ssOverlay) {
                ssOverlay.classList.remove('show-controls');
            }
            return;
        }
        if (e.key === 'Escape') closeSlideshow();
        if (e.key === 'f' || e.key === 'F') _toggleSsFullscreen();
    });

    document.addEventListener('fullscreenchange', () => {
        if (!ssOverlay) return;
        if (!document.fullscreenElement) ssOverlay.classList.remove('is-fullscreen');
    });

    // ══════════════════════════════════════════════════════════════════════
    // SSE Event Handler for Pipeline Events
    // ══════════════════════════════════════════════════════════════════════

    /**
     * Handle a slide_presentation_evt SSE event from the pipeline.
     * Called from the sending flow when obj.t === 'slide_presentation_evt'.
     * @param {object} obj  - the full slide_presentation_evt object
     * @param {string} [messageId] - the assistant message ID
     */
    function handleSlidePresentationEvent(obj, messageId) {
        const event = obj.event;
        const data = obj.data || {};

        switch (event) {
            case 'status': {
                const phase = data.phase || '';
                if (phase === 'generating') {
                    if (!_getConnectedGeneratingCard(messageId)) {
                        _addGeneratingCard(messageId, { title: data.title });
                    }
                    _beginGenerationPreview(data.title || t('slide_presentation_default_title', 'Presentation'));
                    if (data.title && previewTitle) previewTitle.textContent = data.title;
                    _updateGeneratingCard({
                        title: data.title,
                        subtitle: t('slide_presentation_generating_title', 'Generating presentation...'),
                    });
                    _setGenPhase('styles');
                    _setPreviewBusy(true);
                    updatePreviewStatus(t('slide_presentation_generating', 'Generating…'));
                } else if (phase === 'rendering' || phase === 'refining') {
                    _flushPendingHtmlDelta();
                    _setGenPhase('finalizing');
                    updatePreviewStatus(
                        phase === 'refining'
                            ? t('slide_presentation_reviewing', 'Reviewing visual quality…')
                            : t('slide_presentation_rendering', 'Rendering slide previews…'),
                        false,
                        phase === 'refining' ? 'refining' : 'rendering'
                    );
                    _updateGeneratingCard({
                        subtitle: phase === 'refining'
                            ? t('slide_presentation_reviewing', 'Reviewing visual quality…')
                            : t('slide_presentation_rendering', 'Rendering slide previews…'),
                    });
                }
                break;
            }

            case 'tool_call':
            case 'tool_call_delta':
                break;

            case 'html_delta':
                _beginGenerationPreview(t('slide_presentation_default_title', 'Presentation'));
                if (!_getConnectedGeneratingCard(messageId)) {
                    _addGeneratingCard(messageId);
                }
                _queueHtmlDelta(data.delta || '');
                break;

            case 'draft_complete': {
                _flushPendingHtmlDelta();
                slidePresentationPresentationId = data.presentation_id || slidePresentationPresentationId;
                if (data.title && previewTitle) previewTitle.textContent = data.title;
                _setGenPhase('finalizing');
                updatePreviewStatus(
                    t('slide_presentation_draft_ready_rendering', 'Draft ready · rendering accurate previews…'),
                    false,
                    'rendering'
                );
                _updateGeneratingCard({
                    title: data.title,
                    subtitle: t('slide_presentation_draft_ready_rendering', 'Draft ready · rendering accurate previews…'),
                });
                break;
            }

            case 'revision_ready': {
                const presId = data.presentation_id;
                const count = data.count || 0;
                const revision = Number(data.revision) || 0;
                if (presId && count > 0 && revision > slidePresentationRenderedRevision) {
                    slidePresentationPresentationId = presId;
                    _loadSlideImages(presId, count, revision, { draft: true })
                        .catch(err => console.warn('Could not load provisional slide revision', err));
                }
                break;
            }

            case 'slide_images': {
                const presId = data.presentation_id;
                const count = data.count || 0;
                if (presId && count > 0) {
                    slidePresentationPresentationId = presId;
                    _loadSlideImages(presId, count, Number(data.revision) || 0)
                        .then((loaded) => {
                            if (loaded) return;
                            if (previewPresent) previewPresent.disabled = true;
                            updatePreviewStatus(
                                t('slide_presentation_rendering_failed', 'Rendering failed'),
                                false
                            );
                        })
                        .catch(err => {
                            if (previewPresent) previewPresent.disabled = true;
                            updatePreviewStatus(
                                t('slide_presentation_rendering_failed', 'Rendering failed'),
                                false
                            );
                            console.error('Failed to load slide images', err);
                        });
                }
                break;
            }

            case 'complete':
                completePreview(data.file_id, data.presentation_id, data.title, data.slide_count, data.operation || 'created');
                _addCompletionCard(messageId, data);
                _setAssistantMessageListVisible(_activeMessageId || messageId, true);
                _generatingCard = null;
                _finishGenerationTracking();
                _activeMessageId = null;
                break;

            case 'warning':
                updatePreviewStatus(
                    t('slide_presentation_polishing_warning', 'Draft ready · automatic polishing stopped'),
                    false,
                    'warning'
                );
                _updateGeneratingCard({
                    subtitle: t('slide_presentation_polishing_warning', 'Draft ready · automatic polishing stopped'),
                });
                break;

            case 'error':
                {
                    _discardFailedGenerationPreview();
                    _removeGeneratingCard(_activeMessageId || messageId);
                    _setAssistantMessageListVisible(_activeMessageId || messageId, true);
                    _activeMessageId = null;
                    _finishGenerationTracking();
                }
                break;
        }
    }

    // ══════════════════════════════════════════════════════════════════════
    // Canvas Button (Header Toggle)
    // ══════════════════════════════════════════════════════════════════════

    function toggleSlidePresentationPreview() {
        if (slidePresentationPreviewVisible) {
            hidePreviewPanel();
        } else if (_generationInProgress && _generationPreviewInitialized) {
            _setPanelVisible(true);
        } else if (_slideItems.length > 0) {
            _setPanelVisible(true);
            requestAnimationFrame(() => {
                _rescaleAllSlideItems();
                _updateThumbnails();
            });
        } else if (slidePresentationSlideImages.length > 0) {
            _restorePreviewFromImages(true);
        }
    }

    // ══════════════════════════════════════════════════════════════════════
    // History Restore — render slide_presentation_result block saved in DB
    // ══════════════════════════════════════════════════════════════════════

    /**
     * Populate the preview panel with image-only slide items (no HTML needed).
     * Used when restoring from history where only images are available.
     */
    function _restorePreviewFromImages(autoShow = false, options = {}) {
        if (!slidePresentationSlideImages.length) return;
        
        // Only restore if we have a valid presentation context
        if (!slidePresentationFileId && !slidePresentationPresentationId) {
            console.warn('Attempting to restore slide images without presentation context');
            return;
        }

        slidePresentationSlides = slidePresentationSlideImages.map(() => '');
        const preservedIndex = Math.max(0, Math.min(
            Number(options.preserveIndex) || 0,
            slidePresentationSlideImages.length - 1,
        ));
        slidePresentationCurrentIndex = preservedIndex;
        _slideItems = [];
        _destroySlideObserver();

        if (previewThumbnails) previewThumbnails.innerHTML = '';
        if (previewSlidesTrack) previewSlidesTrack.innerHTML = '';
        if (previewGenerating) previewGenerating.classList.add('hidden');
        if (previewNav) previewNav.classList.add('visible');
        if (previewSidebarToggle) previewSidebarToggle.disabled = false;
        _syncPreviewSidebarToggleState();
        if (previewPresent) previewPresent.disabled = false;

        slidePresentationSlideImages.forEach((url, idx) => {
            // Main track item
            const item = document.createElement('div');
            item.className = 'slide-presentation-preview-slide-item';
            item.dataset.slideIndex = idx;

            const img = url ? document.createElement('img') : null;
            if (img) {
                img.src = url;
                img.alt = tf('slide_presentation_slide_number', 'Slide {number}', { number: idx + 1 });
                item.appendChild(img);
            } else {
                const placeholder = document.createElement('div');
                placeholder.className = 'slide-presentation-preview-image-placeholder';
                placeholder.textContent = tf('slide_presentation_slide_number', 'Slide {number}', { number: idx + 1 });
                item.appendChild(placeholder);
            }
            previewSlidesTrack.appendChild(item);
            _slideItems.push(item);

            // Thumbnail
            if (previewThumbnails) {
                const thumb = document.createElement('button');
                thumb.type = 'button';
                thumb.className = 'slide-presentation-preview-thumbnail';
                thumb.dataset.slideIndex = idx;
                thumb.setAttribute('aria-label', tf('slide_presentation_slide_number', 'Slide {number}', { number: idx + 1 }));
                if (url) {
                    const tImg = document.createElement('img');
                    tImg.src = url;
                    tImg.alt = tf('slide_presentation_slide_number', 'Slide {number}', { number: idx + 1 });
                    thumb.appendChild(tImg);
                } else {
                    const placeholder = document.createElement('div');
                    placeholder.className = 'slide-presentation-preview-image-placeholder';
                    placeholder.textContent = String(idx + 1);
                    thumb.appendChild(placeholder);
                }
                thumb.addEventListener('click', () => {
                    _goToSlide(idx);
                });
                previewThumbnails.appendChild(thumb);
            }
        });

        _buildSlideObserver();
        _setCurrentSlideIndex(preservedIndex, { ensureThumbnailVisible: false });
        requestAnimationFrame(() => _scrollTrackToSlide(preservedIndex, 'auto'));

        if (autoShow || slidePresentationPreviewVisible) {
            _setPanelVisible(true);
            requestAnimationFrame(() => {
                _rescaleAllSlideItems();
                _updateThumbnails();
            });
        }
    }

    /**
     * Called from chats.js when rendering a saved slide_presentation_result block.
     * Appends the completion card and restores the preview panel state.
     */
    function renderSlidePresentationResultBlock(messageId, meta) {
        if (!meta) return;
        const context = _normalizePresentationContext(meta);
        const { presentationId, fileId, title, operation } = context;

        if ((fileId || presentationId) && window.canvasFilesDropdown) {
            const registerId = presentationId || fileId;
            window.canvasFilesDropdown.registerFile(registerId, title, 'slide-presentation', function () {
                _openPresentationContext(context).catch((error) => {
                    console.error('[slide-presentation] Failed to open presentation from restored result card', error);
                });
            });
        }

        // Find parent container
        let parent = null;
        if (messageId) parent = document.getElementById('a-' + messageId);
        if (!parent) parent = document.getElementById('chatAreaContainer');
        if (!parent) return;

        const card = document.createElement('div');
        _renderCompletionCardState(card, {
            status: 'ready',
            title: operation === 'updated'
                ? t('slide_presentation_updated', 'Presentation updated')
                : tf('slide_presentation_ready_title', '{title} ready', {
                    title: title || t('slide_presentation_default_title', 'Presentation'),
                }),
            subtitle: operation === 'updated'
                ? t('slide_presentation_updated_ready_desc', 'The latest version is ready in the preview panel.')
                : t('slide_presentation_ready_desc', 'Download or view from the preview panel.'),
        });

        _setCompletionCardContext(card, context);
        _makeCompletionCardBtn(card, context);
        _updateCompletionCardText(card);

        _insertCompletionCard(parent, card);
    }

    async function openExistingPresentationPreview(options = {}) {
        const context = await _resolveLatestPresentationContext(options);
        const presentationId = context.presentationId;
        const fileId = context.fileId;
        const title = context.title;

        if (!presentationId) {
            throw new Error(t('slide_presentation_preview_unavailable_for_file', 'Presentation preview is not available for this file.'));
        }

        _revokeSlideImages();
        showPreviewPanel(title);

        slidePresentationFileId = fileId || null;
        slidePresentationPresentationId = presentationId;
        updatePreviewStatus(t('slide_presentation_loading_slides', 'Loading slides...'), false);

        if (previewGenerating) previewGenerating.classList.remove('hidden');
        if (previewNav) previewNav.classList.remove('visible');
        if (previewSidebarToggle) previewSidebarToggle.disabled = true;
        if (previewPresent) previewPresent.disabled = true;

        // Result blocks are immutable chat history, while editor saves are
        // mutable. Always refresh the count so an older card cannot omit newly
        // added slides or request slides that were removed later.
        const slideCount = await _fetchSlideCountAndLoad(presentationId, 0);

        if (!slidePresentationSlideImages.length) {
            hidePreviewPanel();
            throw new Error(t('slide_presentation_slide_images_unavailable', 'Slide preview images are not available for this presentation.'));
        }

        completePreview(fileId || null, presentationId, title, slideCount, context.operation || 'created');
        _refreshStoredPresentationContext({ ...context, slideCount });
    }

    // ── Expose to global scope ────────────────────────────────────────────
    window.slidePresentationWidget = {
        handlePptxEvent: handleSlidePresentationEvent,
        handleSlidePresentationEvent: handleSlidePresentationEvent,
        showPreviewPanel: showPreviewPanel,
        hidePreviewPanel: hidePreviewPanel,
        togglePptxPreview: toggleSlidePresentationPreview,
        toggleSlidePresentationPreview: toggleSlidePresentationPreview,
        appendHtmlDelta: appendHtmlDelta,
        completePreview: completePreview,
        openSlideshow: openSlideshow,
        closeSlideshow: closeSlideshow,
        renderPptxResultBlock: renderSlidePresentationResultBlock,
        renderSlidePresentationResultBlock: renderSlidePresentationResultBlock,
        renderSlidePresentationErrorBlock: renderSlidePresentationErrorBlock,
        handleStreamEnd: handleStreamEnd,
        reset: reset,
        openExistingPresentationPreview: openExistingPresentationPreview,
        openEditor: openPresentationEditor,
        closeEditor: closePresentationEditor,
        isPreviewOpen: isPreviewPanelVisible,
        getActiveFileId: getActivePreviewFileId,
    };

})();
