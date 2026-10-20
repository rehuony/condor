import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
  },
  {
    files: ['src/{lib,hooks}/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [{
          group: ['@/components/**', '@/pages/**', '**/components/**', '**/pages/**'],
          message: 'Shared data and hooks must not depend on UI modules. Move shared contracts to their data or hook owner.',
        }],
      }],
    },
  },
  {
    files: ['src/lib/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [{
          group: ['@/components/**', '@/pages/**', '@/hooks/**', '**/components/**', '**/pages/**', '**/hooks/**'],
          message: 'Shared data modules must not depend on UI or hook modules. Keep shared contracts in the data layer.',
        }],
      }],
    },
  },
  {
    files: [
      'src/**/*.{test,spec}.{ts,tsx}',
      'src/**/{test,tests,__tests__}/**/*.{ts,tsx}',
    ],
    ignores: ['src/tests/**'],
    rules: {
      'no-restricted-syntax': ['error', {
        selector: 'Program',
        message: 'Place frontend tests under src/tests, mirroring source paths; shared test helpers belong in src/tests/support.',
      }],
    },
  },
])
