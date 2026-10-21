# Telegram agent reply delivery

Private-chat requests use ordinary messages edited in place. A nonempty receipt
appears before preparation waits, model startup, or attachment work. The same
request owns its message segments until final delivery. Input contracts are in
[Telegram conversations](telegram-conversations.md).

## Request lifecycle and ownership

```text
authorize and route → reserve identity, deduplicate, reserve FIFO → receipt
→ preparation and album collection → session and attachments
→ durable acceptance and conversation queue → model, tools and approvals
→ segmented output → completion or cancellation → in-place formatting
```

- `ingress.reserve` is synchronous: it checks capacity, deduplicates and reserves
  preparation order. `admit` waits asynchronously. Album followers, commands,
  forms and duplicate updates do not create conversational receipts.
- `RichReplyStreamer` owns one request's fixed user, chat, conversation and request
  identity, phase, accumulated body, known message IDs and cancellation state.
  Menu selection cannot rebind it to another conversation.
- `presentation.py` supplies pure segment and formatting decisions. `SegmentBuffer`
  keeps contiguous source ranges, preferring paragraph and newline boundaries.
  Each segment is at most 3500 UTF-16 units; supplementary Unicode characters
  remain intact. Once a segment is sealed, subsequent text cannot move its range.
- `ChatWriter` serializes network updates per chat, without reserving the chat for
  an entire turn. Waiting updates render their latest snapshot after the send
  gate opens. Different requests can show receipts while an earlier one executes.
- The runtime owns durable acceptance, conversation FIFO, authorization, provider
  execution and history. Execution completion and delivery completion are separate.
  A delivery failure never causes model execution to repeat.
- `TelegramWakeSink` uses the same presenter and writer. Its stable request
  identity is bound before durable acceptance; cancelling that reply does not
  cancel the background producer that triggered it.

## Content and status

Previews are literal text. The current segment appends a short status on a separate
line and carries **Stop and Clear Queue**. Earlier segments retain their source
text without a status or button. The body always accumulates; tools, heartbeats and
approvals never replace it. Pending approval takes priority over tool activity.
The status shows only the current action and a short target, such as
`Reading input.py` or `Fetching BTC-USDT prices`. Known tools and explicit kinds
select the action; allowlisted fields supply a basename, hostname, trading pair
or service. Multiple targets use a general label. Arbitrary titles, commands,
code, raw parameters, tool output and reasoning are not shown.

Tool identity is separate from its display title. Partial updates retain known
metadata, and the most recently changed active tool supplies the status. Pending
calls say `Preparing to …` before authorization; blocked, failed and cancelled
calls leave the active set. Fast calls may coalesce directly into
`Processing tool results…`, followed by `Writing response…` when text resumes.
There is no step history, minimum display duration or replay of stale phases.
Heartbeats cannot replace the current phase or confirmation prompt.

Normal updates start at least one second apart per chat. Unchanged messages are
not edited. Short-lived phases coalesce into the latest state rather than forming
an animation queue. `RetryAfter` extends the chat's send gate, including final
updates and new receipts.
An unchanged snapshot returns before waiting for a send slot. Final delivery
returns after the last acknowledgement without waiting for another interval.

Completion waits for any in-flight preview before making terminal edits. Existing
message IDs, input quotations and segment boundaries are retained. Safe complete
Markdown blocks pass through `format.render_rich_reply`, which reuses the existing
Markdown and BeautifulSoup pipeline for compact headings, paragraph spacing,
nested lists, mobile table labels and links. Rich HTML uses explicit line breaks
and indentation; inline code, escaped markers, strikethrough, spoilers and
highlighting retain their meaning. Preformatted blocks longer than 12
lines or 900 UTF-16 units use a native expandable details block with the full
literal content inside. Short code stays visible; real language labels remain,
while generic text-language badges are omitted.

