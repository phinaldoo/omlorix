(function () {
  const MARKER_URL = '/.cache_buster_frontend_ready';
  const STORAGE_KEY = 'omlorix:frontendBuildMarker';
  const RELOAD_PARAM = '__build';
  const DISPLAY_MODE_QUERIES = [
    '(display-mode: standalone)',
    '(display-mode: fullscreen)',
    '(display-mode: minimal-ui)'
  ];

  function isStandaloneExperience() {
    if (typeof window === 'undefined') {
      return false;
    }

    if (window.navigator && window.navigator.standalone === true) {
      return true;
    }

    return DISPLAY_MODE_QUERIES.some((query) => {
      try {
        return window.matchMedia(query).matches;
      } catch (err) {
        return false;
      }
    });
  }

  function readStoredMarker() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (err) {
      return null;
    }
  }

  function persistMarker(value) {
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch (err) {
      // Ignore storage failures (e.g., private browsing restrictions)
    }
  }

  async function fetchLatestMarker() {
    try {
      const response = await fetch(`${MARKER_URL}?_=${Date.now()}`, {
        cache: 'no-store',
      });
      if (!response.ok) {
        throw new Error('marker request failed');
      }
      const text = (await response.text()).trim();
      return text || null;
    } catch (err) {
      return null;
    }
  }

  function dropAppliedParamIfFresh(buildId) {
    try {
      const url = new URL(window.location.href);
      if (url.searchParams.get(RELOAD_PARAM) === buildId) {
        url.searchParams.delete(RELOAD_PARAM);
        window.history.replaceState(null, document.title, url.toString());
      }
    } catch (err) {
      // Ignore URL parsing issues
    }
  }

  function forceHardReload(buildId) {
    try {
      const url = new URL(window.location.href);
      url.searchParams.set(RELOAD_PARAM, buildId);
      window.location.replace(url.toString());
    } catch (err) {
      window.location.reload();
    }
  }

  async function ensureFreshAssets() {
    if (!isStandaloneExperience()) {
      return;
    }

    const latestMarker = await fetchLatestMarker();
    if (!latestMarker) {
      return;
    }

    dropAppliedParamIfFresh(latestMarker);

    const storedMarker = readStoredMarker();
    if (!storedMarker) {
      persistMarker(latestMarker);
      return;
    }

    if (storedMarker !== latestMarker) {
      persistMarker(latestMarker);
      forceHardReload(latestMarker);
    }
  }

  if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
    ensureFreshAssets();
  }
})();
