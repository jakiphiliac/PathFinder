/**
 * ESLint configuration for the frontend (Vue 3) with Prettier integration.
 *
 * This file uses CommonJS export so it works with the standard `eslint` CLI.
 *
 * Notes:
 * - Requires: eslint, eslint-plugin-vue, eslint-config-prettier, prettier
 * - Recommended npm install (run in frontend/):
 *     npm install --save-dev eslint eslint-plugin-vue eslint-config-prettier
 *
 * Useful scripts in package.json:
 * - "lint": "eslint --ext .js,.vue src"
 * - "lint:fix": "eslint --ext .js,.vue src --fix"
 */
module.exports = {
  root: true,
  env: {
    browser: true,
    node: true,
    es2021: true
  },
  parser: 'vue-eslint-parser',
  parserOptions: {
    // Use the default JS parser for script blocks inside .vue files
    parser: 'espree',
    ecmaVersion: 2021,
    sourceType: 'module',
    ecmaFeatures: {
      jsx: false
    }
  },
  plugins: ['vue'],
  extends: [
    // Basic recommended ESLint rules
    'eslint:recommended',
    // Vue 3 recommended ruleset
    'plugin:vue/vue3-recommended',
    // Make sure this is last so Prettier formatting rules win over ESLint formatting
    'prettier'
  ],
  rules: {
    // Turn off rules that are noisy for small projects or conflict with Prettier
    'vue/html-self-closing': [
      'error',
      {
        html: {
          void: 'always',
          normal: 'never',
          component: 'always'
        },
        svg: 'always',
        math: 'always'
      }
    ],

    // Allow single-word component names in this project (Home.vue / Dashboard.vue)
    'vue/multi-word-component-names': 'off',

    // Prefer explicit emits declarations but don't fail builds if omitted
    'vue/require-explicit-emits': 'off',

    // Enforce prop types where provided, but don't be strict in early development
    'vue/require-prop-types': 'off',

    // Common JS rules
    'no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
    'no-undef': 'error',

    // Avoid accidental console logs in production
    'no-console':
      process.env.NODE_ENV === 'production' ? ['warn', { allow: ['warn', 'error'] }] : 'off',
    'no-debugger': process.env.NODE_ENV === 'production' ? 'warn' : 'off'
  },
  overrides: [
    {
      files: ['*.vue'],
      parser: 'vue-eslint-parser',
      parserOptions: {
        parser: 'espree',
        ecmaVersion: 2021,
        sourceType: 'module'
      }
    },
    {
      files: ['**/__tests__/**', '**/*.spec.js', '**/*.test.js'],
      env: {
        jest: true
      }
    }
  ],
  ignorePatterns: ['dist/', 'node_modules/']
};
