"""Lightweight Telegram output recorder for runtime event tests."""

from types import SimpleNamespace

from condor.runtime.events import EventType, RuntimeEvent


def message_text(kwargs):
    rich = kwargs.get("rich_message")
    return getattr(rich, "markdown", None) or kwargs.get("text")


class Bot:
    def __init__(self):
        self.messages = []
        self.drafts = []
        self.edits = []
        self.deleted = []
        self.actions = []
        self.draft_failure = None
        self.send_failure = None

    async def send_message(self, **kwargs):
        if self.send_failure:
            raise self.send_failure
        self.messages.append(kwargs)
        return SimpleNamespace(message_id=100 + len(self.messages))

    async def send_rich_message(self, **kwargs):
        if self.send_failure:
            raise self.send_failure
        payload = dict(kwargs)
        payload["text"] = message_text(kwargs)
        self.messages.append(payload)
        return SimpleNamespace(message_id=100 + len(self.messages))

    async def send_rich_message_draft(self, **kwargs):
        if self.draft_failure:
            raise self.draft_failure
        payload = dict(kwargs)
        payload["text"] = message_text(kwargs)
        self.drafts.append(payload)
        return True

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)
        return True

    async def delete_message(self, **kwargs):
        self.deleted.append(kwargs)
        return True

    async def send_chat_action(self, **kwargs):
        self.actions.append(kwargs)
        return True

    async def edit_message_reply_markup(self, **kwargs):
        return True


async def feed(target, text):
    await target.process_event(RuntimeEvent(type=EventType.TEXT, data={"text": text}))
