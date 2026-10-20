# Telegram agent reply delivery

`RichReplyStreamer` presents private-chat agent replies and background wake turns
as Bot API 10.3 rich-message drafts while the model runs, then a separate final
rich message. Input handling is specified in [Telegram conversations](telegram-conversations.md).

## Runtime and ownership

- User input passes through admission and media resolution to `runtime.prompt`,
  the conversation queue, the session/provider, and then `RichReplyStreamer`.
  Wake turns use `TelegramWakeSink` with the same runtime and presenter; opening
  the sink performs no network request. The sink is registered once at process
  start (`register_wake`), not at import, so a handler reload cannot attach a
  second one.
- Private chats use `tg:<chat_id>` session keys. The runtime owns durable
  acceptance, deduplication, conversation FIFO, authorization and history.
- Approval messages come from `TelegramChannel`. The presenter observes pending
  confirmations for its active request; the confirmation registry owns approval,
  authorization and expiry. A non-draft message in the same chat hides the
  draft; the streamer immediately resends it with the same `draft_id`.
- `/stop` calls `runtime.abort`. The client's bottom-right native stop (from
  `can_stop=True` on the draft) delivers `Update.stopped_message_generation`
  with `chat` and `draft_id`. The router looks up `(chat, draft_id)` and calls
  `runtime.abort_turn(owner, conversation, request)`. Stale or duplicate stop
  events cannot stop a later request. Stop ends generation without cancelling
  orders, closing positions, stopping trading bots or undoing completed actions.

The presenter owns its request identity, a process-unique non-zero `draft_id`,
one progress worker and one finalization task. Queued requests show one
"waiting" draft. Execution refreshes the draft about once a second (untouched
drafts vanish in about three seconds) with `keep_on_stop=True`. After a native
stop the streamer must not send another draft for that id. Raw reasoning and
tool logs are hidden. Groups and other non-private chats skip drafts and only
send the final message.

All assistant `TEXT` remains in order because providers do not expose a reliable
commentary/final boundary. Finalization freezes that body, retains any redacted
voice transcript, and drains or cancels the progress worker. Concurrent
finalizers share one task; late events cannot change the result. Per-chat
`OutputLease` instances serialize final delivery in FIFO order. Different chats
remain independent, and owner cancellation closes the final sender.

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
  notify; later segments are silent. Draft updates do not notify.
- Markdown longer than 32000 characters is split. `RICH_MESSAGE_TEXT_TOO_LONG`
  splits further. A parse failure falls back to plain `send_message`.
- Read/write failures, ambiguous timeouts and missing acknowledgements are
  unknown outcomes and are never blindly resent. A definite connection failure
  before sending permits one retry of the unconfirmed page. `BadRequest`,
  `Forbidden` and exhausted rate limits are failures.
- Progress uses `telegram_call(..., retry_rate_limits=False)` and backs off on
  `RetryAfter` (about 11 s after a tight burst). Three consecutive rate limits
  disable further drafts for that turn.

## Notifications and reports

`send_text(..., parse_mode="Markdown")` — notifications, wake notes, routine
results, and delegation details — sends `send_rich_message` first so tables stay
tables. A parse rejection or a sender without that method falls back to the HTML
renderer. Menus stay MarkdownV2. A confirmed non-draft send restores any live
draft in that chat.

## Verification

Confirm a private-chat turn shows a native-stop draft, a regular approval
message hides and restores that draft, `/stop` and the composer stop both end
generation, a finished turn is a new rich message, and an unknown send is not
duplicated. Do not place real trades.
