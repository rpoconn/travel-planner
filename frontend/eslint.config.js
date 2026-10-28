import js from '@eslint/js'
import prettier from 'eslint-config-prettier'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default defineConfig([
    // Generated SDK (src/api) is never hand-edited, so it is not linted.
    globalIgnores(['dist', 'src/api', 'playwright-report', 'test-results']),
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
])
