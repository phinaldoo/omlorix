// Default English fallback messages
const DEFAULT_CHAT_WELCOME_MESSAGES = [
  "How can I assist you today?",
  "What would you like to work on?",
  "How may I support your goals today?",
  "What’s on your mind?",
  "How can I make things easier for you?",
  "What would you like to accomplish right now?",
  "How can I help move your work forward?",
  "What can I clarify or create for you today?",
  "What would you like to explore?",
  "How may I contribute to your success today?",
  "What do you need help with at the moment?",
  "Where would you like to start?",
  "What’s your priority right now?",
  "How can I provide value to you today?",
  "What would you like to achieve?",
  "What’s the next step I can assist with?",
  "How can I make progress easier for you?",
  "What challenge can I help you solve?",
  "What task would you like to focus on?",
  "How may I serve you best today?",
  "Is there a project you'd like to tackle together?",
  "Which blockers can I help remove right now?",
  "What decision would you like a second opinion on?",
  "How can we break down your goals into next actions?",
  "Which part of your roadmap needs attention today?",
  "What insight or resource are you looking for?",
  "How can I accelerate your momentum?",
  "What would make today’s work feel complete?",
  "Where can I provide clarity or structure?",
  "Which idea should we explore deeper together?",
];

let lastWelcomeMessage = null;

function getWelcomeMessages() {
  if (typeof window.getTranslation === "function") {
    const translatedMessages = window.getTranslation("chat_welcome_messages", null);
    if (Array.isArray(translatedMessages) && translatedMessages.length > 0) {
      return translatedMessages;
    }
  }
  return DEFAULT_CHAT_WELCOME_MESSAGES;
}

function welcomeTranslation(key, fallback) {
  return typeof window.getTranslation === "function"
    ? window.getTranslation(key, fallback)
    : fallback;
}

async function persistWelcomeCardDismissal() {
  const response = await window.authedFetch("/api/v1/users/welcome-card/dismiss", {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`Welcome card dismissal failed with status ${response.status}`);
  }
}

// The modal keeps its lifecycle outside the chat container so starting a chat,
// switching workspaces, or changing responsive layouts cannot hide it midway
// through the required first-run choice.
const firstRunWelcomeModalState = {
  bodyOverflow: "",
  cancelPendingClose: null,
  closeTimer: 0,
  dismissalPending: false,
  elements: null,
  escapeRegistration: null,
  initialized: false,
  open: false,
  pendingClosePromise: null,
  returnFocus: null,
  waitingForPrivacyNotice: false,
};

/** Return whether the mandatory privacy notice must resolve before welcome opens. */
function hasPendingPrivacyPolicyNotice(chatSetup = {}) {
  const policy = chatSetup?.privacy_policy_notice;
  if (!policy || !policy.should_show_notice) return false;
  return String(policy.notice_mode || "none") !== "none" && Number(policy.revision || 0) > 0;
}

/** Resume welcome initialization after the higher-priority privacy modal closes. */
function waitForPrivacyPolicyNotice(chatSetup) {
  if (firstRunWelcomeModalState.waitingForPrivacyNotice) return;

  firstRunWelcomeModalState.waitingForPrivacyNotice = true;
  window.addEventListener?.("privacyPolicyNoticeResolved", () => {
    firstRunWelcomeModalState.waitingForPrivacyNotice = false;
    // privacyPolicyNotice mutates the shared setup object before emitting this
    // event. Prefer that authoritative object in case bootstrap state changed.
    initFirstRunWelcomeCard(window.chatSetup || chatSetup);
  }, { once: true });
}

