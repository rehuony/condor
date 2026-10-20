# Telegram agent reply delivery

`TelegramStreamer` presents private-chat agent replies and background wake turns
using silent progress followed by a separate final answer. Input handling is
specified in [Telegram conversations](telegram-conversations.md).

## Configuration

Optional process settings:

```dotenv
TELEGRAM_AGENT_REPLY_MODE=final
TELEGRAM_AGENT_EDIT_INTERVAL=1.0
```

`final` buffers assistant text until completion. `edit` updates a temporary
ordinary-message preview at the configured minimum interval in seconds. Both
send the final answer as new messages. The default interval is one second;
Telegram's `RetryAfter` delay takes precedence. Invalid modes and nonpositive or
nonfinite intervals fail startup checks.

These settings apply to private-chat agent replies and wake turns. Dashboard
rendering, routine notifications and trading controls have separate behavior.
Apply process configuration on restart.

## Runtime and ownership

- User input passes through admission and media resolution to `runtime.prompt`,
  the conversation queue, the session/provider, and then `TelegramStreamer`.
  Wake turns use `TelegramWakeSink` with the same runtime and presenter; opening
  the sink performs no network request.
- Private chats use `tg:<chat_id>` session keys. The runtime owns durable
  acceptance, deduplication, conversation FIFO, authorization and history.
- Approval messages come from `TelegramChannel`. The presenter observes pending
  confirmations for its active request; the confirmation registry owns approval,
  authorization and expiry.
- `/stop` calls `runtime.abort`; inline Stop resolves its opaque run token and
  calls `runtime.abort_turn(owner, conversation, request)`. Stale or duplicate
  buttons cannot stop a later request. Stop ends generation without cancelling
  orders, closing positions, stopping trading bots or undoing completed actions.

The presenter owns its request identity, temporary message IDs, one progress
worker, one typing worker and one finalization task. Queued requests show one
silent waiting notice, without typing or animation. Execution starts typing and
silent status updates derived from actual tool and confirmation events. Typing
pauses during approval. Raw reasoning and tool logs are hidden.

All assistant `TEXT` remains in order because providers do not expose a reliable
commentary/final boundary. Finalization freezes that body, retains any redacted
voice transcript, retires Stop, and drains or cancels the progress and typing
workers. Concurrent finalizers share one task; late events cannot change the
result. Per-chat `OutputLease` instances serialize final delivery in FIFO order.
Different chats remain independent, and owner cancellation closes the final sender.

Relevant event fields:

| Field | Contract |
| --- | --- |
| `ACCEPTED.data.request_id`, `conversation_id`, `agent_slug` | Bind presentation and controls to the accepted runtime request. |
| Session startup `HEARTBEAT` | Indicates execution ownership before provider output. |
| `TextChunk.is_error` / `TEXT.data.is_error` | Provider diagnostics; default false. Telegram shows a safe failure notice instead of raw exception text. |
| `ERROR.data.code = "images_unsupported"` | A known image capability refusal that permits specific guidance. |

## Delivery and failures

`finalize()` returns `DeliveryResult` with `status`, `payloads`, `confirmed` and
`message_ids`. Status is `pending`, `complete`, `failed`, `partial` or
`delivery_unknown`. Completion requires Telegram acknowledgement for every
segment; successful model termination alone proves neither delivery nor execution
of a requested trade.

- Confirmed segments advance the cursor once. Only the first final segment may
  notify; progress and later segments are silent. Link previews are suppressed.
  Telegram client mute settings still apply.
- Read/write failures, ambiguous timeouts and missing acknowledgements are
  unknown outcomes and are never blindly resent. A definite connection failure
  or connection timeout before sending permits one retry of the unconfirmed
  page. `BadRequest`, `Forbidden` and exhausted rate limits are failures.
- `RetryAfter` uses the server delay, including `timedelta` values, with at most
  three attempts. Progress uses `telegram_call(..., retry_rate_limits=False)`
  and schedules a fresh snapshot after waiting, without blocking the runtime
  consumer. Repeated preview failures disable progress, not execution or final
  delivery.
- A known missing or uneditable preview permits one replacement. Exact
  `Message is not modified` responses acknowledge the requested text and markup.
- HTML parsing failures downgrade only unconfirmed content to readable plain
  text, preserving link destinations. Segments respect UTF-16 limits, escaped
  content and formatting wrappers. Oversized link markup falls back to split
  plain text.
- Complete delivery removes only the run's own progress and preview messages.
  Failed deletion attempts an ended label and Stop removal. Incomplete delivery
  preserves visible content and removes Stop where possible. A status can direct
  users to the saved dashboard conversation, but a failed edit cannot guarantee
  that notice arrives. Delivery failures never rerun the model or tools.

The delivery cursor is process-local, so delivery has no cross-network or
cross-restart exactly-once guarantee. Durable request deduplication separately
prevents repeated execution of accepted transport messages. Logs retain error
types, hashed chat references and segment counts, excluding payloads, credentials
and raw exception URLs.

Execution failures retain partial answers. A later `DONE` does not clear an
already recorded failure.

## Verification and deployment

Run the delivery regression suite with:

```bash
python -m pytest -q \
  tests/test_telegram_layered_replies.py tests/test_telegram_output.py \
  tests/test_telegram_reply_integration.py tests/test_agent_stop_generating_button.py \
  tests/test_agent_stop_telegram.py tests/test_agent_stream_dedupe.py \
  tests/test_telegram_delivery_failures.py tests/test_agents_wake_telegram.py
```

These tests use fake bots, mocked HTTP transport and isolated stores. Live
acceptance uses an authorized bot/chat and checks acknowledged message IDs,
send/edit counts, notification flags and task cleanup. Client notifications
require separate observation from API acknowledgements. Keep run-specific results
in `.condor/research/`.

Follow the [maintenance workflow](maintenance.md) for branch
publication. Handler hot reload can activate edits immediately; deploy runtime
and handler changes coherently and verify the running process separately from
Git state. Preserve configuration, conversation records and attachments during
rollback. This delivery protocol requires no data migration.
