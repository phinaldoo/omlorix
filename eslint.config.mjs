import js from '@eslint/js';
import globals from 'globals';

const frontendProjectGlobals = {
  MarkdownIt: 'readonly',
  Prism: 'readonly',
  QRCode: 'readonly',
  mermaid: 'readonly',
};

const frontendStaticRules = {
  'getter-return': 'error',
  'no-constant-binary-expression': 'error',
  'no-dupe-args': 'error',
  'no-dupe-keys': 'error',
  'no-self-assign': 'error',
  'no-self-compare': 'error',
  'no-unreachable': 'error',
  'no-unsafe-finally': 'error',
  'valid-typeof': 'error',
};

export default [
  {
    ignores: [
      '**/*.min.js',
      'frontend/js/vendor/**',
    ],
  },
  {
    files: ['electron/**/*.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'commonjs',
      globals: {
        ...globals.node,
      },
    },
    rules: frontendStaticRules,
  },
  {
    files: ['electron/**/*.mjs'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.node,
      },
    },
    rules: frontendStaticRules,
  },
  {
    files: ['frontend/js/**/*.test.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'commonjs',
      globals: {
        ...globals.node,
      },
    },
    rules: frontendStaticRules,
  },
  {
    files: ['frontend/js/**/*.js'],
    ignores: ['frontend/js/**/*.test.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'script',
      globals: {
        ...globals.browser,
        ...frontendProjectGlobals,
      },
    },
    rules: frontendStaticRules,
  },
];
