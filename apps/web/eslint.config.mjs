import {plugin as shadcn} from '@shadcn/lint'
import tsParser from '@typescript-eslint/parser'
import {defineConfig} from 'eslint/config'

export default defineConfig([
  {
    ignores: ['dist/**', 'node_modules/**', '.pnpm-store/**'],
  },
  {
    files: ['src/**/*.{js,jsx,ts,tsx}'],
    ignores: ['src/components/ui/**'],
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
      // An icon is drawn in currentColor like text, so a page may tone it and animate it (a spinner, a disclosure turn)
      // with token classes; its shape stays in src/components/ui/icon.tsx.
      'shadcn/no-restyle': ['error', {allow: ['layout'], contracts: [{pattern: '^Icon$', allow: ['layout', 'color', 'motion']}]}],
    },
  },
  // Primitives own appearance (tokens/README.md): their visual values come from tokens too. Layout and motion are not
  // visual decisions; one primitive composing another is not a page restyling it; transform carries runtime geometry.
  {
    files: ['src/components/ui/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {ecmaFeatures: {jsx: true}, sourceType: 'module'},
    },
    plugins: {shadcn},
    rules: {
      // rounded-[inherit] follows the parent's radius; it is not a design value.
      'shadcn/no-arbitrary-values': ['error', {allow: ['layout', 'motion', 'rounded-[inherit]']}],
      'shadcn/no-inline-styles': ['error', {allow: ['transform']}],
      'shadcn/no-raw-colors': 'error',
      'shadcn/require-static-classes': 'error',
      'shadcn/no-unknown-classes': 'error',
    },
  },
])
