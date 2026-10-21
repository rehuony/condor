"""Lightweight Telegram output recorder for runtime event tests."""

from types import SimpleNamespace

from condor.runtime.events import EventType, RuntimeEvent
from condor.telegram.format import plain_text


def message_text(kwargs):
    rich = kwargs.get("rich_message")
    if getattr(rich, "html", None) is not None:
        return plain_text(rich.html)
    return getattr(rich, "markdown", None) or kwargs.get("text")


class Bot:
    def __init__(self):
        self.messages = []
        self.edits = []
        self.deleted = []
        self.actions = []
        self.send_failure = None
        self.edit_failure = None
        self.current = {}

    async def send_message(self, **kwargs):
        if self.send_failure:
            raise self.send_failure
        self.messages.append(kwargs)
        message_id = 100 + len(self.messages)
        self.current[message_id] = kwargs
        return SimpleNamespace(message_id=message_id)

    async def send_rich_message(self, **kwargs):
        if self.send_failure:
            raise self.send_failure
        payload = dict(kwargs)
        payload["text"] = message_text(kwargs)
        self.messages.append(payload)
        message_id = 100 + len(self.messages)
        self.current[message_id] = payload
        return SimpleNamespace(message_id=message_id)

    async def edit_message_text(self, **kwargs):
        if self.edit_failure:
            raise self.edit_failure
        payload = dict(kwargs, text=message_text(kwargs))
        self.edits.append(payload)
        self.current[kwargs["message_id"]] = payload
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
