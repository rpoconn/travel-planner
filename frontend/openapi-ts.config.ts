import { defineConfig } from '@hey-api/openapi-ts'

// Generates the typed API client into src/api from the FastAPI spec.
// `npm run gen` exports a fresh spec from the backend first, then runs this.
export default defineConfig({
    input: '../backend/openapi.json',
    output: 'src/api',
    plugins: [
        '@hey-api/client-fetch',
        '@hey-api/typescript',
        '@hey-api/sdk',
        // queryOptions / mutation helpers (e.g. listPlansOptions, createPlanMutation).
        '@tanstack/react-query',
    ],
})
