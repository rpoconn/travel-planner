import { MessageBubble } from '@/components/MessageBubble/MessageBubble'
import { ProposalCard } from '@/components/ProposalCard/ProposalCard'
import type { ChatTurn } from '@/lib/chatTurn'

// The in-flight turn: the user's message, the streaming reply, any staged proposal and a
// failure notice. Replaced by the stored history once the turn ends and is refetched.

// ----- types -----

interface LiveTurnProps {
    turn: ChatTurn
}

export function LiveTurn({ turn }: LiveTurnProps) {
    // ----- state -----
    // Once the guardrail says off-topic, the reply renders as the hint card, matching how the
    // stored redirect looks after the history refetch.
    const kind = turn.onTopic === false ? 'redirect' : 'message'
    const streaming = turn.status === 'streaming'
    const hasReply = streaming || turn.text !== '' || turn.tools.length > 0

    // ----- markup -----
    return (
        <>
            <MessageBubble role="user" kind={kind} content={turn.userText} />
            {hasReply && (
                <MessageBubble
                    role="assistant"
                    kind={kind}
                    content={turn.text}
                    tools={turn.tools}
                    streaming={streaming}
                />
            )}
            {turn.proposal && (
                // Inert: the turn is still in flight until the history refetch replaces it.
                <ProposalCard proposal={turn.proposal} disabled onDecide={() => undefined} />
            )}
            {turn.status === 'error' && turn.error && (
                <MessageBubble role="system" kind="error" content={turn.error} />
            )}
        </>
    )
}
