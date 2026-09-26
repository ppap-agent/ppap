import {plugin as shadcn} from '@shadcn/lint'
import tsParser from '@typescript-eslint/parser'
import {defineConfig} from 'eslint/config'

export default defineConfig([
  {
    ignores: ['dist/**', 'node_modules/**', '.pnpm-store/**', 'src/components/ui/**', 'e2e/**', 'playwright.config.ts', 'playwright-report/**'],
  },
  {
    files: ['src/**/*.{js,jsx,ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {ecmaFeatures: {jsx: true}, sourceType: 'module'},
    },
    plugins: {shadcn},
    rules: {
      'shadcn/no-arbitrary-values': ['error', {allow: ['layout']}],
      'shadcn/no-inline-styles': 'error',
      'shadcn/no-raw-colors': 'error',
      'shadcn/require-static-classes': 'error',
      'shadcn/no-unknown-classes': 'error',
      'shadcn/no-restyle': ['error', {allow: ['layout']}],
    },
  },
])
