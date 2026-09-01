// Theme initialization — must run before user interactions to avoid FOUC.
(function () {
  let mode = 'system';
  let theme = 'mono';
  try {
    mode = localStorage.getItem('mode') || 'system';
    theme = localStorage.getItem('theme') || 'mono';
  } catch (e) {}

  function apply(currentMode) {
    const final = currentMode === 'system'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : currentMode;
    document.documentElement.setAttribute('data-mode', final);
    document.documentElement.setAttribute('data-theme', theme);
    // Match Electron's native backing surface to the CSS canvas. During rapid
    // resizing the compositor can expose this surface for a frame before the
    // renderer paints the newly added area.
    window.omlorixServer?.setWindowBackground?.(final)?.catch(() => {});
    const color = final === 'dark' ? '#0a0a0a' : '#ffffff';
    document.querySelectorAll('meta[name="theme-color"]').forEach(t => t.setAttribute('content', color));
  }

  apply(mode);

  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (mode === 'system') apply('system');
    });
  }

  window.addEventListener('storage', (event) => {
    if (event.key === 'mode') {
      mode = event.newValue || 'system';
      apply(mode);
    } else if (event.key === 'theme') {
      theme = event.newValue || 'mono';
      apply(mode);
    }
  });
})();

// Section-based navigation: clicking a sidebar nav link shows the
// corresponding content section and hides all others. The active
// section is persisted in sessionStorage so refreshes stay on the
// same tab.
document.addEventListener('DOMContentLoaded', function () {
  const links = document.querySelectorAll('.sidebar-nav .nav-link');
  const sections = document.querySelectorAll('.content-section');
  const sectionTriggers = document.querySelectorAll('[data-open-section]');
  const contentPanel = document.querySelector('.app-content');
  if (!links.length || !sections.length) return;

  const validSections = new Set(Array.from(sections).map(section => section.id));

  /**
   * Reset every element that can own the launcher's vertical scroll position.
   *
   * The desktop layout scrolls `.app-content`, while the responsive layout
   * lets the document scroll. Resetting both keeps navigation predictable when
   * the window is resized as well as when a section is selected normally.
   */
  function resetContentScroll() {
    if (contentPanel) {
      contentPanel.scrollTop = 0;
    }

    if (document.scrollingElement) {
      document.scrollingElement.scrollTop = 0;
    }
  }

  /**
   * Switch the visible content section and update nav link active states.
   * @param {string} targetId - The section id to activate (e.g. 'status').
   */
  function activateSection(targetId) {
    if (!validSections.has(targetId)) return;

    // Detail pages belong to a top-level sidebar section. Keeping the parent
    // link active tells users where they are without adding another nav item.
    const targetSection = Array.from(sections).find(section => section.id === targetId);
    const navSectionId = targetSection?.dataset.parentSection || targetId;

    // Update nav links — only one active at a time
    links.forEach(link => {
      const isActive = link.dataset.section === navSectionId;
      link.classList.toggle('is-active', isActive);
      if (isActive) {
        link.setAttribute('aria-current', 'page');
      } else {
        link.removeAttribute('aria-current');
      }
    });

    // Show/hide content sections — only the target is visible
    sections.forEach(section => {
      const isActive = section.id === targetId;
      section.classList.toggle('is-active', isActive);
    });

    // Sections share a scroll container, so explicitly discard the previous
    // section's offset after the newly selected section becomes visible.
    resetContentScroll();

    // Persist the active section so page refresh keeps the same tab
    try {
      sessionStorage.setItem('launcher-active-section', targetId);
    } catch (e) {}
  }

  // Attach click handlers to each nav link
  links.forEach(link => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      const targetId = link.dataset.section;
      if (targetId) {
        activateSection(targetId);
      }
    });
  });

  // Buttons within a section may open a related detail page. Optional focus
  // targets make forward and back navigation predictable for keyboard users.
  sectionTriggers.forEach(trigger => {
    trigger.addEventListener('click', (event) => {
      event.preventDefault();
      const targetId = trigger.dataset.openSection;
      if (!validSections.has(targetId)) return;

      activateSection(targetId);
      const focusTargetId = trigger.dataset.sectionFocus;
      const focusTarget = focusTargetId ? document.getElementById(focusTargetId) : null;
      focusTarget?.focus?.();
    });
  });

  // Restore the previously active section from sessionStorage,
  // falling back to 'status' if nothing was saved.
  let savedSection = 'status';
  try {
    savedSection = sessionStorage.getItem('launcher-active-section') || 'status';
  } catch (e) {}

  // Verify the saved section actually exists in the DOM.
  if (!validSections.has(savedSection)) {
    savedSection = 'status';
  }

  activateSection(savedSection);
});
