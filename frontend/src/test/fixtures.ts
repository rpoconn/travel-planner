import type { PlanSummary, TokenResponse, TravelPlan, UserRead } from '@/api/types.gen'

// Typed API fixtures + a builder for the generated SDK's success result shape.

export function sdkOk<T>(data: T) {
    return {
        data,
        error: undefined,
        request: new Request('http://test'),
        response: new Response(),
    }
}

export const ADA: UserRead = {
    id: '11111111-1111-1111-1111-111111111111',
    email: 'ada@example.com',
    display_name: 'Ada',
    created_at: '2026-10-01T12:00:00Z',
}

export function tokenResponse(user: UserRead = ADA): TokenResponse {
    return { access_token: 'test-token', token_type: 'bearer', expires_in: 86400, user }
}

export function planSummary(overrides: Partial<PlanSummary> = {}): PlanSummary {
    return {
        id: 'aaaaaaaa-0000-0000-0000-000000000001',
        title: 'Tokyo in May',
        status: 'draft',
        destination: { name: 'Tokyo' },
        start_date: '2027-05-01',
        end_date: '2027-05-08',
        version: 1,
        updated_at: '2026-10-01T12:00:00Z',
        ...overrides,
    }
}

export function travelPlan(overrides: Partial<TravelPlan> = {}): TravelPlan {
    return {
        id: 'aaaaaaaa-0000-0000-0000-000000000001',
        user_id: ADA.id,
        title: 'Tokyo in May',
        status: 'draft',
        origin: null,
        destination: { name: 'Tokyo' },
        start_date: '2027-05-01',
        end_date: '2027-05-08',
        travelers: { adults: 2, children: 0 },
        budget: null,
        notes: '',
        version: 1,
        created_at: '2026-10-01T12:00:00Z',
        updated_at: '2026-10-01T12:00:00Z',
        ...overrides,
    }
}
