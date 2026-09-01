(function () {
  function pullToRefreshT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
      return window.getTranslation(key, fallback);
    }
    return fallback;
  }

  const HEADER_SELECTOR = '.main-container-header';
  const INTERACTIVE_SELECTOR = [
    'button',
    'a',
    'input',
    'textarea',
    'select',
    'label',
    'summary',
    '[role="button"]',
    '[role="link"]',
    '[contenteditable="true"]',
    '[data-pull-refresh-ignore]'
  ].join(', ');
  const MAX_PULL_DISTANCE = 108;
  const TRIGGER_DISTANCE = 72;
  const DRAG_RESISTANCE = 0.5;
  const RELOAD_DELAY_MS = 120;

  function isTouchCapableDevice() {
    if (typeof window === 'undefined') {
      return false;
    }

    if ('ontouchstart' in window || navigator.maxTouchPoints > 0) {
      return true;
    }

    try {
      return window.matchMedia('(pointer: coarse)').matches;
    } catch (_) {
      return false;
    }
  }

  function isInteractiveTarget(target) {
    return target instanceof Element && Boolean(target.closest(INTERACTIVE_SELECTOR));
  }

  function isRefreshGestureBlocked(target, header) {
    // Some overlays, such as the mobile model selector, are rendered inside
    // the header for layout purposes. Their touch events still bubble through
    // the header, even though the user is scrolling the overlay rather than
    // pulling the page. The open-state check protects the whole overlay, and
    // the data attribute makes the exemption explicit for nested UI surfaces.
    if (header.classList.contains('model-select-open')) {
      return true;
    }

    return target instanceof Element
      && Boolean(target.closest('[data-pull-refresh-ignore]'));
  }

  function createIndicator(header) {
    const indicator = document.createElement('div');
    indicator.className = 'pull-refresh-indicator';
    indicator.setAttribute('aria-hidden', 'true');
    indicator.innerHTML = `
      <span class="pull-refresh-indicator-icon" aria-hidden="true">
        ${Icons.chevron}
      </span>
      <span class="pull-refresh-indicator-label" data-i18n="pull_to_refresh_pull">Pull to refresh</span>
    `;
    header.classList.add('pull-refresh-host');
    header.appendChild(indicator);
    return {
      root: indicator,
      label: indicator.querySelector('.pull-refresh-indicator-label')
    };
  }

  function persistDraftIfNeeded() {
    const input = document.getElementById('chatBoxInput');
    const value = typeof input?.value === 'string' ? input.value : '';
    window.writeChatInputDraft(value);
  }

  function triggerRefresh(indicatorState) {
    indicatorState.root.classList.add('is-visible', 'is-refreshing');
    indicatorState.root.classList.remove('is-armed');
    indicatorState.root.style.transform = 'translate(-50%, 8px) scale(1)';
    indicatorState.label.textContent = pullToRefreshT('pull_to_refresh_refreshing', 'Refreshing...');
    persistDraftIfNeeded();

    window.setTimeout(() => {
      window.location.reload();
    }, RELOAD_DELAY_MS);
  }

  function initPullToRefresh() {
    if (!isTouchCapableDevice()) {
      return;
    }

    const header = document.querySelector(HEADER_SELECTOR);
    if (!(header instanceof HTMLElement)) {
      return;
    }

    const indicator = createIndicator(header);
    const state = {
      active: false,
      armed: false,
      refreshing: false,
      startY: 0,
      pullDistance: 0
    };

    const render = () => {
      if (state.refreshing) {
        return;
      }

      const visible = state.pullDistance > 0;
      indicator.root.classList.toggle('is-visible', visible);
      indicator.root.classList.toggle('is-armed', state.armed);
      indicator.label.textContent = state.armed
        ? pullToRefreshT('pull_to_refresh_release', 'Release to refresh')
        : pullToRefreshT('pull_to_refresh_pull', 'Pull to refresh');

      const translateY = Math.max(-18, state.pullDistance - 18);
      const scale = Math.min(1, 0.96 + (state.pullDistance / MAX_PULL_DISTANCE) * 0.04);
      indicator.root.style.transform = `translate(-50%, ${translateY}px) scale(${scale})`;
    };

    const reset = () => {
      if (!state.refreshing) {
        indicator.root.classList.remove('is-visible', 'is-armed');
        indicator.label.textContent = pullToRefreshT('pull_to_refresh_pull', 'Pull to refresh');
        indicator.root.style.transform = 'translate(-50%, -18px) scale(0.96)';
      }

      state.active = false;
      state.armed = false;
      state.pullDistance = 0;
      state.startY = 0;
    };

    header.addEventListener('touchstart', (event) => {
      if (state.refreshing) {
        return;
      }
      if (event.touches.length !== 1) {
        reset();
        return;
      }
      if (isRefreshGestureBlocked(event.target, header) || isInteractiveTarget(event.target)) {
        reset();
        return;
      }

      state.active = true;
      state.armed = false;
      state.pullDistance = 0;
      state.startY = event.touches[0].clientY;
      indicator.root.classList.remove('is-refreshing');
      render();
    }, { passive: true });

    header.addEventListener('touchmove', (event) => {
      if (!state.active || state.refreshing || event.touches.length !== 1) {
        return;
      }

      const deltaY = event.touches[0].clientY - state.startY;
      if (deltaY <= 0) {
        state.pullDistance = 0;
        state.armed = false;
        render();
        return;
      }

      event.preventDefault();
      state.pullDistance = Math.min(MAX_PULL_DISTANCE, deltaY * DRAG_RESISTANCE);
      state.armed = state.pullDistance >= TRIGGER_DISTANCE;
      render();
    }, { passive: false });

    const finishGesture = () => {
      if (!state.active || state.refreshing) {
        return;
      }

      if (state.armed) {
        state.refreshing = true;
        triggerRefresh(indicator);
        state.active = false;
        return;
      }

      reset();
    };

    header.addEventListener('touchend', finishGesture, { passive: true });
    header.addEventListener('touchcancel', () => {
      if (state.refreshing) {
        return;
      }
      reset();
    }, { passive: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPullToRefresh, { once: true });
  } else {
    initPullToRefresh();
  }
})();
