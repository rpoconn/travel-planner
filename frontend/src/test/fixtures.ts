import type {
    ActivityItem,
    AskInput,
    Candidate,
    ChatMessage,
    ChatProposal,
    FlightItem,
    Gap,
    Issue,
    IssueCode,
    LodgingItem,
    OfferChoice,
    Place,
    PlanAssessment,
    PlanningMessage,
    PlanStage,
    PlanSummary,
    StageStatus,
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
    home: null,
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
        stage: 'basics',
        destination: { name: 'Tokyo' },
        start_date: '2027-05-01',
        end_date: '2027-05-08',
        version: 1,
        updated_at: '2026-10-01T12:00:00Z',
        ...overrides,
    }
}

export function travelPlan(overrides: Partial<TravelPlan> = {}): TravelPlan {
    const stage = overrides.stage ?? 'basics'
    return {
        id: 'aaaaaaaa-0000-0000-0000-000000000001',
        user_id: ADA.id,
        title: 'Tokyo in May',
        status: 'draft',
        stage,
        flights_skipped: false,
        origin: null,
        destination: { name: 'Tokyo' },
        start_date: '2027-05-01',
        end_date: '2027-05-08',
        travelers: { adults: 2, children: 0 },
        budget: null,
        limits: [],
        preferences: { flights: null, lodging: null, activities: null, declined: [] },
        notes: '',
        version: 1,
        created_at: '2026-10-01T12:00:00Z',
        updated_at: '2026-10-01T12:00:00Z',
        items: [],
        stages: stageStatuses(),
        gaps: [],
        assessment: planAssessment({ stage }),
        ...overrides,
    }
}

/** The engine's verdict; by default the stage is done and nothing is asked. */
export function planAssessment(overrides: Partial<PlanAssessment> = {}): PlanAssessment {
    return {
        stage: 'basics',
        issues: [],
        next_action: { type: 'complete' },
        can_advance: false,
        ...overrides,
    }
}

/** Where Ada's trips start and end. */
export const PHILADELPHIA: Place = { name: 'Philadelphia', city: 'Philadelphia', iata: 'PHL' }

// A plausible code per stage for fixture issues; tests only read the messages.
const FIXTURE_CODES: Record<PlanStage, IssueCode> = {
    basics: 'missing_dates',
    flights: 'missing_outbound',
    lodging: 'uncovered_night',
    activities: 'no_activities',
    ground: 'missing_transfer',
    review: 'stage_incomplete',
}

function stageStatus(stage: PlanStage, missing: string[]): StageStatus {
    const issues: Issue[] = missing.map((message) => ({
        code: FIXTURE_CODES[stage],
        stage,
        severity: 'blocking',
        message,
        date: null,
        item_ids: [],
        gap: null,
    }))
    return { stage, issues, ready: issues.length === 0, missing }
}

/**
 * One status per stage, in order: ready unless listed in `missing` (stage → what's missing).
 * Review is ready only when every other stage is.
 */
export function stageStatuses(
    missing: Partial<Record<StageStatus['stage'], string[]>> = {},
): StageStatus[] {
    const stages = ['basics', 'flights', 'lodging', 'activities', 'ground'] as const
    const statuses = stages.map((stage) => stageStatus(stage, missing[stage] ?? []))
    const review =
        missing.review ?? statuses.filter((s) => !s.ready).map((s) => `the ${s.stage} stage`)
    return [...statuses, stageStatus('review', review)]
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
    return {
        plan_id: PLAN_ID,
        version: 1,
        days: [],
        lodging: [],
        warnings: [],
        gaps: [],
        home: null,
        ...overrides,
    }
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
                // Still in San Francisco time until the flight lands.
                timezone: 'America/Los_Angeles',
            },
            {
                index: 1,
                date: '2027-05-02',
                entries: [
                    timelineEntry(flight, { continues_from_previous_day: true }),
                    timelineEntry(transferItem()),
                ],
                timezone: 'Asia/Tokyo',
            },
            {
                index: 2,
                date: '2027-05-03',
                entries: [timelineEntry(activityItem())],
                timezone: 'Asia/Tokyo',
            },
            { index: 3, date: '2027-05-04', entries: [], timezone: 'Asia/Tokyo' },
            { index: 4, date: '2027-05-05', entries: [], timezone: 'Asia/Tokyo' },
        ],
        lodging: [{ item: lodgingItem(), start_day_index: 1, end_day_index: 4 }],
    })
}

