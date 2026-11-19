import {plugin as shadcn} from '@shadcn/lint'
import tsParser from '@typescript-eslint/parser'
import {defineConfig} from 'eslint/config'

export default defineConfig([
  {
    ignores: ['dist/**', 'node_modules/**', '.pnpm-store/**'],
  },
  {
    files: ['src/**/*.{js,jsx,ts,tsx}', 'preview/**/*.{js,jsx,ts,tsx}'],
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
      'shadcn/no-restyle': ['error', {allow: ['layout']}],
    },
  },
  // Primitives own appearance (docs/DESIGN_SYSTEM.md): their visual values come from tokens too. Layout and motion are not
  // visual decisions; one primitive composing another is not a page restyling it; transform carries runtime geometry.
  {
    files: ['src/components/ui/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {ecmaFeatures: {jsx: true}, sourceType: 'module'},
    },
    plugins: {shadcn},
    rules: {
      // Canvas/CanvasText are system colours that keep native <option> lists readable in either theme;
      // rounded-[inherit] follows the parent's radius. Neither is a design value.
      'shadcn/no-arbitrary-values': ['error', {allow: ['layout', 'motion', 'bg-[Canvas]', 'text-[CanvasText]', 'rounded-[inherit]']}],
      'shadcn/no-inline-styles': ['error', {allow: ['transform']}],
      'shadcn/no-raw-colors': 'error',
      'shadcn/require-static-classes': 'error',
      'shadcn/no-unknown-classes': 'error',
    },
  },
])
