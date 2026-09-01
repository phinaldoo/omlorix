'use strict';

function createLocalizedRoleMenuItem(role, translationKey, translate, variables = {}) {
  const label = String(translate(translationKey, variables));

  return {
    role,
    label,
    accessibilityLabel: label,
  };
}

module.exports = {
  createLocalizedRoleMenuItem,
};
