import { client } from '@/api/client.gen'

/** Backend origin; override with VITE_API_URL (see .env.example). */
export const API_BASE_URL: string =
    (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

/** Point the generated SDK at the backend. Call once at startup, before any request. */
export function configureApiClient(): void {
    client.setConfig({ baseUrl: API_BASE_URL })
}
