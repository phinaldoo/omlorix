// Signup Password Requirements & Live Validation with Tooltip UI
(function () {
  const reqUrl = '/api/v1/users/password/requirements';

  let passwordEl, confirmEl, requirementsEl, tooltipEl, checklistEl, infoBtn;
  let req = null;
  let tooltipVisible = false;
  const defaultSpecialCharacters = `!"#$%&'()*+,-./:;<=>?@[\\]^_\`{|}~`;

  function el(id) { return document.getElementById(id); }
  function translate(key, fallback) {
    return typeof window.getTranslation === 'function'
      ? window.getTranslation(key, fallback)
      : fallback;
  }

  function getPasswordRequirementUtils() {
    return window.passwordRequirementUtils || {};
  }

  function getPasswordRequirementIcon(name) {
    // Resolve icons when they are rendered instead of caching them during
    // module evaluation. The login page loads the shared icon bundle first,
    // while this late lookup also keeps the component resilient if script
    // loading changes in the future.
    return window.Icons?.[name] || '';
  }

  function setChecklistIcon(iconWrapper, iconName, stateClassName) {
    if (!iconWrapper) return;

    iconWrapper.innerHTML = getPasswordRequirementIcon(iconName);
    const svg = iconWrapper.querySelector('svg');
    if (!svg) return;

    // Shared icons deliberately contain only their base SVG markup. Add the
    // password-status hooks locally so the login stylesheet can size, color,
    // and animate each state without changing the icon globally.
    svg.classList.add('pw-status-icon', stateClassName);
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
  }

  function createChecklistIcon() {
    const iconWrapper = document.createElement('span');
    iconWrapper.className = 'pw-icon-wrapper';
    setChecklistIcon(iconWrapper, 'close', 'pw-cross');
    return iconWrapper;
  }

  function renderRequirementsView(requirements) {
    if (!checklistEl) return;
    const utils = getPasswordRequirementUtils();
    if (typeof utils.renderChecklist === 'function') {
      utils.renderChecklist({
        checklistEl,
        requirements,
        wrapperEl: requirementsEl,
        itemClassName: 'pw-item',
        textClassName: 'pw-text',
        createIconElement: createChecklistIcon,
        translateFn: translate,
      });
      return;
    }

    checklistEl.innerHTML = '';
    const visibleItems = typeof utils.getVisibleItems === 'function'
      ? utils.getVisibleItems(requirements, translate)
      : [];

    visibleItems.forEach(({ key, label }) => {
      const item = document.createElement('div');
      item.className = 'pw-item';
      item.dataset.key = key;
      item.appendChild(createChecklistIcon());

      const text = document.createElement('span');
      text.className = 'pw-text';
      text.textContent = label;

      item.appendChild(text);
      checklistEl.appendChild(item);
    });

    if (requirementsEl) {
      requirementsEl.style.display = visibleItems.length === 0 ? 'none' : '';
    }
  }

  function toggleInputGroupElevation(isActive) {
    if (!requirementsEl) return;
    requirementsEl.classList.toggle('pw-tooltip-active', !!isActive);
    const formGroup = requirementsEl.closest('.form-group');
    if (!formGroup) return;
    formGroup.classList.toggle('pw-tooltip-active', !!isActive);
  }

  function countChars(str) {
    const specialRaw = req?.special_characters ?? '';
    const utils = getPasswordRequirementUtils();
    if (utils && typeof utils.countChars === 'function') {
      return utils.countChars(str, specialRaw, defaultSpecialCharacters);
    }

    // ASCII-only fallback (keeps page functional if common/passwordRequirements.js is not loaded)
    let upper = 0, lower = 0, num = 0, special = 0;
    const specialCharacters = new Set(Array.from(specialRaw || defaultSpecialCharacters));
    for (const ch of str) {
      if (specialCharacters.has(ch)) special++;
      else if (/[A-Z]/.test(ch)) upper++;
      else if (/[a-z]/.test(ch)) lower++;
      else if (/[0-9]/.test(ch)) num++;
    }
    return { upper, lower, num, special, len: Array.from(str).length };
  }

  function updateChecklist() {
    if (!req || !passwordEl || !checklistEl) return false;
    const stats = countChars(passwordEl.value || '');
    let allOk = true;

    const items = checklistEl.querySelectorAll('.pw-item');
    items.forEach((item) => {
      const key = item.dataset.key;
      const iconWrapper = item.querySelector('.pw-icon-wrapper');
      let ok = false;
      switch (key) {
        case 'min_len': ok = stats.len >= (req.min_len || 0); break;
        case 'min_special': ok = stats.special >= (req.min_special || 0); break;
        case 'min_upper': ok = stats.upper >= (req.min_upper || 0); break;
        case 'min_lower': ok = stats.lower >= (req.min_lower || 0); break;
        case 'min_num': ok = stats.num >= (req.min_num || 0); break;
        default: ok = true;
      }
      if (ok) {
        item.classList.remove('pw-item-fail');
        item.classList.add('pw-item-pass');
        setChecklistIcon(iconWrapper, 'check', 'pw-check');
      } else {
        item.classList.remove('pw-item-pass');
        item.classList.add('pw-item-fail');
        setChecklistIcon(iconWrapper, 'close', 'pw-cross');
        allOk = false;
      }
    });

    // Update info icon color based on overall status
    if (infoBtn) {
      if (items.length === 0) {
        infoBtn.classList.remove('pw-all-pass', 'pw-not-met');
      } else if (allOk) {
        infoBtn.classList.add('pw-all-pass');
        infoBtn.classList.remove('pw-not-met');
      } else {
        infoBtn.classList.remove('pw-all-pass');
        infoBtn.classList.add('pw-not-met');
      }
    }

    return allOk;
  }

  function passwordsMatch() {
    if (!confirmEl || !passwordEl) return false;
    const newVal = passwordEl.value || '';
    const confirmVal = confirmEl.value || '';
    if (!confirmVal) return false;
    return newVal === confirmVal;
  }

  function updateSubmitState() {
    updateChecklist();
  }

  // Tooltip positioning & visibility
  function showTooltip() {
    if (!tooltipEl) return;
    tooltipVisible = true;
    tooltipEl.classList.add('visible');
    tooltipEl.setAttribute('aria-hidden', 'false');
    infoBtn?.setAttribute('aria-expanded', 'true');
    toggleInputGroupElevation(true);
    positionTooltip();
  }

  function hideTooltip() {
    if (!tooltipEl) return;
    tooltipVisible = false;
    tooltipEl.classList.remove('visible');
    tooltipEl.setAttribute('aria-hidden', 'true');
    infoBtn?.setAttribute('aria-expanded', 'false');
    toggleInputGroupElevation(false);
  }

  function positionTooltip() {
    if (!tooltipEl || !infoBtn) return;
    const tooltipHost = requirementsEl || tooltipEl.parentElement;
    if (!tooltipHost) return;
    
    const btnRect = infoBtn.getBoundingClientRect();
    const tooltipRect = tooltipEl.getBoundingClientRect();
    const containerRect = tooltipHost.getBoundingClientRect();
    const viewportHeight = window.innerHeight;
    
    // Reset classes
    tooltipEl.classList.remove('tooltip-left', 'tooltip-right', 'tooltip-above');
    
    // Check if tooltip fits below
    const spaceBelow = viewportHeight - btnRect.bottom;
    const spaceAbove = btnRect.top;
    
    if (spaceBelow < tooltipRect.height + 20 && spaceAbove > tooltipRect.height + 20) {
      tooltipEl.classList.add('tooltip-above');
    }

    // Align dropdown and arrow directly to the info icon
    const tooltipWidth = tooltipRect.width || 280;
    const btnCenterX = (btnRect.left - containerRect.left) + (btnRect.width / 2);
    const maxLeft = Math.max(0, containerRect.width - tooltipWidth);
    const tooltipLeft = Math.max(0, Math.min(btnCenterX - (tooltipWidth / 2), maxLeft));

    const arrowSize = 10;
    const arrowInset = 12;
    const rawArrowLeft = btnCenterX - tooltipLeft - (arrowSize / 2);
    const maxArrowLeft = Math.max(arrowInset, tooltipWidth - arrowSize - arrowInset);
    const arrowLeft = Math.max(arrowInset, Math.min(rawArrowLeft, maxArrowLeft));

    tooltipEl.style.setProperty('--pw-tooltip-left', `${Math.round(tooltipLeft)}px`);
    tooltipEl.style.setProperty('--pw-tooltip-arrow-left', `${Math.round(arrowLeft)}px`);
  }

  async function fetchRequirements() {
    try {
      const res = await fetch(reqUrl, { method: 'GET' });
      if (!res.ok) return;
      
      const data = await res.json();
      req = {
        min_len: Number(data?.min_len || 0),
        min_special: Number(data?.min_special || 0),
        min_upper: Number(data?.min_upper || 0),
        min_lower: Number(data?.min_lower || 0),
        min_num: Number(data?.min_num || 0),
        special_characters: typeof data?.special_characters === 'string' ? data.special_characters : defaultSpecialCharacters,
      };
      renderRequirementsView(req);
      updateSubmitState();
    } catch (e) {
      console.warn('Unable to load password requirements for signup', e);
    }
  }

  function bindEvents() {
    if (passwordEl) {
      passwordEl.addEventListener('input', () => updateSubmitState());
      // The requirements tooltip is intentionally controlled by the adjacent
      // info button. Opening it on input focus makes a pointer click briefly
      // show the tooltip before the outside-click handler closes it again.
      passwordEl.addEventListener('blur', () => {
        hideTooltip();
      });
    }
    
    if (confirmEl) {
      confirmEl.addEventListener('input', () => updateSubmitState());
      confirmEl.addEventListener('focus', () => {
        hideTooltip();
      });
    }

    // Info button interactions
    if (infoBtn) {
      // Click/tap toggle for mobile
      infoBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (tooltipVisible) {
          hideTooltip();
        } else {
          showTooltip();
        }
      });

      // Hover for desktop
      infoBtn.addEventListener('mouseenter', () => showTooltip());
      infoBtn.addEventListener('mouseleave', (e) => {
        // Don't hide if moving to tooltip
        const related = e.relatedTarget;
        if (tooltipEl && tooltipEl.contains(related)) return;
        hideTooltip();
      });

      // Keyboard accessibility
      infoBtn.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          if (tooltipVisible) hideTooltip();
          else showTooltip();
        } else if (e.key === 'Escape') {
          hideTooltip();
        }
      });
    }

    // Allow hovering over tooltip without it closing
    if (tooltipEl) {
      tooltipEl.addEventListener('mouseenter', () => {
        if (tooltipVisible) showTooltip();
      });
      tooltipEl.addEventListener('mouseleave', () => hideTooltip());
    }

    // Close tooltip when clicking outside
    document.addEventListener('click', (e) => {
      if (!tooltipVisible) return;
      if (infoBtn && infoBtn.contains(e.target)) return;
      if (tooltipEl && tooltipEl.contains(e.target)) return;
      hideTooltip();
    });

    // Reposition on scroll/resize
    window.addEventListener('resize', () => {
      if (tooltipVisible) positionTooltip();
    });
    window.addEventListener('scroll', () => {
      if (tooltipVisible) positionTooltip();
    }, true);

    document.addEventListener('i18n:updated', () => {
      if (!req) return;
      renderRequirementsView(req);
      updateSubmitState();
    });

    // Form submission validation
    const form = document.getElementById('registerForm');
    if (form) {
      form.addEventListener('submit', (e) => {
        const reqOk = updateChecklist();
        const matchOk = passwordsMatch();
        if (!reqOk || !matchOk) {
          e.preventDefault();
          if (!reqOk) showTooltip();
        }
      });
    }
  }

  function init() {
    passwordEl = el('signupPassword');
    confirmEl = el('confirmPassword');
    requirementsEl = el('signupPasswordRequirements');
    tooltipEl = el('pwReqTooltip');
    checklistEl = el('pwChecklist');
    infoBtn = el('pwInfoBtn');
    if (infoBtn) {
      infoBtn.setAttribute('aria-expanded', 'false');
    }

    // Expose global API
    window.signupPw = {
      checkAndDisplay(display) {
        const reqOk = updateChecklist();
        const matchOk = passwordsMatch();
        return !!reqOk && !!matchOk;
      },
      meetsRequirements() {
        return !!updateChecklist();
      }
    };

    bindEvents();
    fetchRequirements();
    updateSubmitState();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