/** A gap from home to the outbound flight (SFO); override the ends for other gaps. */
export function gap(overrides: Partial<Gap> = {}): Gap {
    return {
        from_place: { name: 'San Francisco', city: 'San Francisco' },
        to_place: { name: 'San Francisco International', iata: 'SFO', city: 'San Francisco' },
        after_item_id: null,
        before_item_id: 'flight-1',
        leave_after: null,
        arrive_before: '2027-05-01T11:00:00-07:00',
        timezone: 'America/Los_Angeles',
        ...overrides,
    }
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

// ----- planning flow -----

/** A planning transcript entry; by default the assistant asking where the trip goes. */
export function planningMessage(overrides: Partial<PlanningMessage> = {}): PlanningMessage {
    return {
        id: 'planning-1',
        plan_id: PLAN_ID,
        role: 'assistant',
        kind: 'message',
        content: 'Where are you going?',
        payload: null,
        created_at: '2026-10-01T12:00:00Z',
        ...overrides,
    }
}

/** The pending destination question, as the engine asks it for a new plan. */
export const ASK_DESTINATION: AskInput = {
    type: 'ask_input',
    slot: 'destination',
    stage: 'basics',
    prompt: 'Where are you going?',
    fields: [
        {
            name: 'destination',
            label: 'Destination',
            kind: 'place',
            required: true,
            options: [],
            min: null,
            max: null,
            placeholder: 'Lisbon',
        },
    ],
    optional: false,
    prefill: {},
}

export const ASK_DATES: AskInput = {
    type: 'ask_input',
    slot: 'dates',
    stage: 'basics',
    prompt: 'When are you going?',
    fields: [
        {
            name: 'dates',
            label: 'Trip dates',
            kind: 'date_range',
            required: true,
            options: [],
            min: null,
            max: null,
            placeholder: '',
        },
    ],
    optional: false,
    prefill: {},
}

export const ASK_BUDGET: AskInput = {
    type: 'ask_input',
    slot: 'budget',
    stage: 'basics',
    prompt: 'Do you have a budget for the whole trip?',
    fields: [
        {
            name: 'budget',
            label: 'Total budget',
            kind: 'money',
            required: true,
            options: [],
            min: null,
            max: null,
            placeholder: '',
        },
    ],
    optional: true,
    prefill: {},
}

/** Flight preferences: two required choices and one optional choice with an "Any" (null). */
export const ASK_FLIGHT_PREFERENCES: AskInput = {
    type: 'ask_input',
    slot: 'flight_preferences',
    stage: 'flights',
    prompt: 'How do you like to fly?',
    fields: [
        {
            name: 'cabin',
            label: 'Cabin',
            kind: 'choice',
            required: true,
            options: [
                { value: 'economy', label: 'Economy' },
                { value: 'business', label: 'Business' },
            ],
            min: null,
            max: null,
            placeholder: '',
        },
        {
            name: 'max_stops',
            label: 'Stops',
            kind: 'choice',
            required: false,
            options: [
                { value: 0, label: 'Nonstop' },
                { value: 1, label: 'Up to 1 stop' },
                { value: null, label: 'Any' },
            ],
            min: null,
            max: null,
            placeholder: '',
        },
    ],
    optional: false,
    prefill: {},
}

/** A plan in `stage` whose engine asks `action` next. */
export function planAsking(action: AskInput, overrides: Partial<TravelPlan> = {}): TravelPlan {
    return travelPlan({
        stage: action.stage,
        assessment: planAssessment({ stage: action.stage, next_action: action }),
        ...overrides,
    })
}

/** An option in the planner's choices; by default the first canned flight out. */
export function candidate(overrides: Partial<Candidate> = {}): Candidate {
    return {
        id: '1',
        title: 'Example Air EX 101',
        subtitle: 'PHL Sat May 1 09:00 → LIS Sat May 1 22:00',
        details: ['13h', 'nonstop'],
        warnings: [],
        price: { amount: '1800', currency: 'USD' },
        source_url: null,
        changes: [{ name: 'add_flight', arguments: {}, summary: 'Add flight out PHL → LIS' }],
        follow_up_token: null,
        ...overrides,
    }
}

/** The flights out to pick from: two canned options. */
export const OFFER_FLIGHTS_OUT: OfferChoice = {
    type: 'offer_choice',
    offer_id: 'bbbbbbbb-0000-0000-0000-000000000001',
    slot: 'flight_outbound',
    stage: 'flights',
    prompt: 'Pick your flight out.',
    candidates: [
        candidate(),
        candidate({ id: '2', title: 'Example Air EX 102', subtitle: 'PHL Sat May 1 18:30' }),
    ],
    min_picks: 1,
    max_picks: 1,
    gap: null,
    nights: null,
}

/** Things to do: pick one to three of three. */
export const OFFER_ACTIVITIES: OfferChoice = {
    type: 'offer_choice',
    offer_id: 'bbbbbbbb-0000-0000-0000-000000000002',
    slot: 'activities',
    stage: 'activities',
    prompt: "Pick the things you'd like to do.",
    candidates: [
        candidate({ id: '1', title: 'Tile museum', price: null }),
        candidate({ id: '2', title: 'Fado night', price: null }),
        candidate({ id: '3', title: 'Tram 28', price: null }),
    ],
    min_picks: 1,
    max_picks: 3,
    gap: null,
    nights: null,
}

/** Places to stay for the nights of May 1 and 2. */
export const OFFER_LODGING: OfferChoice = {
    type: 'offer_choice',
    offer_id: 'bbbbbbbb-0000-0000-0000-000000000003',
    slot: 'lodging_stay',
    stage: 'lodging',
    prompt: 'Pick a place to stay.',
    candidates: [
        candidate({
            id: '1',
            title: 'Example Riverside Hotel',
            subtitle: '3-star hotel · rated 4.5 (300 reviews)',
            details: ['2 nights: in May 1 at 15:00, out May 3 at 11:00'],
            price: { amount: '280', currency: 'USD' },
        }),
    ],
    min_picks: 1,
    max_picks: 1,
    gap: null,
    nights: { check_in: '2027-05-01', check_out: '2027-05-03' },
}

/** A plan in `stage` whose engine shows `action`'s choices next. */
export function planChoosing(action: OfferChoice, overrides: Partial<TravelPlan> = {}): TravelPlan {
    return travelPlan({
        stage: action.stage,
        assessment: planAssessment({ stage: action.stage, next_action: action }),
        ...overrides,
    })
}
