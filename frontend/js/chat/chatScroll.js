/**
 * Coordinate chat scrolling without confusing browser-generated scroll events
 * with deliberate user navigation.
 *
 * The coordinator owns one alignment transaction per scroll viewport. A new
 * transaction supersedes the old one, which prevents nested animation-frame
 * callbacks from competing when messages are sent quickly.
 */
(function initializeChatScrollCoordinator(globalObject) {
    'use strict';

    const DEFAULT_ANIMATION_DURATION_MS = 360;
    const ALIGNMENT_GUARD_DURATION_MS = 6000;
    const ALIGNMENT_GUARD_INTERVAL_MS = 100;
    const USER_SCROLL_INTENT_GRACE_MS = 900;
    const ALIGNMENT_TOLERANCE_PX = 1;
    const SCROLL_KEYS = new Set([
        'ArrowDown',
        'ArrowUp',
        'End',
        'Home',
        'PageDown',
        'PageUp',
        ' ',
        'Spacebar',
    ]);

    /**
     * Build a coordinator. The optional runtime is injectable so the geometry
     * and timing behavior can be tested without a browser.
     *
     * @param {Window|object} runtime Browser window or a compatible test runtime.
     * @returns {object} Public scrolling API.
     */
    function createChatScrollCoordinator(runtime = globalObject) {
        const documentRef = runtime?.document || null;
        const viewportStates = new WeakMap();

        const requestFrame = typeof runtime?.requestAnimationFrame === 'function'
            ? runtime.requestAnimationFrame.bind(runtime)
            : (callback) => runtime.setTimeout(() => callback(now()), 16);
        const cancelFrame = typeof runtime?.cancelAnimationFrame === 'function'
            ? runtime.cancelAnimationFrame.bind(runtime)
            : runtime.clearTimeout?.bind(runtime);
        const scheduleTimeout = typeof runtime?.setTimeout === 'function'
            ? runtime.setTimeout.bind(runtime)
            : setTimeout;
        const cancelTimeout = typeof runtime?.clearTimeout === 'function'
            ? runtime.clearTimeout.bind(runtime)
            : clearTimeout;

        function now() {
            if (runtime?.performance && typeof runtime.performance.now === 'function') {
                return runtime.performance.now();
            }
            return Date.now();
        }

        function isElement(value) {
            const HTMLElementCtor = runtime?.HTMLElement;
            if (typeof HTMLElementCtor === 'function') {
                return value instanceof HTMLElementCtor;
            }
            return Boolean(value && typeof value === 'object' && typeof value.getBoundingClientRect === 'function');
        }

        function resolveTargets(options = {}) {
            const viewport = isElement(options.viewport)
                ? options.viewport
                : documentRef?.getElementById?.('chatArea');
            const container = isElement(options.container)
                ? options.container
                : documentRef?.getElementById?.('chatAreaContainer');

            if (!viewport || !container) {
                return null;
            }
            return { viewport, container };
        }

        function getState(viewport, container = null) {
            let state = viewportStates.get(viewport);
            if (!state) {
                state = {
                    activeAlignment: null,
                    bound: false,
                    container,
                    lastUserIntentAt: Number.NEGATIVE_INFINITY,
                    pointerActive: false,
                    spacerUpdateFrame: null,
                    sequence: 0,
                };
                viewportStates.set(viewport, state);
            } else if (container) {
                state.container = container;
            }
            return state;
        }

        function getSpacer(container) {
            return container?.querySelector?.('.dynamic-scroll-spacer') || null;
        }

        function getRenderedSpacerHeight(spacer) {
            if (!spacer) {
                return 0;
            }
            const inlineHeight = Number.parseFloat(spacer.style?.height || '');
            if (Number.isFinite(inlineHeight)) {
                return Math.max(0, inlineHeight);
            }
            const offsetHeight = Number(spacer.offsetHeight);
            return Number.isFinite(offsetHeight) ? Math.max(0, offsetHeight) : 0;
        }

        /**
         * Keep the spacer as the transcript's final real child. Message
         * renderers also follow this ordering rule, so streaming nodes remain
         * above the spacer and inside the reachable scroll range.
         */
        function ensureSpacer(container) {
            let spacer = getSpacer(container);
            if (!spacer) {
                spacer = documentRef?.createElement?.('div');
                if (!spacer) {
                    return null;
                }
                spacer.className = 'dynamic-scroll-spacer';
                spacer.setAttribute?.('aria-hidden', 'true');
            }

            if (spacer.parentElement !== container || container.lastElementChild !== spacer) {
                container.appendChild(spacer);
            }
            spacer.style.transition = 'none';
            return spacer;
        }

        function setSpacerHeight(spacer, height) {
            if (!spacer?.style) {
                return;
            }
            const roundedHeight = Math.max(0, Math.ceil(Number(height) || 0));
            const nextHeight = `${roundedHeight}px`;
            if (spacer.style.height !== nextHeight) {
                spacer.style.height = nextHeight;
            }
        }

        function removeSpacer(container) {
            const spacer = getSpacer(container);
            if (spacer) {
                spacer.remove();
            }
        }

        function findUserMessageContent(container, messageId) {
            if (!container || !messageId) {
                return null;
            }
            const contentId = `u-${messageId}`;
            if (runtime?.CSS && typeof runtime.CSS.escape === 'function') {
                return container.querySelector(`#${runtime.CSS.escape(contentId)}`);
            }
            return Array.from(container.querySelectorAll?.('[id]') || [])
                .find((element) => element.id === contentId) || null;
        }

        function shouldReduceMotion() {
            try {
                return runtime?.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
            } catch (_error) {
                return false;
            }
        }

        function writeScrollTop(viewport, top) {
            const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
            const boundedTop = Math.min(maxScrollTop, Math.max(0, Number(top) || 0));

            // "instant" prevents a CSS scroll-behavior rule from starting a
            // second native animation underneath the coordinator's animation.
            if (typeof viewport.scrollTo === 'function') {
                try {
                    viewport.scrollTo({ top: boundedTop, behavior: 'instant' });
                    return;
                } catch (_error) {
                    // Older engines reject "instant"; direct assignment is the
                    // compatible non-animated fallback.
                }
            }
            viewport.scrollTop = boundedTop;
        }

        function easeOutCubic(progress) {
            const inverse = 1 - Math.min(1, Math.max(0, progress));
            return 1 - (inverse * inverse * inverse);
        }

        /**
         * Measure the target from live rectangles and size the spacer to the
         * exact minimum needed to make that target reachable. Repeating this
         * calculation handles reflow, composer resizing, and viewport changes.
         */
        function measureAlignment(alignment) {
            const {
                container,
                messageArea,
                spacer,
                topGap,
                viewport,
            } = alignment;
            if (
                messageArea.parentElement !== container
                || spacer.parentElement !== container
                || !container.contains?.(messageArea)
            ) {
                return null;
            }

            const viewportRect = viewport.getBoundingClientRect();
            const messageRect = messageArea.getBoundingClientRect();
            let targetScrollTop = viewport.scrollTop + messageRect.top - viewportRect.top - topGap;

            // Remove the current spacer contribution before calculating the
            // natural maximum. This avoids spacer growth feeding back into its
            // own next measurement.
            const renderedSpacerHeight = getRenderedSpacerHeight(spacer);
            const naturalMaximum = Math.max(
                0,
                viewport.scrollHeight - renderedSpacerHeight - viewport.clientHeight,
            );
            setSpacerHeight(spacer, Math.max(0, targetScrollTop - naturalMaximum));

            // Scroll anchoring can adjust scrollTop synchronously when spacer
            // geometry changes. Re-read the rectangles so the coordinate is
            // correct even in that case.
            const adjustedViewportRect = viewport.getBoundingClientRect();
            const adjustedMessageRect = messageArea.getBoundingClientRect();
            targetScrollTop = viewport.scrollTop
                + adjustedMessageRect.top
                - adjustedViewportRect.top
                - topGap;
            const maximum = Math.max(0, viewport.scrollHeight - viewport.clientHeight);

            return Math.min(maximum, Math.max(0, targetScrollTop));
        }

        function cleanupAlignment(alignment) {
            if (alignment.frameId !== null) {
                cancelFrame?.(alignment.frameId);
                alignment.frameId = null;
            }
            if (alignment.guardTimer !== null) {
                cancelTimeout(alignment.guardTimer);
                alignment.guardTimer = null;
            }
            alignment.resizeObserver?.disconnect?.();
            alignment.mutationObserver?.disconnect?.();
            alignment.cleanupCallbacks.forEach((cleanup) => {
                try {
                    cleanup();
                } catch (_error) {
                    // Cleanup is best-effort and must never break chat input.
                }
            });
            alignment.cleanupCallbacks.length = 0;
            alignment.viewport.classList?.remove('chat-scroll-aligning');
        }

        function finishAlignment(state, alignment) {
            if (state.activeAlignment !== alignment) {
                return;
            }
            cleanupAlignment(alignment);
            state.activeAlignment = null;
        }

        function isAlignmentCurrent(state, alignment) {
            return state.activeAlignment === alignment
                && state.sequence === alignment.sequence;
        }

        function scheduleCorrection(state, alignment) {
            if (!isAlignmentCurrent(state, alignment) || alignment.frameId !== null) {
                return;
            }
            alignment.frameId = requestFrame((timestamp) => {
                runAlignmentFrame(state, alignment, timestamp);
            });
        }

        function scheduleGuardTick(state, alignment) {
            if (!isAlignmentCurrent(state, alignment) || alignment.guardTimer !== null) {
                return;
            }
            alignment.guardTimer = scheduleTimeout(() => {
                alignment.guardTimer = null;
                scheduleCorrection(state, alignment);
            }, ALIGNMENT_GUARD_INTERVAL_MS);
        }

        function runAlignmentFrame(state, alignment, timestamp) {
            alignment.frameId = null;
            if (!isAlignmentCurrent(state, alignment)) {
                return;
            }

            const targetScrollTop = measureAlignment(alignment);
            if (targetScrollTop === null) {
                finishAlignment(state, alignment);
                return;
            }

            const frameTime = Number.isFinite(timestamp) ? timestamp : now();
            if (!alignment.initialAnimationComplete) {
                if (alignment.startedAt === null) {
                    alignment.startedAt = frameTime;
                    alignment.startScrollTop = alignment.viewport.scrollTop;
                }
                const elapsed = Math.max(0, frameTime - alignment.startedAt);
                const progress = alignment.duration === 0
                    ? 1
                    : Math.min(1, elapsed / alignment.duration);
                const nextScrollTop = alignment.startScrollTop
                    + ((targetScrollTop - alignment.startScrollTop) * easeOutCubic(progress));
                writeScrollTop(alignment.viewport, nextScrollTop);

                if (progress < 1) {
                    scheduleCorrection(state, alignment);
                    return;
                }
                alignment.initialAnimationComplete = true;
                alignment.guardDeadline = frameTime + ALIGNMENT_GUARD_DURATION_MS;
            }

            // Finish each correction at the exact live target. The tolerance
            // avoids sub-pixel scroll churn on fractional zoom levels.
            const exactTarget = measureAlignment(alignment);
            if (exactTarget === null) {
                finishAlignment(state, alignment);
                return;
            }
            if (Math.abs(alignment.viewport.scrollTop - exactTarget) > ALIGNMENT_TOLERANCE_PX) {
                writeScrollTop(alignment.viewport, exactTarget);
            }

            if (frameTime >= alignment.guardDeadline) {
                finishAlignment(state, alignment);
                return;
            }
            scheduleGuardTick(state, alignment);
        }

        function observeAlignmentLayout(state, alignment) {
            const ResizeObserverCtor = runtime?.ResizeObserver;
            if (typeof ResizeObserverCtor === 'function') {
                alignment.resizeObserver = new ResizeObserverCtor(() => {
                    scheduleCorrection(state, alignment);
                });
                alignment.resizeObserver.observe(alignment.viewport);
                alignment.resizeObserver.observe(alignment.container);
                Array.from(alignment.container.children || []).forEach((child) => {
                    if (child !== alignment.spacer) {
                        alignment.resizeObserver.observe(child);
                    }
                });
            }

            const MutationObserverCtor = runtime?.MutationObserver;
            if (typeof MutationObserverCtor === 'function') {
                alignment.mutationObserver = new MutationObserverCtor((records = []) => {
                    // Ignore the coordinator's own spacer style mutation. It
                    // has already been accounted for by measureAlignment and
                    // otherwise creates a needless observer/frame feedback loop.
                    const hasExternalMutation = records.length === 0
                        || records.some((record) => record.target !== alignment.spacer);
                    if (!hasExternalMutation) {
                        return;
                    }
                    // Newly inserted direct children also need resize
                    // observation so later image/widget growth is detected.
                    Array.from(alignment.container.children || []).forEach((child) => {
                        if (child !== alignment.spacer) {
                            alignment.resizeObserver?.observe?.(child);
                        }
                    });
                    scheduleCorrection(state, alignment);
                });
                alignment.mutationObserver.observe(alignment.container, {
                    attributes: true,
                    childList: true,
                    subtree: true,
                });
            }

            const visualViewport = runtime?.visualViewport;
            if (visualViewport?.addEventListener) {
                const handleVisualViewportResize = () => scheduleCorrection(state, alignment);
                visualViewport.addEventListener('resize', handleVisualViewportResize, { passive: true });
                alignment.cleanupCallbacks.push(() => {
                    visualViewport.removeEventListener('resize', handleVisualViewportResize);
                });
            }

            // Images can finish after their surrounding message was measured.
            // Listen directly as a fallback for browsers without ResizeObserver.
            Array.from(alignment.container.querySelectorAll?.('img') || [])
                .filter((image) => !image.complete)
                .forEach((image) => {
                    const handleImageSettled = () => scheduleCorrection(state, alignment);
                    image.addEventListener('load', handleImageSettled, { once: true });
                    image.addEventListener('error', handleImageSettled, { once: true });
                    alignment.cleanupCallbacks.push(() => {
                        image.removeEventListener('load', handleImageSettled);
                        image.removeEventListener('error', handleImageSettled);
                    });
                });

            const fontsReady = documentRef?.fonts?.ready;
            if (fontsReady && typeof fontsReady.then === 'function') {
                fontsReady.then(() => {
                    scheduleCorrection(state, alignment);
                }).catch?.(() => {});
            }
        }

        /**
         * Align a newly-sent user message under the top edge of its viewport.
         *
         * @param {string} messageId User message identifier without the "u-" prefix.
         * @param {object} options Optional viewport, container, and topGap.
         * @returns {boolean} Whether an alignment transaction was started.
         */
        function alignUserMessage(messageId, options = {}) {
            const targets = resolveTargets(options);
            if (!targets || !messageId) {
                return false;
            }

            const { viewport, container } = targets;
            bindViewport(viewport, container);
            const userMessageContent = findUserMessageContent(container, messageId);
            const messageArea = userMessageContent?.closest?.('.user-message-area');
            if (!messageArea) {
                return false;
            }

            const state = getState(viewport, container);
            cancel(viewport, { preserveSpacer: true });
            const spacer = ensureSpacer(container);
            if (!spacer) {
                return false;
            }

            state.sequence += 1;
            const alignment = {
                cleanupCallbacks: [],
                container,
                duration: shouldReduceMotion() ? 0 : DEFAULT_ANIMATION_DURATION_MS,
                frameId: null,
                guardDeadline: Number.POSITIVE_INFINITY,
                guardTimer: null,
                initialAnimationComplete: false,
                messageArea,
                messageId: String(messageId),
                mutationObserver: null,
                resizeObserver: null,
                sequence: state.sequence,
                spacer,
                startedAt: null,
                startScrollTop: viewport.scrollTop,
                topGap: Math.max(0, Number(options.topGap) || 0),
                viewport,
            };
            state.activeAlignment = alignment;
            viewport.classList?.add('chat-scroll-aligning');
            observeAlignmentLayout(state, alignment);
            scheduleCorrection(state, alignment);
            return true;
        }

        /**
         * Cancel the viewport's active alignment. By default the spacer stays in
         * place so cancellation cannot clamp scrollTop and cause a visible jump.
         */
        function cancel(viewport, options = {}) {
            if (!viewport) {
                if (options.removeSpacer) {
                    removeSpacer(options.container);
                }
                return false;
            }
            const state = viewportStates.get(viewport);
            const alignment = state?.activeAlignment;
            if (
                alignment
                && options.messageId
                && String(options.messageId) !== alignment.messageId
            ) {
                return false;
            }
            if (alignment) {
                cleanupAlignment(alignment);
                state.activeAlignment = null;
                state.sequence += 1;
            }

            if (options.removeSpacer) {
                const container = options.container || state?.container;
                removeSpacer(container);
            } else if (options.preserveSpacer === false) {
                const container = options.container || state?.container;
                setSpacerHeight(getSpacer(container), 0);
            }
            return Boolean(alignment);
        }

        function hasRecentUserIntent(state) {
            return state.pointerActive
                || (now() - state.lastUserIntentAt) <= USER_SCROLL_INTENT_GRACE_MS;
        }

        /**
         * Shrink the spacer only when removal cannot clamp the current scroll
         * position. This preserves the user's exact position while eliminating
         * obsolete blank space once they have navigated into natural content.
         */
        function reconcileSpacerAfterUserScroll(viewport, container) {
            const state = getState(viewport, container);
            state.spacerUpdateFrame = null;
            if (state.activeAlignment || !hasRecentUserIntent(state)) {
                return;
            }

            const spacer = getSpacer(container);
            const spacerHeight = getRenderedSpacerHeight(spacer);
            if (!spacer || spacerHeight <= 0) {
                return;
            }
            const naturalMaximum = Math.max(
                0,
                viewport.scrollHeight - spacerHeight - viewport.clientHeight,
            );
            if (viewport.scrollTop <= naturalMaximum + ALIGNMENT_TOLERANCE_PX) {
                setSpacerHeight(spacer, 0);
            }
        }

        function handleViewportScroll(viewport, container = null) {
            if (!viewport) {
                return;
            }
            const state = getState(viewport, container);
            if (
                state.activeAlignment
                || !state.container
                || !hasRecentUserIntent(state)
                || state.spacerUpdateFrame !== null
            ) {
                return;
            }
            state.spacerUpdateFrame = requestFrame(() => {
                reconcileSpacerAfterUserScroll(viewport, state.container);
            });
        }

        function markUserScrollIntent(viewport, container = null, { pointerActive = null } = {}) {
            const state = getState(viewport, container);
            state.lastUserIntentAt = now();
            if (pointerActive !== null) {
                state.pointerActive = Boolean(pointerActive);
            }
            cancel(viewport, { preserveSpacer: true });
        }

        /**
         * Install user-intent listeners once for a viewport. Scroll events alone
         * are insufficient because browsers emit them for native smooth scroll,
         * scroll anchoring, focus changes, and direct scrollTop writes.
         */
        function bindViewport(viewport, container) {
            if (!viewport || !container) {
                return false;
            }
            const state = getState(viewport, container);
            if (state.bound) {
                return true;
            }

            viewport.addEventListener('scroll', () => {
                handleViewportScroll(viewport, state.container);
            }, { passive: true });
            viewport.addEventListener('wheel', () => {
                markUserScrollIntent(viewport, state.container);
            }, { passive: true });
            viewport.addEventListener('touchstart', () => {
                markUserScrollIntent(viewport, state.container, { pointerActive: true });
            }, { passive: true });
            viewport.addEventListener('touchend', () => {
                markUserScrollIntent(viewport, state.container, { pointerActive: false });
            }, { passive: true });
            viewport.addEventListener('touchcancel', () => {
                markUserScrollIntent(viewport, state.container, { pointerActive: false });
            }, { passive: true });
            viewport.addEventListener('pointerdown', () => {
                markUserScrollIntent(viewport, state.container, { pointerActive: true });
            }, { passive: true });
            viewport.addEventListener('keydown', (event) => {
                if (SCROLL_KEYS.has(event.key)) {
                    markUserScrollIntent(viewport, state.container);
                }
            });

            const releasePointer = () => {
                if (state.pointerActive) {
                    state.pointerActive = false;
                    state.lastUserIntentAt = now();
                }
            };
            runtime?.addEventListener?.('pointerup', releasePointer, { passive: true });
            runtime?.addEventListener?.('pointercancel', releasePointer, { passive: true });
            state.bound = true;
            return true;
        }

        /**
         * Scroll to the natural transcript bottom. The dynamic spacer is removed
         * first so "bottom" never means the end of temporary alignment space.
         */
        function scrollToBottom(viewport, container, { behavior = 'auto' } = {}) {
            if (!viewport) {
                return false;
            }
            const state = getState(viewport, container);
            cancel(viewport, {
                container: container || state.container,
                removeSpacer: true,
            });
            const requestedBehavior = behavior === 'smooth' && !shouldReduceMotion()
                ? 'smooth'
                : 'instant';
            const bottom = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
            if (typeof viewport.scrollTo === 'function') {
                try {
                    viewport.scrollTo({ top: bottom, behavior: requestedBehavior });
                    return true;
                } catch (_error) {
                    // Fall through to direct assignment for older browsers.
                }
            }
            viewport.scrollTop = bottom;
            return true;
        }

        /**
         * Cancel all activity and remove temporary geometry for a viewport.
         */
        function reset(viewport, container) {
            if (!viewport && !container) {
                return;
            }
            if (viewport) {
                const state = getState(viewport, container);
                if (state.spacerUpdateFrame !== null) {
                    cancelFrame?.(state.spacerUpdateFrame);
                    state.spacerUpdateFrame = null;
                }
                cancel(viewport, { container: container || state.container, removeSpacer: true });
                state.lastUserIntentAt = Number.NEGATIVE_INFINITY;
                state.pointerActive = false;
                return;
            }
            removeSpacer(container);
        }

        function isAligning(viewport) {
            return Boolean(viewportStates.get(viewport)?.activeAlignment);
        }

        return {
            alignUserMessage,
            bindViewport,
            cancel,
            handleViewportScroll,
            isAligning,
            markUserScrollIntent,
            reset,
            scrollToBottom,
        };
    }

    const coordinator = createChatScrollCoordinator(globalObject);
    if (globalObject) {
        globalObject.ChatScrollCoordinator = coordinator;
    }
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            createChatScrollCoordinator,
        };
    }
}(typeof window !== 'undefined' ? window : globalThis));
