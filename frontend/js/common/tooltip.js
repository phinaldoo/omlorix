// Detect touch-first / coarse pointer devices (e.g. mobile, tablets).
const isCoarsePointerDevice = () => {
    try {
        return window.matchMedia('(hover: none), (pointer: coarse)').matches;
    } catch (e) {
        return ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);
    }
};

// Track the currently open touch-tooltip so only one is open at a time.
let __activeTouchTooltipHide = null;

// Position fixed tooltip near the hovered icon and support dynamic tooltips.
function setupTooltip(container) {
    if (!container || container.dataset.tooltipInitialized === 'true') return;

    const trigger = container.querySelector(':scope > .tooltip-content');
    const tooltip = container.querySelector('.tooltip');
    if (!trigger || !tooltip) return;

    if (tooltip.parentElement !== document.body) {
        document.body.appendChild(tooltip);
    }

    container.dataset.tooltipInitialized = 'true';

    let hideTimeout = null;
    let touchListenerTimeout = null;
    let rafId = null;
    let isTouchOpen = false;

    const isTooltipEnabled = () => container.dataset.tooltipEnabled !== 'false';

    const positionTooltip = () => {
        const rect = trigger.getBoundingClientRect();
        const w = tooltip.offsetWidth;
        const h = tooltip.offsetHeight;

        const docEl = document.documentElement;
        const visualViewport = window.visualViewport;
        const viewportWidth = visualViewport?.width ?? docEl.clientWidth ?? window.innerWidth;
        const viewportHeight = visualViewport?.height ?? docEl.clientHeight ?? window.innerHeight;
        const viewportOffsetX = visualViewport?.offsetLeft ?? 0;
        const viewportOffsetY = visualViewport?.offsetTop ?? 0;

        // Exclude scrollbars via client sizes; clamp padding so mobile safe-area differences don't shove tooltips away
        const rawVScroll = window.innerWidth - (docEl?.clientWidth ?? viewportWidth);
        const rawHScroll = window.innerHeight - (docEl?.clientHeight ?? viewportHeight);
        const vScrollPad = Math.min(24, Math.max(0, rawVScroll));
        const hScrollPad = Math.min(24, Math.max(0, rawHScroll));
        const edge = Math.max(12, vScrollPad, hScrollPad);

        // Center on the trigger, then clamp the tooltip's left edge into the visible viewport.
        const preferredLeft = rect.left + viewportOffsetX + (rect.width / 2) - (w / 2);
        const minLeft = viewportOffsetX + edge;
        const maxLeft = viewportOffsetX + viewportWidth - edge - w;
        const boundedMaxLeft = Math.max(minLeft, maxLeft);
        const left = Math.min(Math.max(preferredLeft, minLeft), boundedMaxLeft);

        // Prefer below; flip above if not enough space
        let y = rect.bottom + 10 + viewportOffsetY;

        // Vertical clamp (avoid bottom/top scrollbar area)
        const minY = viewportOffsetY + edge;
        const maxY = viewportOffsetY + viewportHeight - edge - h;
        if (y > maxY) {
            // Try above if below overflows and there's space
            const tryAbove = rect.top - h - 10;
            if (tryAbove >= minY) y = tryAbove;
            else y = maxY;
        }
        if (y < minY) y = minY;

        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${y}px`;
    };

    const stopTrackingPosition = () => {
        if (rafId) {
            cancelAnimationFrame(rafId);
            rafId = null;
        }
    };

    const startTrackingPosition = () => {
        if (rafId) return;
        const tick = () => {
            positionTooltip();
            rafId = requestAnimationFrame(tick);
        };
        rafId = requestAnimationFrame(tick);
    };

    const show = () => {
        if (!isTooltipEnabled()) {
            return;
        }
        if (hideTimeout) {
            clearTimeout(hideTimeout);
            hideTimeout = null;
        }
        // Temporarily add visible class without opacity to measure dimensions
        tooltip.classList.add('visible');
        tooltip.style.opacity = '0';
        positionTooltip();
        requestAnimationFrame(() => {
            positionTooltip();
            tooltip.style.opacity = '';
        });
        startTrackingPosition();
    };

    const hide = () => {
        if (hideTimeout) {
            clearTimeout(hideTimeout);
            hideTimeout = null;
        }
        if (touchListenerTimeout) {
            clearTimeout(touchListenerTimeout);
            touchListenerTimeout = null;
        }
        tooltip.classList.remove('visible');
        tooltip.style.opacity = '';
        tooltip.style.left = '-9999px';
        tooltip.style.top = '-9999px';
        stopTrackingPosition();
        if (isTouchOpen) {
            isTouchOpen = false;
            if (__activeTouchTooltipHide === hide) {
                __activeTouchTooltipHide = null;
            }
            document.removeEventListener('click', onDocClick, true);
            document.removeEventListener('touchstart', onDocClick, true);
        }
    };

    // Dynamic controls can change purpose or become hidden while the pointer
    // still rests on them. Give their owner a synchronous dismissal hook so a
    // visible tooltip cannot keep tracking a zero-sized trigger at (0, 0).
    container.addEventListener('omlorix:tooltip-dismiss', hide);

    const scheduleHide = () => {
        hideTimeout = setTimeout(hide, 120);
    };

    const onDocClick = (e) => {
        if (container.contains(e.target) || tooltip.contains(e.target)) return;
        hide();
    };

    const openTouchTooltip = () => {
        if (__activeTouchTooltipHide && __activeTouchTooltipHide !== hide) {
            __activeTouchTooltipHide();
        }
        if (hideTimeout) {
            clearTimeout(hideTimeout);
            hideTimeout = null;
        }
        isTouchOpen = true;
        __activeTouchTooltipHide = hide;
        show();
        // Defer adding listeners so the same tap that opened doesn't immediately close it.
        touchListenerTimeout = setTimeout(() => {
            touchListenerTimeout = null;
            if (!isTouchOpen) return;
            document.addEventListener('click', onDocClick, true);
            document.addEventListener('touchstart', onDocClick, true);
        }, 0);
    };

    trigger.addEventListener('mouseenter', () => {
        if (isCoarsePointerDevice()) return;
        show();
    });
    trigger.addEventListener('mouseleave', () => {
        if (isCoarsePointerDevice()) return;
        scheduleHide();
    });

    tooltip.addEventListener('mouseenter', () => {
        if (isCoarsePointerDevice()) return;
        if (hideTimeout) {
            clearTimeout(hideTimeout);
            hideTimeout = null;
        }
    });
    tooltip.addEventListener('mouseleave', () => {
        if (isCoarsePointerDevice()) return;
        hide();
    });

    // Touch / coarse-pointer support: tap toggles, tap outside dismisses.
    trigger.addEventListener('click', (e) => {
        if (!isCoarsePointerDevice()) return;
        if (!isTooltipEnabled()) return;
        e.preventDefault();
        e.stopPropagation();
        if (isTouchOpen) {
            hide();
        } else {
            openTouchTooltip();
        }
    });

    // Keyboard accessibility: show tooltip when trigger or its children receive focus
    container.addEventListener('focusin', () => {
        if (!isTooltipEnabled()) return;
        show();
    });

    container.addEventListener('focusout', (e) => {
        if (isTouchOpen) return;
        if (tooltip.contains(e.relatedTarget)) return;
        scheduleHide();
    });

    // In case the trigger loses focus (keyboard navigation) hide tooltip
    container.addEventListener('blur', (e) => {
        if (isTouchOpen) return; // touch users dismiss via outside-tap
        hide();
    }, true);
}

window.setupTooltip = setupTooltip;

document.querySelectorAll('.tooltip-container').forEach(setupTooltip);
