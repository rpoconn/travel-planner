import { client } from '@/api/client.gen'
import { clearSession, getToken } from '@/lib/auth'

/** Backend origin; override with VITE_API_URL (see .env.example). */
export const API_BASE_URL: string =
    (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

/** Point the generated SDK at the backend. Call once at startup, before any request. */
export function configureApiClient(): void {
    client.setConfig({
        baseUrl: API_BASE_URL,
        // Called for operations the spec marks as bearer-secured; public ones get no header.
        auth: () => getToken(),
    })

    // A 401 on an authenticated call means the token expired or the user is gone: drop the
    // session, and the authed layout redirects to /login. Login's own 401 is left alone.
    client.interceptors.response.use((response, request) => {
        if (response.status === 401 && request.headers.has('Authorization')) {
            clearSession()
        }
        return response
    })
}
