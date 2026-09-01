(function launcherIcons() {
  'use strict';

  /** Replace a secret reveal button's contents with the shared eye icon. */
  function setSecretRevealIcon(button, revealed) {
    if (!button) return;
    button.replaceChildren(Icons.createSvgElement(revealed ? Icons.eyeOff : Icons.eye));
  }

  window.OmlorixLauncherIcons = Object.freeze({ setSecretRevealIcon });
})();
