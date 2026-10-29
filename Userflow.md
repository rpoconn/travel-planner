# User Flow: Chat → Guardrail → Agent → Proposal → Approval

How one chat message travels through the app, from the user's point of view, and every
check that decides what happens to it along the way. The short version: the assistant
never changes the trip on its own. Every change it wants to make is **staged** into a
proposal card, and nothing is written until the user picks **Approve**.

- [1. The big picture (sequence)](#1-the-big-picture-sequence)
- [2. Every decision in one turn (flowchart)](#2-every-decision-in-one-turn-flowchart)
- [3. Decision logic reference](#3-decision-logic-reference)
- [4. Approve / Deny / Other](#4-approve--deny--other)
- [5. Inside a tool call](#5-inside-a-tool-call)
- [6. What the user sees](#6-what-the-user-sees)

Source of truth: [router.py](backend/src/features/chat/router.py),
[pipeline.py](backend/src/features/chat/pipeline.py),
[guardrail.py](backend/src/features/chat/guardrail.py),
[agent.py](backend/src/features/chat/agent.py),
[tools.py](backend/src/features/chat/tools.py),
[prompts.py](backend/src/features/chat/prompts.py),
[service.py](backend/src/features/chat/service.py),
[chatTurn.ts](frontend/src/lib/chatTurn.ts),
[ProposalCard.tsx](frontend/src/components/ProposalCard/ProposalCard.tsx).

---

## 1. The big picture (sequence)

The same local model plays two roles: **topic classifier** (structured JSON-schema output)
and **planning agent** (streams text and tool calls). Write tools only validate during a
turn; the changes they stage are stored as one proposal on the assistant's reply.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI as ChatPanel (UI)
    participant API as Chat router
    participant P as Pipeline
    participant G as Guardrail
    participant A as Agent loop
    participant M as Model (Ollama)
    participant T as Tools
    participant DB as Database

    U->>UI: Types a message (or clicks "Fix it")
    UI->>API: POST /plans/{id}/chat
    Note over API: Pre-stream checks: auth, rate limit,<br/>body validation, plan ownership<br/>(failures are plain 401 / 429 / 422 / 404)
    API->>P: answer_message()
    P->>DB: Load last 12 messages, store user message

    alt Normal message
        P->>G: check_topic(last 6 messages, latest)
        G->>M: chat(format = GuardrailVerdict schema)
        M-->>G: on_topic, confidence, topic
        G-->>P: allowed?
    else "Fix it" request
        Note over P: Skip classifier (on topic by construction)<br/>latest = warning details + fix instructions
    end
    P-->>UI: SSE guardrail event

    alt Off topic
        P->>M: stream redirect (no tools)
        M-->>UI: SSE token events (friendly steer back to the trip)
    else On topic
        P->>A: run_agent(plan JSON, history, latest)
        loop Up to 6 tool rounds (+1 final text-only round)
            A->>M: stream(messages, tools)
            M-->>UI: SSE token events
            M-->>A: tool calls (if any)
            opt Model requested tools
                loop Each tool call
                    A-->>UI: SSE tool_call
                    alt web_search (read tool)
                        A->>T: run
                        T-->>A: results as text
                    else Plan change (write tool)
                        A->>T: prepare (validate only, no write)
                        T-->>A: Prepared change, or an error
                        Note over A: Valid change is staged<br/>(a repeat of a staged change is not)
                    end
                    A-->>UI: SSE tool_result (proposed = true when staged)
                    Note over A: Tool result appended to messages<br/>so the model sees it next round
                end
            end
        end
    end

    P->>DB: Store assistant reply + tool traces
    opt Changes were staged
        P->>DB: Store proposal (pending),<br/>mark any older pending proposal superseded
    end
    P-->>UI: SSE done (message.proposal = the card) or error
    UI-->>U: Reply, tool chips, proposal card with Approve / Deny / Other…
```

The user's answer to the card is a second request; see [section 4](#4-approve--deny--other).

---

## 2. Every decision in one turn (flowchart)

Diamonds are decisions. The label on each diamond is the actual rule in code. Tags like
**D3** match the rows in [section 3](#3-decision-logic-reference).

```mermaid
flowchart TD
    Start(["User sends message"]) --> D1{"D1 · Authenticated?"}
    D1 -->|no| E401["401 Unauthorized"]
    D1 -->|yes| D2{"D2 · Under 20 messages/min<br/>for this user?"}
    D2 -->|no| E429["429 + Retry-After"]
    D2 -->|yes| D3{"D3 · Body valid?<br/>1–4000 chars after trim"}
    D3 -->|no| E422["422 Validation error"]
    D3 -->|yes| D4{"D4 · Plan exists and<br/>belongs to user?"}
    D4 -->|no| E404["404 Plan not found"]
    D4 -->|yes| Stream["SSE stream opens"]

    Stream --> Hist["Load last 12 messages<br/>keep kind = message or decision<br/>(drops redirects + error notices)"]
    Hist --> Store["Store user message"]
    Store --> D5{"D5 · Is this a<br/>'Fix it' request?"}

    D5 -->|yes| Fix["allowed = true, topic = 'itinerary fix'<br/>latest = warning details + instructions"]
    D5 -->|no| Topic["Topic classifier<br/>(last 6 messages + latest)"]

    Topic --> D6{"D6 · JSON matches<br/>GuardrailVerdict?"}
    D6 -->|"no, attempt 1"| Topic
    D6 -->|"no, attempt 2"| FailOpen["Fail OPEN → allowed"]
    D6 -->|yes| D7{"D7 · on_topic OR<br/>confidence below 0.5?"}
    D7 -->|no| Redirect["Mark user msg kind=redirect<br/>Stream short redirect reply (no tools)"]
    D7 -->|yes| Agent
    FailOpen --> Agent
    Fix --> Agent

    Agent["Build messages:<br/>system prompt (rules + checklist + plan JSON + today)<br/>+ history + latest"] --> Round

    Round["Stream model reply<br/>(tokens → UI, collect tool calls)"] --> D8{"D8 · Any tool calls?<br/>and not final round?"}
    D8 -->|no| Empty
    D8 -->|yes| Each["For each tool call:<br/>emit tool_call"]

    Each --> D9{"D9 · Does the tool<br/>write to the plan?<br/>(unknown tool = write)"}
    D9 -->|"no (web_search)"| Run["Run it (section 5)"]
    D9 -->|yes| Prep["Prepare: validate only,<br/>write nothing (section 5)"]
    Prep --> D10{"D10 · Valid?"}
    D10 -->|no| Err["Error text for the model<br/>to correct and retry"]
    D10 -->|yes| D11{"D11 · Same change<br/>already staged?"}
    D11 -->|yes| Dup["'Already staged; do not repeat it'"]
    D11 -->|no| Stage["Stage it<br/>'Staged for the user's approval…'"]

    Run --> Result["emit tool_result"]
    Err --> Result
    Dup --> Result
    Stage --> Result
    Result --> Feed["Append tool result to messages"]
    Feed --> D13{"D13 · More calls<br/>this round?"}
    D13 -->|yes| Each
    D13 -->|no| D14{"D14 · Used 6<br/>tool rounds?"}
    D14 -->|no| Round
    D14 -->|yes| Final["Add 'reply now, no more tools'<br/>Final round: tools disabled"]
    Final --> Round

    Redirect --> Empty
    Empty{"D15 · Reply text empty?"}
    Empty -->|"yes, changes staged"| FB0["'Here's what I'd change —<br/>approve the card to apply it.'"]
    Empty -->|"yes, nothing staged"| FB2["'Sorry, I don't have an answer…'"]
    Empty -->|no| Save
    FB0 --> Save
    FB2 --> Save
    Save["Store assistant reply<br/>(kind + tool traces)"] --> D16{"D16 · Anything staged?"}
    D16 -->|yes| Prop["Store proposal (pending)<br/>older pending one → superseded"]
    D16 -->|no| Done
    Prop --> Done(["emit done"])

    Round -.->|"model unreachable<br/>after 3 retries"| LLMErr["Store error notice<br/>emit error"]
    Topic -.-> LLMErr
```

---

## 3. Decision logic reference

| # | Decision | Logic | If it fails | Where |
|---|----------|-------|-------------|-------|
| D1 | Authenticated | `CurrentUser` dependency resolves the session user | `401` before the stream opens | [router.py](backend/src/features/chat/router.py) |
| D2 | Rate limit | Sliding 60 s window per user id, default **20** messages/min (`chat_rate_limit_per_minute`, 0 = off). Messages and card decisions share the budget. Rejected hits aren't counted. | `429` with `Retry-After` seconds | [rate_limit.py](backend/src/shared/rate_limit.py), [dependencies.py](backend/src/features/chat/dependencies.py) |
| D3 | Body valid | `content` trimmed, 1–4000 chars. Optional `fix` = a `TimelineWarning` | `422` | [schemas.py](backend/src/features/chat/schemas.py) |
| D4 | Plan ownership | `plans_service.get_plan(user_id, plan_id)` | `404` (checked before streaming so it's a normal HTTP error) | [router.py](backend/src/features/chat/router.py) |
| D5 | Fix request? | `body.fix is not None` → skip the classifier and wrap the message with the warning + fix instructions. The agent has every tool, so it proposes the fix on a card (or asks which of 2-3 fixes the user wants). | n/a | [pipeline.py](backend/src/features/chat/pipeline.py), [prompts.py](backend/src/features/chat/prompts.py) |
| D6 | Classifier output valid | `GuardrailVerdict.model_validate_json(reply)`; up to **2** attempts | **Fail open**: allow the message (a wrong redirect is worse than an occasional off-topic answer) | [guardrail.py](backend/src/features/chat/guardrail.py) |
| D7 | On topic | `allowed = on_topic or confidence < 0.5`. Greetings, thanks and follow-ups like "yes please" count as on topic. | Redirect: user message re-tagged `kind=redirect` so it never enters future agent context, and the model writes 1–2 sentences steering back to the trip | [guardrail.py](backend/src/features/chat/guardrail.py), [prompts.py](backend/src/features/chat/prompts.py) |
| D8 | Keep looping | Continue while the model returns tool calls and the round isn't the final one | Return: the turn's text is the answer | [agent.py](backend/src/features/chat/agent.py) |
| D9 | Write tool? | `changes_plan(name)`: true for every tool except `web_search` (a `ReadTool`); an **unknown** tool name also counts as a write | n/a | [tools.py](backend/src/features/chat/tools.py) |
| D10 | Change valid | The write tool's `prepare` step: same Pydantic schemas as the REST API, past-date check, item exists, plan rules, flight direction inference. **Nothing is written.** | Model gets `Error: …` and can correct the arguments | [tools.py](backend/src/features/chat/tools.py) |
| D11 | Already staged | Same tool and same **normalised** arguments as a change staged earlier this turn (small models often re-send) | Not staged twice. Model is told "Already staged; do not repeat it" | [agent.py](backend/src/features/chat/agent.py) |
| D14 | Round limit | `MAX_TOOL_ROUNDS = 6`. On round 7 a system note says "reply now", and tools are disabled, so the turn **always ends in text** | n/a | [agent.py](backend/src/features/chat/agent.py) |
| D15 | Empty reply | Small models sometimes stop after tool calls with no text | Fallback: "Here's what I'd change — approve the card to apply it." if changes were staged, "I've updated your itinerary." after an approval applied them, else "Sorry, I don't have an answer for that. Could you rephrase?" | [pipeline.py](backend/src/features/chat/pipeline.py) |
| D16 | Proposal | Staged changes are stored as one `chat_proposals` row on the reply (`status = pending`). Only one card per plan stays open: storing a new one marks any other pending one **superseded** in the same commit. | n/a | [service.py](backend/src/features/chat/service.py) |
| — | Model transport | Connection errors, HTTP 429 and 5xx are retried with exponential backoff (default 3 attempts, `llm_max_attempts`). A 404 (model not pulled) is not retried. | `LLMError` → stored `system` notice (`kind=error`) + `error` event. Any other exception → rollback + "Something went wrong…" | [ollama_client.py](backend/src/shared/llm/ollama_client.py), [pipeline.py](backend/src/features/chat/pipeline.py) |

**Asking before proposing.** The system prompt has a checklist the agent must cover before
`set_plan_details` or any `add_*` tool: travel dates, number of travelers (adults and
children) and origin; for flights, airline, departure-time and cabin preferences; for
lodging, area, type and budget; for meals, dietary needs and whether a reservation is
wanted. A new plan's `travelers` is unknown (`null`, left out of the plan JSON) rather than
"1 adult", so the agent sees the gap and asks. Its worked example: *"I want to spend a week
in Tokyo in May"* gets questions, not a tool call.

**Fail open vs. the user as the check:**

| Check | Protects against | When it breaks |
|-------|------------------|----------------|
| Topic classifier | Off-topic use of the assistant | **Open**: let the message through |
| Proposal card | The agent inventing trip details | Can't break: a change only reaches the plan when the user approves it |

---

## 4. Approve / Deny / Other

Each proposal card is answered with one request,
`POST /plans/{plan_id}/chat/proposals/{proposal_id}/decision` (SDK: `decideProposal`), body
`{action: "approve" | "deny" | "revise", message?}`. It streams the same `ChatEvent`s as
`sendMessage` and shares its rate limit. A card is all-or-nothing: Approve applies every
change, Deny none.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI as ProposalCard / ChatPanel
    participant API as Chat router
    participant P as Pipeline
    participant T as Tools
    participant A as Agent
    participant DB as Database

    U->>UI: Approve / Deny / Other… (+ text)
    UI->>API: POST …/proposals/{id}/decision
    Note over API: Pre-stream checks: auth, rate limit, plan ownership,<br/>proposal exists in this plan (404), still pending (409),<br/>revise has a message / others don't (422)
    API->>P: decide_proposal()
    P-->>UI: SSE guardrail (on_topic, "proposal decision"): no classifier call

    alt Approve
        P->>DB: Store user decision "Approved the proposed changes."
        loop Phase 1: each change
            P->>T: check_change (prepare again, no write)
        end
        alt Every change still valid
            loop Phase 2: each change, in order
                P-->>UI: SSE tool_call
                P->>T: apply_change (prepare again, then write)
                T->>DB: write
                P-->>UI: SSE tool_result ("Added lodging “…”")
            end
            P-->>UI: SSE plan_updated (timeline refreshes)
            P->>DB: proposal → approved
            P->>A: latest = "approved; these are now applied: …"
        else A change no longer fits (item removed, date now past…)
            Note over P: Nothing is written
            P->>DB: proposal → failed
            P->>A: latest = "approved, but could not be applied: …"
        end
        A-->>UI: Confirms in one sentence, suggests the next step<br/>(may stage a new proposal)
    else Deny
        P->>DB: Store user decision "Denied the proposed changes."
        P->>DB: proposal → denied
        P-->>UI: SSE token: "No problem — nothing was changed. …" (no model call)
    else Other… (revise)
        P->>DB: Store the user's text (kind = decision)
        P->>DB: proposal → revised
        P->>A: latest = text + the proposal it replaces
        A-->>UI: Answers and usually re-proposes (a new card)
    end

    P->>DB: Store assistant reply (+ new proposal, if any)
    P-->>UI: SSE done
```

| Status | Meaning | Card shows |
|--------|---------|------------|
| `pending` | Waiting for the user | Approve / Deny / Other… buttons |
| `approved` | Every change was applied | "Approved" badge |
| `denied` | Nothing applied, by the user's choice | "Denied" badge |
| `revised` | The user asked for something else instead | "Revised" badge |
| `superseded` | A newer proposal replaced it before a decision | "Superseded" badge |
| `failed` | Approved, but a change no longer validated; nothing (or, rarely, only the first part) was applied, and the agent was told exactly what | "Couldn't apply" badge |

| Case | Response |
|------|----------|
| Proposal id unknown, or in another user's plan | `404` |
| Proposal already decided or superseded (another tab, a double click) | `409` "This proposal was already …" |
| `revise` without `message`, or `approve`/`deny` with one | `422` |

**Context after reload.** The model never sees its old tool calls, so in the agent's history
an assistant message that proposed changes is rendered as its text plus
`[Proposed changes (<status>): - <summary> …]`, and the user's decisions (`kind=decision`)
are included like normal messages.

**"Fix it" flow.** Clicking *Fix it* on a timeline warning sends a turn with the warning
details and fix instructions (D5). The agent proposes the fix on a card like any other
change (or asks which fix the user wants), and Approve applies it. Only the visible
`Fix: …` text is stored, not the injected instructions.

---

## 5. Inside a tool call

Every write tool has two steps: **prepare** (validate, write nothing) runs while the agent
replies and again when the user approves; **apply** (write) runs only on approval, right
after the re-prepare. Errors are returned to the model as text so it can correct itself.
They never crash the turn.

```mermaid
flowchart TD
    Call(["Tool call from model"]) --> K{"Known tool?"}
    K -->|no| F0["Error: Unknown tool, lists available tools"]
    K -->|yes| N["Drop null arguments and echoed schemas<br/>(both mean 'not provided')"]
    N --> V{"Pydantic validation<br/>(same schemas as REST API)"}
    V -->|fails| F1["Error: Invalid arguments<br/>field: message (sent keys: …)"]
    V -->|passes| Past{"Any date before today?"}
    Past -->|yes| F2["Error: date is in the past<br/>suggest next occurrence<br/>e.g. May → next May"]
    Past -->|no| Ty{"Which tool?"}

    Ty -->|add_flight| Dir["Infer flight direction<br/>(below)"]
    Ty -->|"add_lodging / activity / transfer"| Stage
    Ty -->|set_plan_details| PD{"Any fields given?<br/>Plan rules valid?"}
    PD -->|no| F3["Error: No plan details / invalid plan"]
    PD -->|yes| Stage
    Ty -->|update_item| UI1{"Item exists?<br/>Fields exist on this item type?<br/>Any changes? Merged item valid?"}
    UI1 -->|no| F4["Error: no item / no such field / no changes"]
    UI1 -->|yes| Stage
    Ty -->|remove_item| RI{"Item exists?"}
    RI -->|no| F4
    RI -->|yes| Stage
    Ty -->|web_search| WS["Shape query with plan<br/>origin / destination / dates"]

    Dir --> Stage["Stage (no write): normalised arguments<br/>+ summary, e.g. 'Add flight “SFO → NRT”<br/>on 2027-05-01 11:00'<br/>chip: 'Proposed: …'"]
    WS --> SR{"Search succeeded?"}
    SR -->|"no (provider error)"| F5["ok = false<br/>'Don't repeat this search,<br/>answer from general knowledge'"]
    SR -->|"yes, 0 results"| R0["ok = true<br/>'Try fewer or different words'"]
    SR -->|"yes, results"| R1["ok = true<br/>numbered results + URLs<br/>'set source_url when adding'"]
```

**Flight direction inference** (small models often mislabel the flight home as
"outbound"). Strongest evidence wins; the inferred direction is part of the staged
arguments, so the card shows what will be stored:

```mermaid
flowchart LR
    S(["add_flight"]) --> O{"Leaves from or lands at<br/>the trip origin?<br/>(IATA same-area, or city name match)"}
    O -->|"leaves origin"| OUT1["outbound"]
    O -->|"lands at origin"| RET1["return"]
    O -->|neither| Dt{"On trip's first day or last day?<br/>and model did NOT say 'internal'"}
    Dt -->|"first day"| OUT2["outbound"]
    Dt -->|"last day"| RET2["return"]
    Dt -->|"neither / both / model said internal"| Mi{"Mirrors an existing flight?"}
    Mi -->|"lands where outbound left"| RET3["return"]
    Mi -->|"leaves where return landed"| OUT3["outbound"]
    Mi -->|no| Def["Model's label,<br/>else 'internal'"]
```

---

## 6. What the user sees

Each SSE event maps to something on screen
([chatTurn.ts](frontend/src/lib/chatTurn.ts), [planCache.ts](frontend/src/lib/planCache.ts)):

| Event | When | UI effect |
|-------|------|-----------|
| `guardrail` | Always first | `onTopic` set. `false` means the reply is a redirect. |
| `token` | Model streaming text | Appended to the live reply |
| `tool_call` | Agent invokes a tool, or an approved change is applied | Chip in *running* state, e.g. "Searching the web for “ryokan in Kyoto”…", "Preparing a flight…" |
| `tool_result` | Tool finished | `proposed = true`: neutral chip "Proposed: Add flight “…”". Otherwise ok / failed with the server summary, e.g. "Added flight “…”" (after Approve) |
| `plan_updated` | Approved changes were written | Plan written straight into the TanStack Query cache, so the itinerary timeline updates mid-turn |
| `done` | Turn finished | Stored reply replaces the live text. `done.message.proposal` shows the card under the reply (inert until the history refetch); then history is refetched |
| `error` | Model unreachable / unexpected failure | Error shown. A stored system notice keeps it in the history. |

The card ([ProposalCard.tsx](frontend/src/components/ProposalCard/ProposalCard.tsx)) lists
one row per change with its key details, and while pending offers **Approve**, **Deny** and
**Other…** (a text box; Enter sends). Its buttons are disabled while any reply streams.
Decisions appear in the transcript as the user's own messages.

> **Offline mode** (`LLM_PROVIDER=fake`): a keyword match stands in for the topic
> classifier, and only `"add <something>"` triggers a tool call (`add_activity` tomorrow
> 10:00–12:00), which is proposed like any other change. After Approve the scripted reply is
> "Done! It's on your itinerary."; "Other…" text starting with "add" proposes that instead.
> The decision flow above is otherwise identical. See
> [offline.py](backend/src/features/chat/offline.py).
