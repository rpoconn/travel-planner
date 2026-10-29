import type {
    ActivityItem,
    ChatMessage,
    ChatProposal,
    FlightItem,
    LodgingItem,
    PlanSummary,
    Timeline,
    TimelineEntry,
    TokenResponse,
    TransferItem,
    TravelPlan,
    UserRead,
} from '@/api/types.gen'

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
        limits: [],
        notes: '',
        version: 1,
        created_at: '2026-10-01T12:00:00Z',
        updated_at: '2026-10-01T12:00:00Z',
        items: [],
        ...overrides,
    }
}

// ----- itinerary -----
// A Tokyo trip: overnight outbound flight SFO → NRT (lands May 2, local), Narita Express into
// town, three nights at the hotel, and a temple visit on May 3.

export const PLAN_ID = 'aaaaaaaa-0000-0000-0000-000000000001'

const ITEM_DEFAULTS = {
    plan_id: PLAN_ID,
    created_by: 'assistant',
    status: 'proposed',
    end_timezone: null,
    cost: null,
    booking_ref: null,
    source_url: null,
    notes: '',
} as const

export function flightItem(overrides: Partial<FlightItem> = {}): FlightItem {
    return {
        ...ITEM_DEFAULTS,
        id: 'flight-1',
        type: 'flight',
        title: 'SFO → NRT',
        start: '2027-05-01T11:00:00-07:00',
        end: '2027-05-02T14:30:00+09:00',
        timezone: 'America/Los_Angeles',
        end_timezone: 'Asia/Tokyo',
        carrier: 'United',
        flight_number: 'UA 837',
        from_place: { name: 'San Francisco International', iata: 'SFO', city: 'San Francisco' },
        to_place: { name: 'Narita International Airport', iata: 'NRT', city: 'Tokyo' },
        cabin: 'economy',
        direction: 'outbound',
        layovers: [],
        ...overrides,
    }
}

export function lodgingItem(overrides: Partial<LodgingItem> = {}): LodgingItem {
    return {
        ...ITEM_DEFAULTS,
        id: 'lodging-1',
        type: 'lodging',
        title: 'Hotel Okura',
        start: '2027-05-02T15:00:00+09:00',
        end: '2027-05-05T11:00:00+09:00',
        timezone: 'Asia/Tokyo',
        place: { name: 'Hotel Okura', city: 'Tokyo', country: 'Japan' },
        room_type: null,
        nightly_rate: null,
        kind: 'hotel',
        ...overrides,
    }
}

export function activityItem(overrides: Partial<ActivityItem> = {}): ActivityItem {
    return {
        ...ITEM_DEFAULTS,
        id: 'activity-1',
        type: 'activity',
        title: 'Senso-ji Temple',
        start: '2027-05-03T09:00:00+09:00',
        end: '2027-05-03T11:00:00+09:00',
        timezone: 'Asia/Tokyo',
        place: { name: 'Senso-ji', city: 'Tokyo' },
        category: 'sightseeing',
        booking_required: false,
        ...overrides,
    }
}

export function transferItem(overrides: Partial<TransferItem> = {}): TransferItem {
    return {
        ...ITEM_DEFAULTS,
        id: 'transfer-1',
        type: 'transfer',
        title: 'Narita Express',
        start: '2027-05-02T15:30:00+09:00',
        end: '2027-05-02T16:30:00+09:00',
        timezone: 'Asia/Tokyo',
        mode: 'train',
        from_place: { name: 'Narita International Airport', iata: 'NRT' },
        to_place: { name: 'Tokyo Station' },
        provider: 'JR East',
        pickup_location: null,
        dropoff_location: null,
        ...overrides,
    }
}

export function timelineEntry(
    item: TimelineEntry['item'],
    overrides: Partial<Omit<TimelineEntry, 'item'>> = {},
): TimelineEntry {
    return {
        item,
        continues_from_previous_day: false,
        continues_to_next_day: false,
        ...overrides,
    }
}

export function timeline(overrides: Partial<Timeline> = {}): Timeline {
    return { plan_id: PLAN_ID, version: 1, days: [], lodging: [], warnings: [], ...overrides }
}

/** The Tokyo trip above as the server's timeline: May 1–5 with the hotel spanning 1..4. */
export function tokyoTimeline(): Timeline {
    const flight = flightItem()
    return timeline({
        days: [
            {
                index: 0,
                date: '2027-05-01',
                entries: [timelineEntry(flight, { continues_to_next_day: true })],
            },
            {
                index: 1,
                date: '2027-05-02',
                entries: [
                    timelineEntry(flight, { continues_from_previous_day: true }),
                    timelineEntry(transferItem()),
                ],
            },
            { index: 2, date: '2027-05-03', entries: [timelineEntry(activityItem())] },
            { index: 3, date: '2027-05-04', entries: [] },
            { index: 4, date: '2027-05-05', entries: [] },
        ],
        lodging: [{ item: lodgingItem(), start_day_index: 1, end_day_index: 4 }],
    })
}

// ----- chat -----

export function chatMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
    return {
        id: 'message-1',
        plan_id: PLAN_ID,
        role: 'assistant',
        kind: 'message',
        content: 'Happy to help plan Tokyo!',
        tool_calls: [],
        proposal: null,
        created_at: '2026-10-01T12:00:00Z',
        ...overrides,
    }
}

/** A pending proposal of one hotel, made by the assistant message `message-1`. */
export function chatProposal(overrides: Partial<ChatProposal> = {}): ChatProposal {
    return {
        id: 'proposal-1',
        plan_id: PLAN_ID,
        message_id: 'message-1',
        status: 'pending',
        changes: [
            {
                call_id: 'c1',
                name: 'add_lodging',
                arguments: {
                    title: 'Hotel Okura',
                    start: '2027-05-02T15:00:00+09:00',
                    end: '2027-05-06T11:00:00+09:00',
                    timezone: 'Asia/Tokyo',
                    place: { name: 'Hotel Okura', city: 'Tokyo' },
                },
                summary: 'Add lodging “Hotel Okura” on 2027-05-02 15:00',
            },
        ],
        plan_version: 1,
        created_at: '2026-10-01T12:00:00Z',
        decided_at: null,
        ...overrides,
    }
}
