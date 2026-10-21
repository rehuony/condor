# Telegram conversations

Send a message to Condor in a private chat. Text and attachments use the same
conversation, model, permissions, durable history, and execution queue as the
dashboard. A model must support image input to inspect pictures.

## Supported input

| Input | Behavior |
| --- | --- |
| Text | Sent as the user's question, with its original whitespace. |
| Rich formatted message | Converts paragraphs, lists, tables, quotes, and code into text, including in replies and active forms. Rich messages containing media blocks are refused as a whole. |
| Photo, with or without a caption | Downloads one suitable photo variant and sends the actual image pixels alongside the caption. |
| Image file or static sticker | Validates PNG, JPEG, GIF, or WebP content before sending it to the model. |
| Photo album | Collects the album into one ordered turn with its captions and images. |
| Reply to a message | Includes the immediate original text and supported attachments. Replying to a picture reattaches its pixels; a thumbnail or historical attachment ID is not sufficient. External reply media is included when Telegram exposes it. |
| Voice, audio, or audio file | Transcribes locally using the selected voice preferences; the answer includes a sanitized transcript. |
| UTF-8 text document | Reads `.txt`, `.md`, `.csv`, `.tsv`, `.json`, `.yaml`, `.yml`, or `.log` as text context. |

PDFs, office documents, videos, animated stickers/images, and other unsupported
content receive an explicit explanation. They do not silently become text-only
questions. URLs in captions or quoted text are not downloaded as attachments.
Only new private messages are conversational input; edits, channel posts, and
service messages do not rerun the assistant.

## Limits and failures

| Resource | Limit |
| --- | --- |
| Attachments, including replied-to media | 4 per turn |
| Image | 5 MiB and 25 million pixels |
| Audio | 20 MiB and 10 minutes of actual decoded audio |
| Text document | 128 KiB, strict UTF-8 |
| All attachment downloads combined | 20 MiB per turn |
| Question, quotes, document text, and transcripts combined | 32,768 characters |
| In-flight conversational turns | 4 per chat, 16 process-wide |

Downloads stream into bounded buffers, share a connection pool, and have a
45-second deadline including waiting for a download slot. At most four downloads
and two image decodes run concurrently. Speech processing admits one worker and
keeps one lazily loaded Whisper model resident. Text/image-only use does not load
Whisper. Audio decoding cannot open secondary local files or network resources.

An invalid, missing, oversized, or unsupported attachment rejects the entire
turn before model execution. A failed image is never omitted while the model
answers about the remaining text. Unaccepted saved images are cleaned up;
accepted images belong to their conversation and are deleted with it.

Telegram supplies album members as separate updates with no end marker. Condor
waits for 350 ms of quiet, up to 1.2 seconds from the first member. A member
arriving after the album is sealed cannot start a second partial request; Condor
asks for a new complete album. Split albums larger than four attachments before
sending them.

## Ordering, controls, and forms

- Each chat reserves preparation order before downloads or transcription, so a
  later text message cannot overtake an earlier picture. Once accepted, execution
  belongs to the runtime's conversation queue; preparation does not hold the
  model lock.
- Telegram message IDs and album IDs identify turns. In-flight and recent
  duplicates are suppressed; retained conversation history prevents accepted
  turns from running again after restart, including in a different selected chat.
- `/stop` and inline **Stop and Clear Queue** cancel preparation, accepted queued
  requests and running Telegram replies registered in that chat when Stop arrives.
  New inputs received afterwards remain eligible to run.
  Native image/audio work that has already started finishes under its resource
  limit; cancellation does not release its worker capacity early.
- A plain `-`, including in a rich formatted paragraph, resets the conversation
  even when that text replies to another message. Captions and transcriptions
  cannot accidentally invoke this control.
- Actual trading/configuration forms retain priority and require text. Attachments
  or replies containing attachments receive a `/cancel` hint without changing
  the form. The explicit bot configuration upload flow still accepts its file.
  Authorized active forms and configuration uploads also work in groups and
  supergroups; ordinary group messages never enter the assistant. Merely opening
  a navigation menu does not swallow ordinary private-chat messages.
- If access or the selected conversation changes while attachments are being
  prepared, the input is refused rather than attached to another conversation.
  Access is checked again before a queued turn starts model execution.

## Agent replies

Replies begin with a nonempty ordinary message before model startup or attachment
preparation. Literal body text accumulates in that message; status appears on its
last line. Long answers keep earlier segments and continue in a new one, with a
3500 UTF-16-unit body budget per segment. Completed segments lose their status and
Stop button. At completion, safe Markdown is formatted in place without changing
segment boundaries. Cross-segment code and tables remain literal. Raw reasoning
and tool logs are hidden. Sanitized voice transcripts remain in the answer,
including partial or failed responses.

Stop preserves the conversation and partial answer. It does not undo completed
actions or stop trading. Unknown new-message delivery is not blindly retried;
the saved conversation remains available in the dashboard.

See [Telegram agent reply delivery](telegram-reply-delivery.md) for request
ownership, notification behavior, delivery guarantees and verification.

## Implementation and lifecycle

`handlers/messages.py` owns dispatch and form priority. `condor/telegram/input.py`
normalizes messages and resolves bounded media. `condor/telegram/ingress.py` owns
album collection, admission, and preparation cancellation. The agent handler
creates the existing `PromptRequest` and consumes runtime events for delivery.
`condor.runtime` remains responsible for durable acceptance, conversation ordering,
model capability checks, and history.

Tasks, locks, binary payloads, and HTTP clients stay outside Telegram persistence
and outside reloadable handler modules. Shutdown stops polling and admission,
cancels handler work, and drains actual decoder/transcriber workers. Execution,
approvals and output workers are cleaned up before the bot transport closes.
Closed services cannot be lazily reopened by a late update.

Restart Condor when changing handler registration or startup/shutdown lifecycle.

The transport contracts follow the [Telegram Bot API Message model](https://core.telegram.org/bots/api#message),
[getFile download API](https://core.telegram.org/bots/api#getfile), and
[aiogram dispatcher polling lifecycle](https://docs.aiogram.dev/en/latest/dispatcher/dispatcher.html).