Final edits use `InputRichMessage(html=...)`. Compiled HTML is kept separately from
the original source and is never used as a plain fallback. Formatting that would
overflow a single message, incomplete or cross-segment fences and tables remain
literal. Parser rejection falls back to the original text on the same message.
No terminal repagination or delete-and-resend cycle occurs. Voice transcript
prefixes remain part of the segmented source.
Failure and cancellation notices follow the preserved partial body.

## Stop and approval contracts

Inline Stop validates the sender, chat, request token and originating message ID.
It and `/stop` synchronously snapshot the chat's registered Telegram requests and
mark them stopped before awaiting any network or provider operation. Preparing
owners are cancelled; accepted requests use runtime batch cancellation by stable
`(user_id, conversation_id, request_id)`. Queued requests wake independently of
an active turn. New inputs arriving after that snapshot continue normally.

Completed execution retires the Stop target before final delivery. Repeated or
stale buttons cannot cancel a later request. Stop freezes displayed body text;
provider settlement still records executed tools, partial results and usage.
Dashboard requests and background tasks themselves are outside the chat snapshot.
The runtime's `abort_turn` remains an exact active-turn-only operation.

Approvals carry their owning request identity. Stop denies only those requests'
confirmations. Late approvals for a stopped request cannot reopen it. Approval,
rejection, timeout and cancellation schedule keyboard cleanup; central registry
resolution does not depend on successful Telegram editing.

## Delivery outcomes and shutdown

`finalize()` returns `DeliveryResult`: `status`, source `payloads`, terminally
`confirmed` segment count and known `message_ids`. Status is `pending`, `complete`,
`failed`, `partial` or `delivery_unknown`. Model completion alone does not prove
successful message delivery.

An unchanged edit is success. A definitely missing message permits a replacement.
Unknown edits may be retried against the same ID. An ambiguous new send (including
a missing acknowledgement) is never blindly repeated. A connection failure known
to precede transmission can be retried. Retry attempts are bounded, and all
rate-limit waits share the chat gate. Existing confirmed text remains visible
when final delivery fails; the saved conversation is available in the dashboard.

Shutdown stops intake and preparation, stops producers and execution, drains
approval cleanup, closes presentation workers, then closes the bot transports.
There is no native-draft ID registry, empty placeholder, keepalive, recovery
middleware, native composer Stop route, or whole-turn output lease.

Notifications outside conversational output continue to use `send_text`:
standard Markdown prefers rich messages, with the existing HTML fallback. Menus
retain their MarkdownV2 contract.

## Timing diagnostics

Content-free INFO logs correlate stages by `request_id`. Reply milestones record
elapsed milliseconds from receipt: durable deduplication, preparation admission,
session startup, input readiness, acceptance, the first execution/text events, first send
attempt/acknowledgement, first body acknowledgement, execution completion and
final delivery. Each milestone is logged once. The human-input handler also
records release of the request; wake replies share the same delivery milestones.
Unavailable stages are omitted, not reported as zero.

Each actual send/edit logs lock wait, requested throttle/server-cooldown wait and
network duration. Overlapping gates count as server cooldown, so waits are not
double-counted. No-op updates do not consume a send slot. Logs contain only request
identity, fixed stage/operation/outcome labels and timings, never message text or
tool arguments. An API acknowledgement measures transport delivery, not when the
phone renders it. These measurements distinguish application overhead from model
and network latency; they do not imply a faster model or a higher Telegram limit.

## Verification

Automated tests cover early receipts, duplicate suppression, queue cancellation,
slow delivery, late events, approval races, Unicode segmentation, incomplete
Markdown and delivery failures. Real-device acceptance is also required: on the
owner's iPhone check first status, whole-message updates, tool/approval phases,
long replies retaining all segments, Stop clearing the existing queue, a later
new message succeeding, and final in-place formatting. Automated tests cannot
establish Telegram client animation or layout behavior. Do not place real trades.

API reference: [editMessageText](https://core.telegram.org/bots/api#editmessagetext).
