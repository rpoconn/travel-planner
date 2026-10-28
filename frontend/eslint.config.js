import js from '@eslint/js'
import prettier from 'eslint-config-prettier'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default defineConfig([
    // Generated code (SDK in src/api, TanStack route tree) is never hand-edited, so not linted.
    globalIgnores(['dist', 'src/api', 'src/routeTree.gen.ts', 'playwright-report', 'test-results']),
    {
        files: ['**/*.{ts,tsx}'],
        extends: [
            js.configs.recommended,
            tseslint.configs.strictTypeChecked,
            tseslint.configs.stylisticTypeChecked,
            reactHooks.configs.flat['recommended-latest'],
            reactRefresh.configs.vite,
            prettier,
        ],
        languageOptions: {
            ecmaVersion: 2023,
            globals: globals.browser,
            parserOptions: {
                projectService: true,
                tsconfigRootDir: import.meta.dirname,
            },
        },
    },
    {
        // shadcn primitives intentionally co-export their `cva` variant helpers.
        files: ['src/components/ui/**/*.tsx'],
        rules: { 'react-refresh/only-export-components': 'off' },
    },
    {
        // TanStack route files export a `Route` object (the router plugin handles their HMR)
        // and guard navigation with `throw redirect(...)`, which is a Response, not an Error.
        files: ['src/routes/**/*.tsx'],
        rules: {
            'react-refresh/only-export-components': 'off',
            '@typescript-eslint/only-throw-error': [
                'error',
                {
                    allow: [
                        { from: 'package', package: '@tanstack/router-core', name: 'Redirect' },
                    ],
                },
            ],
        },
    },
])
