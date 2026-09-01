/**
 * Coordinate scroll position while chat content is changing during a stream.
 *
 * Streaming can replace a large Markdown subtree several times per second. A
 * delayed scroll correction that was captured before a wheel or touch gesture
 * must never run after that gesture, otherwise the viewport appears to resist
 * the user. This controller owns those corrections and invalidates them as
 * soon as direct user intent is detected.
 */
const ChatScrollManager = (() => {
    const VIEWPORT_SELECTOR = '.chat-area, .split-chat-area, .subagent-modal-scroll';
    const CONTAINER_SELECTOR = '.chat-area-container, .split-chat-area-container, .subagent-modal-chat';
    const PRESERVE_THRESHOLD = 80;
    const AUTO_FOLLOW_THRESHOLD = 100;
    const AUTO_FOLLOW_RESUME_THRESHOLD = 2;
    const SCROLL_KEYS = new Set(['ArrowDown', 'ArrowUp', 'End', 'Home', 'PageDown', 'PageUp', ' ']);
    const viewportStates = new WeakMap();

    let keyboardListenerInstalled = false;
    let activeScrollbarViewport = null;

    /** Return true when the value is a usable DOM element. */
    function isElement(value) {
        return typeof Element !== 'undefined' && value instanceof Element;
    }

    /** Return true when the value is a usable scroll viewport. */
    function isHtmlElement(value) {
        return typeof HTMLElement !== 'undefined' && value instanceof HTMLElement;
    }

    /** Measure the non-negative number of pixels below the viewport. */
    function distanceFromBottom(viewport) {
        if (!isHtmlElement(viewport)) return 0;
        return Math.max(viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop, 0);
    }

    /** Respect the browser's operating-system motion preference. */
    function shouldReduceMotion() {
        try {
            return typeof window.matchMedia === 'function'
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        } catch (_error) {
            return false;
        }
    }

    /**
     * Apply a correction synchronously even when a split pane declares
     * `scroll-behavior: smooth` in CSS. Streaming corrections must never start
     * overlapping animations.
     */
    function setImmediateScrollTop(viewport, top) {
        if (!isHtmlElement(viewport)) return;
        if (!viewport.style) {
            viewport.scrollTop = top;
            return;
        }
        const previousBehavior = viewport.style.scrollBehavior || '';
        viewport.style.scrollBehavior = 'auto';
        if (typeof viewport.scrollTo === 'function') {
            viewport.scrollTo({ top, behavior: 'auto' });
        } else {
            viewport.scrollTop = top;
        }
        viewport.style.scrollBehavior = previousBehavior;
    }

    /** Resolve either a viewport itself or the nearest chat viewport around a child. */
    function resolveViewport(target) {
        if (!isElement(target)) return null;
        // Nested tool previews are explicitly bound even though they do not
        // use a chat viewport class. Prefer that direct binding over an outer
        // transcript returned by closest().
        if (viewportStates.has(target)) return target;
        if (target.matches(VIEWPORT_SELECTOR)) return target;
        const viewport = target.closest(VIEWPORT_SELECTOR);
        return isHtmlElement(viewport) ? viewport : null;
    }

    /** Find the direct transcript container whose children are stable anchors. */
    function resolveContainer(target, viewport) {
        if (isElement(target)) {
            const closest = target.closest(CONTAINER_SELECTOR);
            if (isHtmlElement(closest)) return closest;
        }
        if (!isHtmlElement(viewport)) return null;
        const nested = viewport.querySelector(CONTAINER_SELECTOR);
        return isHtmlElement(nested) ? nested : null;
    }

    /** Cancel every delayed write owned by a viewport state. */
    function cancelPendingWrites(state) {
        if (!state) return;
        if (state.restoreFrame) {
            cancelAnimationFrame(state.restoreFrame);
            state.restoreFrame = 0;
        }
        if (state.followFrame) {
            cancelAnimationFrame(state.followFrame);
            state.followFrame = 0;
        }
    }

    /** Stop a native smooth scroll at its current position. */
    function stopSmoothScroll(viewport, state) {
        if (!state?.smoothScrollActive || !isHtmlElement(viewport)) return;
        state.smoothScrollActive = false;
        if (state.smoothScrollTimer) {
            clearTimeout(state.smoothScrollTimer);
            state.smoothScrollTimer = 0;
        }
        beginProgrammaticScroll(state);
        setImmediateScrollTop(viewport, viewport.scrollTop);
        clearProgrammaticScrollAfterPaint(state);
    }

    /** Record direct user intent before the browser applies the resulting scroll. */
    function interrupt(viewport) {
        const state = bind(viewport);
        if (!state) return;
        state.inputRevision += 1;
        state.userInterrupted = true;
        state.autoFollow = false;
        cancelPendingWrites(state);
        stopSmoothScroll(viewport, state);
    }

    /** Ignore keystrokes that belong to editable controls rather than the transcript. */
    function isEditableTarget(target) {
        if (!isElement(target)) return false;
        return Boolean(target.closest('input, textarea, select, [contenteditable="true"], [role="textbox"]'));
    }

    /** Install one document-level handler because the main transcript is not focusable. */
    function installKeyboardListener() {
        if (keyboardListenerInstalled || typeof document === 'undefined') return;
        keyboardListenerInstalled = true;
        document.addEventListener('keydown', (event) => {
            if (!SCROLL_KEYS.has(event.key) || event.defaultPrevented || isEditableTarget(event.target)) return;

            const targetedViewport = isElement(event.target) ? event.target.closest(VIEWPORT_SELECTOR) : null;
            const mainViewport = document.getElementById('chatArea');
            const viewport = isHtmlElement(targetedViewport) ? targetedViewport : mainViewport;
            if (isHtmlElement(viewport) && viewport.scrollHeight > viewport.clientHeight + 1) {
                interrupt(viewport);
            }
        }, { capture: true });
        document.addEventListener('mouseup', () => {
            activeScrollbarViewport = null;
        }, { capture: true, passive: true });
    }

    /**
     * Bind intent listeners once and return the mutable state for a viewport.
     * The initial follow decision mirrors the viewport's current position.
     */
    function bind(viewport) {
        if (!isHtmlElement(viewport)) return null;
        const existing = viewportStates.get(viewport);
        if (existing) return existing;

        const initiallyNearBottom = distanceFromBottom(viewport) <= AUTO_FOLLOW_THRESHOLD;
        const state = {
            inputRevision: 0,
            autoFollow: initiallyNearBottom,
            userInterrupted: !initiallyNearBottom,
            restoreFrame: 0,
            followFrame: 0,
            smoothScrollActive: false,
            smoothScrollTimer: 0,
            programmaticScroll: false,
            programmaticScrollFrame: 0,
            touchStartY: null,
        };
        viewportStates.set(viewport, state);

        viewport.addEventListener('wheel', () => interrupt(viewport), { capture: true, passive: true });
        viewport.addEventListener('touchstart', (event) => {
            state.touchStartY = event.touches?.[0]?.clientY ?? null;
        }, { capture: true, passive: true });
        viewport.addEventListener('touchmove', (event) => {
            const nextY = event.touches?.[0]?.clientY ?? null;
            if (nextY !== null && state.touchStartY !== null && Math.abs(nextY - state.touchStartY) > 2) {
                interrupt(viewport);
            }
            state.touchStartY = nextY;
        }, { capture: true, passive: true });
        viewport.addEventListener('mousedown', (event) => {
            // A pointer press only represents scroll intent when it lands on the
            // native scrollbar gutter. Ordinary clicks in a message must not
            // turn off following.
            const rect = viewport.getBoundingClientRect();
            const scrollbarWidth = Math.max(viewport.offsetWidth - viewport.clientWidth, 0);
            if (scrollbarWidth > 0 && event.clientX >= rect.right - scrollbarWidth - 2) {
                activeScrollbarViewport = viewport;
                interrupt(viewport);
            }
        }, { capture: true, passive: true });
        viewport.addEventListener('scroll', () => {
            if (state.programmaticScroll) {
                return;
            }
            // Once the user deliberately reaches the bottom, future streaming
            // updates may follow again. A tight threshold avoids resuming while
            // the user is merely near the bottom and still scrolling upward.
            const remaining = distanceFromBottom(viewport);
            if (activeScrollbarViewport === viewport
                && !state.smoothScrollActive
                && remaining > AUTO_FOLLOW_RESUME_THRESHOLD) {
                // A thumb drag produces one mousedown followed by many scroll
                // events. Treat every movement as newer input so a snapshot
                // captured mid-drag cannot win the next frame.
                state.inputRevision += 1;
                state.userInterrupted = true;
                state.autoFollow = false;
                cancelPendingWrites(state);
            }
            if (!state.smoothScrollActive
                && state.userInterrupted
                && remaining <= AUTO_FOLLOW_RESUME_THRESHOLD) {
                state.userInterrupted = false;
                state.autoFollow = true;
            }
        }, { passive: true });

        installKeyboardListener();
        return state;
    }

    /** Clear a positioning marker after its resulting scroll event is delivered. */
    function clearProgrammaticScrollAfterPaint(state) {
        if (!state) return;
        if (state.programmaticScrollFrame) {
            cancelAnimationFrame(state.programmaticScrollFrame);
        }
        state.programmaticScrollFrame = requestAnimationFrame(() => {
            state.programmaticScrollFrame = 0;
            state.programmaticScroll = false;
        });
    }

    /** Mark a positioning write and supersede an older pending marker reset. */
    function beginProgrammaticScroll(state) {
        if (!state) return;
        if (state.programmaticScrollFrame) {
            cancelAnimationFrame(state.programmaticScrollFrame);
            state.programmaticScrollFrame = 0;
        }
        state.programmaticScroll = true;
    }

    /** Start a stream with an explicit or position-derived follow policy. */
    function beginStream(target, options = {}) {
        const viewport = resolveViewport(target) || (isHtmlElement(target) ? target : null);
        const state = bind(viewport);
        if (!state) return false;
        cancelPendingWrites(state);
        const shouldFollow = typeof options.autoFollow === 'boolean'
            ? options.autoFollow
            : distanceFromBottom(viewport) <= AUTO_FOLLOW_THRESHOLD;
        state.autoFollow = shouldFollow;
        state.userInterrupted = !shouldFollow;
        return shouldFollow;
    }

    /** End a stream and invalidate pending stream-owned writes. */
    function endStream(target) {
        const viewport = resolveViewport(target) || (isHtmlElement(target) ? target : null);
        const state = viewportStates.get(viewport);
        if (!state) return;
        if (state.followFrame) {
            cancelAnimationFrame(state.followFrame);
            state.followFrame = 0;
        }
        // Guarded anchor corrections may still be covering the final math or
        // font layout. Leave them active; any user input cancels them before
        // the browser applies that input.
    }

    /** Return whether a direct transcript child participates in visual layout. */
    function isRenderedAnchorCandidate(element) {
        if (!isHtmlElement(element)) return false;
        if (element.hidden || element.hasAttribute?.('hidden')) return false;
        if (element.dataset?.hidden === 'true') return false;
        if (element.style?.display === 'none') return false;
        if (element.style?.visibility === 'hidden' || element.style?.visibility === 'collapse') {
            return false;
        }

        try {
            const computedStyle = typeof window.getComputedStyle === 'function'
                ? window.getComputedStyle(element)
                : null;
            if (
                computedStyle?.display === 'none'
                || computedStyle?.visibility === 'hidden'
                || computedStyle?.visibility === 'collapse'
                || computedStyle?.contentVisibility === 'hidden'
            ) {
                return false;
            }
        } catch (_error) {
            // Detached test doubles and nodes crossing document boundaries may
            // reject computed-style reads. Their client rectangles below remain
            // the authoritative visibility check.
        }

        if (typeof element.getClientRects === 'function' && element.getClientRects().length === 0) {
            return false;
        }
        return true;
    }

    /** Select the visually first intersecting entry without ordering assumptions. */
    function findVisibleAnchorLinear(entries, viewportRect) {
        let candidate = null;
        entries.forEach((entry) => {
            const intersectsViewport = entry.rect.bottom > viewportRect.top + 1
                && entry.rect.top < viewportRect.bottom - 1;
            if (!intersectsViewport) return;
            if (
                !candidate
                || entry.rect.top < candidate.rect.top
                || (
                    Math.abs(entry.rect.top - candidate.rect.top) < 0.5
                    && entry.rect.bottom < candidate.rect.bottom
                )
            ) {
                candidate = entry;
            }
        });
        return candidate?.element || null;
    }

    /**
     * Find the first visible top-level transcript item.
     *
     * The binary path is retained for normally ordered chat flow, but only
     * after one stable rectangle snapshot proves that its predicate is
     * monotonic. Hidden assistant versions have no client rectangles and are
     * excluded before this check. Unusual positioned or transformed children
     * fall back to a visual-order linear selection instead of producing a
     * stale anchor during Markdown reflow.
     */
    function findVisibleAnchor(container, viewportRect) {
        if (!isHtmlElement(container)) return null;
        const entries = Array.from(container.children)
            .filter(isRenderedAnchorCandidate)
            .map((element) => ({
                element,
                rect: element.getBoundingClientRect(),
            }))
            .filter(({ rect }) => (
                Number.isFinite(rect?.top)
                && Number.isFinite(rect?.bottom)
            ));
        if (!entries.length) return null;

        let rectanglesAreOrdered = true;
        for (let index = 1; index < entries.length; index += 1) {
            const previous = entries[index - 1].rect;
            const current = entries[index].rect;
            if (
                current.top < previous.top - 0.5
                || current.bottom < previous.bottom - 0.5
            ) {
                rectanglesAreOrdered = false;
                break;
            }
        }
        if (!rectanglesAreOrdered) {
            return findVisibleAnchorLinear(entries, viewportRect);
        }

        let low = 0;
        let high = entries.length - 1;
        let candidate = entries.length - 1;
        while (low <= high) {
            const middle = Math.floor((low + high) / 2);
            const rect = entries[middle].rect;
            if (rect.bottom > viewportRect.top + 1) {
                candidate = middle;
                high = middle - 1;
            } else {
                low = middle + 1;
            }
        }

        const anchor = entries[candidate];
        return anchor.rect.top < viewportRect.bottom - 1 ? anchor.element : null;
    }

    /** Capture a stable visual anchor before a potentially resizing mutation. */
    function capture(target) {
        const viewport = resolveViewport(target);
        const container = resolveContainer(target, viewport);
        const state = bind(viewport);
        if (!viewport || !container || !state) return null;
        if (state.smoothScrollActive) return null;
        if (viewport.scrollHeight <= viewport.clientHeight + 1) return null;
        if (!state.userInterrupted && distanceFromBottom(viewport) <= PRESERVE_THRESHOLD) return null;

        // A newer mutation supersedes the late correction for an older DOM.
        if (state.restoreFrame) {
            cancelAnimationFrame(state.restoreFrame);
            state.restoreFrame = 0;
        }
        const viewportRect = viewport.getBoundingClientRect();
        const anchor = findVisibleAnchor(container, viewportRect);
        return {
            viewport,
            anchor,
            anchorTop: anchor ? anchor.getBoundingClientRect().top : 0,
            fallbackScrollTop: viewport.scrollTop,
            inputRevision: state.inputRevision,
        };
    }

    /** Apply one correction only while no newer user input has occurred. */
    function applySnapshot(snapshot) {
        if (!snapshot) return false;
        const { viewport, anchor, anchorTop, fallbackScrollTop, inputRevision } = snapshot;
        const state = viewportStates.get(viewport);
        if (!state || state.inputRevision !== inputRevision || !viewport.isConnected) return false;

        let nextScrollTop = viewport.scrollTop;
        if (isHtmlElement(anchor) && anchor.isConnected) {
            // Apply only the remaining visual delta. Reusing the original
            // scrollTop here would undo a correction on the following frame.
            nextScrollTop += anchor.getBoundingClientRect().top - anchorTop;
        } else {
            nextScrollTop = fallbackScrollTop;
        }
        const maxScrollTop = Math.max(viewport.scrollHeight - viewport.clientHeight, 0);
        const clampedScrollTop = Math.min(Math.max(nextScrollTop, 0), maxScrollTop);
        // Avoid generating redundant scroll events when a mutation below the
        // viewport did not move the visible anchor at all.
        if (Math.abs(clampedScrollTop - viewport.scrollTop) >= 0.5) {
            setImmediateScrollTop(viewport, clampedScrollTop);
        }
        return true;
    }

    /**
     * Restore immediately, then cover two layout frames for async math/font
     * sizing. Both frames are cancellable before the browser handles a wheel or
     * touch gesture, which is the key guarantee missing from the old logic.
     */
    function restore(snapshot) {
        if (!snapshot || !applySnapshot(snapshot)) return;
        const state = viewportStates.get(snapshot.viewport);
        if (!state || typeof requestAnimationFrame !== 'function') return;

        let framesRemaining = 2;
        const correctAfterLayout = () => {
            state.restoreFrame = 0;
            if (!applySnapshot(snapshot)) return;
            framesRemaining -= 1;
            if (framesRemaining > 0) {
                state.restoreFrame = requestAnimationFrame(correctAfterLayout);
            }
        };
        state.restoreFrame = requestAnimationFrame(correctAfterLayout);
    }

    /** Run a synchronous DOM mutation while maintaining the visible anchor. */
    function preserveDuringMutation(target, mutate) {
        const snapshot = capture(target);
        try {
            return mutate();
        } finally {
            restore(snapshot);
        }
    }

    /** Follow new content on one animation frame if the user has not detached. */
    function scheduleFollow(target) {
        const viewport = resolveViewport(target) || (isHtmlElement(target) ? target : null);
        const state = bind(viewport);
        if (!state || !state.autoFollow || state.userInterrupted || state.followFrame) return false;
        const revision = state.inputRevision;
        state.followFrame = requestAnimationFrame(() => {
            state.followFrame = 0;
            if (state.inputRevision !== revision || state.userInterrupted || !state.autoFollow) return;
            setImmediateScrollTop(viewport, Math.max(viewport.scrollHeight - viewport.clientHeight, 0));
        });
        return true;
    }

    /** Scroll explicitly to the bottom and opt back into streaming follow. */
    function scrollToBottom(target, options = {}) {
        const viewport = resolveViewport(target) || (isHtmlElement(target) ? target : null);
        const state = bind(viewport);
        if (!state) return false;
        cancelPendingWrites(state);
        state.autoFollow = true;
        state.userInterrupted = false;

        const behavior = options.behavior === 'smooth' && !shouldReduceMotion() ? 'smooth' : 'auto';
        const top = Math.max(viewport.scrollHeight - viewport.clientHeight, 0);
        if (behavior === 'smooth' && typeof viewport.scrollTo === 'function') {
            state.smoothScrollActive = true;
            viewport.scrollTo({ top, behavior });
            state.smoothScrollTimer = setTimeout(() => {
                state.smoothScrollActive = false;
                state.smoothScrollTimer = 0;
            }, 500);
        } else {
            setImmediateScrollTop(viewport, top);
        }
        return true;
    }

    /** Position a newly sent user message without enabling bottom following. */
    function scrollToPosition(target, top, options = {}) {
        const viewport = resolveViewport(target) || (isHtmlElement(target) ? target : null);
        const state = bind(viewport);
        if (!state) return false;
        cancelPendingWrites(state);
        state.autoFollow = false;
        state.userInterrupted = true;
        beginProgrammaticScroll(state);

        const behavior = options.behavior === 'smooth' && !shouldReduceMotion() ? 'smooth' : 'auto';
        if (behavior === 'smooth' && typeof viewport.scrollTo === 'function') {
            state.smoothScrollActive = true;
            viewport.scrollTo({ top, behavior });
            state.smoothScrollTimer = setTimeout(() => {
                state.smoothScrollActive = false;
                state.smoothScrollTimer = 0;
                clearProgrammaticScrollAfterPaint(state);
            }, 500);
        } else {
            setImmediateScrollTop(viewport, top);
            clearProgrammaticScrollAfterPaint(state);
        }
        return true;
    }

    /** Expose follow state for integrations and focused behavioral tests. */
    function isFollowing(target) {
        const viewport = resolveViewport(target) || (isHtmlElement(target) ? target : null);
        const state = bind(viewport);
        return Boolean(state && state.autoFollow && !state.userInterrupted);
    }

    return {
        beginStream,
        bind,
        capture,
        distanceFromBottom,
        endStream,
        interrupt,
        isFollowing,
        preserveDuringMutation,
        restore,
        scheduleFollow,
        scrollToBottom,
        scrollToPosition,
    };
})();

if (typeof window !== 'undefined') {
    window.ChatScrollManager = ChatScrollManager;
}