/** Return whether the operating system has requested reduced motion. */
function shouldReduceWelcomeModalMotion() {
  return typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Find the controls that should participate in the modal's Tab loop. */
function getWelcomeModalFocusableElements() {
  const modal = firstRunWelcomeModalState.elements?.modal;
  if (!modal || typeof modal.querySelectorAll !== "function") return [];
  return Array.from(modal.querySelectorAll(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )).filter((element) => !element.hidden && element.getAttribute?.("aria-hidden") !== "true");
}

/** Keep keyboard focus inside the active aria-modal dialog. */
function handleWelcomeModalKeydown(event) {
  if (!firstRunWelcomeModalState.open) return;

  // The shared Escape manager normally owns Escape. This local fallback keeps
  // the modal dismissible in reduced test or embedded hosts without it.
  if (event.key === "Escape") {
    event.preventDefault();
    void dismissFirstRunWelcomeModal();
    return;
  }
  if (event.key !== "Tab") return;

  const focusable = getWelcomeModalFocusableElements();
  if (!focusable.length) {
    event.preventDefault();
    firstRunWelcomeModalState.elements?.modal?.focus?.();
    return;
  }

  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/** Reveal the welcome modal and move focus into its primary action. */
function openFirstRunWelcomeModal() {
  const { overlay, modal, reviewButton } = firstRunWelcomeModalState.elements || {};
  if (!overlay || !modal || !reviewButton) return;

  // Reopening is allowed while the exit animation is running. Cancel its
  // listener and timer first so neither can hide this newly opened modal.
  const wasClosing = typeof firstRunWelcomeModalState.cancelPendingClose === "function";
  firstRunWelcomeModalState.cancelPendingClose?.();
  overlay.classList.remove("is-closing");

  if (!firstRunWelcomeModalState.open && !wasClosing) {
    firstRunWelcomeModalState.returnFocus = document.activeElement || null;
    firstRunWelcomeModalState.bodyOverflow = document.body?.style?.overflow || "";
  }
  firstRunWelcomeModalState.open = true;
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  if (document.body?.style) {
    document.body.style.overflow = "hidden";
  }

  // initChatSetup dispatches chatSetupReady immediately after this function.
  // Deferring focus prevents the chat composer listener from stealing focus
  // back into the page behind the modal during that same dispatch.
  const scheduleFocus = typeof window.requestAnimationFrame === "function"
    ? window.requestAnimationFrame.bind(window)
    : (callback) => window.setTimeout?.(callback, 0);
  scheduleFocus(() => {
    if (firstRunWelcomeModalState.open) {
      reviewButton.focus?.();
    }
  });
}

/** Finish hiding the shell after its shared exit motion and restore focus. */
function closeFirstRunWelcomeModal({ restoreFocus = true } = {}) {
  const { overlay, modal } = firstRunWelcomeModalState.elements || {};
  if (firstRunWelcomeModalState.pendingClosePromise) {
    return firstRunWelcomeModalState.pendingClosePromise;
  }
  if (!overlay || !modal || (!firstRunWelcomeModalState.open && overlay.hidden)) {
    return Promise.resolve(false);
  }

  firstRunWelcomeModalState.open = false;
  overlay.setAttribute("aria-hidden", "true");
  overlay.classList.add("is-closing");
  if (document.body?.style) {
    document.body.style.overflow = firstRunWelcomeModalState.bodyOverflow;
  }

  let handleAnimationEnd = null;
  let resolveClose = null;
  let settled = false;
  const closePromise = new Promise((resolve) => {
    resolveClose = resolve;
  });
  firstRunWelcomeModalState.pendingClosePromise = closePromise;

  /** Remove every asynchronous close hook exactly once. */
  const teardown = () => {
    window.clearTimeout?.(firstRunWelcomeModalState.closeTimer);
    firstRunWelcomeModalState.closeTimer = 0;
    firstRunWelcomeModalState.cancelPendingClose = null;
    firstRunWelcomeModalState.pendingClosePromise = null;
    if (handleAnimationEnd) {
      modal.removeEventListener?.("animationend", handleAnimationEnd);
      handleAnimationEnd = null;
    }
  };

  /** Settle without hiding when another initialization reopens the modal. */
  const cancelClose = () => {
    if (settled) return;
    settled = true;
    teardown();
    resolveClose(false);
  };

  const finishClose = () => {
    if (settled) return;
    if (firstRunWelcomeModalState.open) {
      cancelClose();
      return;
    }
    settled = true;
    teardown();
    overlay.classList.remove("is-closing");
    overlay.hidden = true;

    if (restoreFocus) {
      const returnTarget = firstRunWelcomeModalState.returnFocus;
      const fallbackTarget = document.getElementById("chatBoxInput");
      if (returnTarget?.focus && returnTarget.isConnected !== false && returnTarget !== document.body) {
        returnTarget.focus();
      } else {
        fallbackTarget?.focus?.();
      }
    }
    firstRunWelcomeModalState.returnFocus = null;
    resolveClose(true);
  };

  firstRunWelcomeModalState.cancelPendingClose = cancelClose;
  if (shouldReduceWelcomeModalMotion()) {
    finishClose();
    return closePromise;
  }

  handleAnimationEnd = (event) => {
    if (event.target !== modal) return;
    finishClose();
  };
  modal.addEventListener?.("animationend", handleAnimationEnd);
  const closeTimer = window.setTimeout(finishClose, 240);
  if (!settled) {
    firstRunWelcomeModalState.closeTimer = closeTimer;
  }
  return closePromise;
}

/** Persist the first-run choice before allowing the modal to disappear. */
async function dismissFirstRunWelcomeModal({ reviewPrivacy = false } = {}) {
  if (firstRunWelcomeModalState.dismissalPending) return false;

  const { modal, closeButton, reviewButton, dismissButton } = firstRunWelcomeModalState.elements || {};
  if (!modal || !closeButton || !reviewButton || !dismissButton) return false;

  firstRunWelcomeModalState.dismissalPending = true;
  modal.setAttribute("aria-busy", "true");
  closeButton.disabled = true;
  reviewButton.disabled = true;
  dismissButton.disabled = true;
  let dismissed = false;
  try {
    await persistWelcomeCardDismissal();
    if (window.chatSetup) {
      window.chatSetup.show_welcome_card = false;
    }
    // Restore a real page control before opening settings. Otherwise settings
    // records the now-hidden review button as its own focus-return target.
    dismissed = await closeFirstRunWelcomeModal({ restoreFocus: true });
    if (!dismissed) return false;
  } catch (error) {
    console.error("Unable to dismiss first-run welcome modal:", error);
    if (typeof window.notifyError === "function") {
      window.notifyError(welcomeTranslation(
        "welcome_card_dismiss_failed",
        "Could not dismiss the welcome card. Please try again.",
      ));
    }
    return false;
  } finally {
    firstRunWelcomeModalState.dismissalPending = false;
    modal.removeAttribute?.("aria-busy");
    closeButton.disabled = false;
    reviewButton.disabled = false;
    dismissButton.disabled = false;
  }

  // Opening settings is a separate navigation step. A settings load failure
  // must not incorrectly report that the already-persisted dismissal failed.
  if (dismissed && reviewPrivacy && typeof window.openUserSettings === "function") {
    try {
      await window.openUserSettings("security");
    } catch (error) {
      console.error("Unable to open security settings from the welcome modal:", error);
    }
  }
  return dismissed;
}

/** Bind the first-run modal once and synchronize it with chat setup state. */
function initFirstRunWelcomeCard(chatSetup = {}) {
  const overlay = document.getElementById("firstRunWelcomeOverlay");
  const modal = document.getElementById("firstRunWelcomeModal");
  const privacyStatus = document.getElementById("firstRunWelcomePrivacy");
  const closeButton = document.getElementById("firstRunWelcomeCloseButton");
  const reviewButton = document.getElementById("welcomeReviewPrivacyBtn");
  const dismissButton = document.getElementById("welcomeDismissBtn");
  if (!overlay || !modal || !privacyStatus || !closeButton || !reviewButton || !dismissButton) {
    return;
  }

  firstRunWelcomeModalState.elements = {
    overlay,
    modal,
    privacyStatus,
    closeButton,
    reviewButton,
    dismissButton,
  };

  if (!firstRunWelcomeModalState.initialized) {
    closeButton.addEventListener("click", () => {
      void dismissFirstRunWelcomeModal();
    });
    dismissButton.addEventListener("click", () => {
      void dismissFirstRunWelcomeModal();
    });
    reviewButton.addEventListener("click", () => {
      void dismissFirstRunWelcomeModal({ reviewPrivacy: true });
    });
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        void dismissFirstRunWelcomeModal();
      }
    });
    modal.addEventListener("keydown", handleWelcomeModalKeydown);

    if (typeof window.registerEscapeHandler === "function") {
      firstRunWelcomeModalState.escapeRegistration = window.registerEscapeHandler({
        id: "first-run-welcome-modal",
        priority: 185,
        isActive: () => firstRunWelcomeModalState.open,
        close: () => { void dismissFirstRunWelcomeModal(); },
      });
    }
    firstRunWelcomeModalState.initialized = true;
  }

  if (!chatSetup.show_welcome_card) {
    // A policy refresh can hide the card while its exit animation is pending.
    // Resolve that close before forcing the shell into its hidden state.
    firstRunWelcomeModalState.cancelPendingClose?.();
    overlay.classList.remove("is-closing");
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    if (firstRunWelcomeModalState.open && document.body?.style) {
      document.body.style.overflow = firstRunWelcomeModalState.bodyOverflow;
    }
    firstRunWelcomeModalState.open = false;
    return;
  }

  // The privacy-policy notice has a higher interaction priority. Waiting for
  // its resolved event guarantees that only one aria-modal dialog is active
  // and prevents the welcome modal's deferred focus from moving behind it.
  if (hasPendingPrivacyPolicyNotice(chatSetup)) {
    waitForPrivacyPolicyNotice(chatSetup);
    return;
  }

  const privacyTranslationKey = chatSetup.personal_info_access_enabled
    ? "welcome_card_ai_access_on"
    : "welcome_card_ai_access_off";
  const privacyFallback = chatSetup.personal_info_access_enabled
    ? "AI access to selected personal profile fields is currently on."
    : "AI access to selected personal profile fields is off by default.";
  privacyStatus.dataset.i18n = privacyTranslationKey;
  privacyStatus.textContent = welcomeTranslation(privacyTranslationKey, privacyFallback);
  openFirstRunWelcomeModal();
}

function initWelcomeMessage(chatSetup) {
  const welcomeContainer = document.getElementById("chatContainerWelcome");
  if (!welcomeContainer) {
    return;
  }
  const textTarget = welcomeContainer.querySelector("p") || welcomeContainer;
  const messages = getWelcomeMessages();
  let nextMessage = messages[Math.floor(Math.random() * messages.length)];

  if (messages.length > 1) {
    let safeguard = messages.length;
    while (nextMessage === lastWelcomeMessage && safeguard > 0) {
      nextMessage = messages[Math.floor(Math.random() * messages.length)];
      safeguard -= 1;
    }
  }

  textTarget.textContent = nextMessage;
  lastWelcomeMessage = nextMessage;
  if (chatSetup && typeof chatSetup === "object") {
    initFirstRunWelcomeCard(chatSetup);
  }
}
