"""The three controller wizards share steps but keep their own callbacks and state."""

import asyncio
from types import SimpleNamespace

import pytest

from handlers.bots.controller_handlers import pmm_mister, pmm_v1
from handlers.bots.controller_handlers.grid_strike import steps as gs_steps


class _Message:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def _update():
    message = _Message()
    query = SimpleNamespace(message=message, from_user=SimpleNamespace(id=7))
    return (
        SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=7)),
        message,
    )


def _context(user_data):
    return SimpleNamespace(user_data=user_data, bot=None)


def _buttons(markup):
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


@pytest.mark.parametrize(
    "show, state, pair_prefix, back",
    [
        (
            pmm_mister._show_pmm_wizard_pair_step,
            "pmm",
            "bots:pmm_pair:",
            "bots:pmm_back:connector",
        ),
        (
            pmm_v1._show_pv1_wizard_pair_step,
            "pv1",
            "bots:pv1_pair:",
            "bots:pv1_back:connector",
        ),
        (
            gs_steps._show_wizard_pair_step,
            "gs",
            "bots:gs_pair:",
            "bots:gs_back_to_connector",
        ),
    ],
)
def test_pair_step_keeps_each_wizards_state_and_callbacks(
    show, state, pair_prefix, back
):
    update, message = _update()
    user_data = {
        "controller_config_params": {"connector_name": "binance_perpetual"},
        "controller_configs_list": [
            {"trading_pair": "BTC-USDT"},
            {"trading_pair": "ETH-USDT"},
            {"trading_pair": "BTC-USDT"},
        ],
    }
    asyncio.run(show(update, _context(user_data)))

    assert user_data["bots_state"] == f"{state}_wizard_input"
    assert user_data[f"{state}_wizard_step"] == "trading_pair"
    text, kwargs = message.edits[0]
    buttons = _buttons(kwargs["reply_markup"])
    assert buttons[:2] == [
        ("BTC-USDT", f"{pair_prefix}BTC-USDT"),
        ("ETH-USDT", f"{pair_prefix}ETH-USDT"),
    ]
    assert buttons[-2:] == [("« Back", back), ("↩️ Cancel", "bots:main_menu")]
    assert "binance\\_perpetual" in text


def test_grid_strike_pair_prompt_counts_steps_for_perpetuals():
    update, message = _update()
    user_data = {"controller_config_params": {"connector_name": "binance_perpetual"}}
    asyncio.run(gs_steps._show_wizard_pair_step(update, _context(user_data)))
    assert "Step 2/6" in message.edits[0][0]

    update, message = _update()
    user_data = {"controller_config_params": {"connector_name": "binance"}}
    asyncio.run(gs_steps._show_wizard_pair_step(update, _context(user_data)))
    assert "Step 2/5" in message.edits[0][0]


def test_pair_suggestions_edit_the_wizard_message_with_wizard_callbacks():
    edits = []

    class _Bot:
        async def edit_message_text(self, **kwargs):
            edits.append(kwargs)

    user_data = {"pmm_wizard_message_id": 11, "pmm_wizard_chat_id": 7}
    context = SimpleNamespace(user_data=user_data, bot=_Bot())
    update, _ = _update()
    asyncio.run(
        pmm_mister._show_pmm_pair_suggestions(
            update, context, "BTCUSDT", "Pair not found", ["BTC-USDT"], "binance"
        )
    )

    assert edits[0]["chat_id"] == 7 and edits[0]["message_id"] == 11
    assert _buttons(edits[0]["reply_markup"]) == [
        ("📈 BTC-USDT", "bots:pmm_pair_select:BTC-USDT"),
        ("« Back", "bots:pmm_back:connector"),
        ("↩️ Cancel", "bots:main_menu"),
    ]
