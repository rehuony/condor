"""Trend Watch retains its signal policy while delegating all card behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from routines import trend_watch as scanner
from routines.base import RoutineContext


def signal_frames():
    now = 2_000_000_000
    h1 = pd.DataFrame(
        {
            "timestamp": [now - (101 - i) * 3600 for i in range(100)],
            "open": 99.0,
            "close": [98 + i * 0.02 for i in range(100)],
            "high": 100.0,
            "low": 98.7,
            "ema9": 99.0,
            "ema21": 98.8,
            "ema50": 97.0,
            "atr": 1.0,
            "rsi": 55.0,
        }
    )
    m15 = pd.DataFrame(
        {
            "timestamp": [now - (101 - i) * 900 for i in range(100)],
            "open": 98.8,
            "close": 98.7,
            "high": 99.2,
            "low": 98.5,
            "ema9": 98.9,
            "atr": 0.5,
            "rsi": 54.0,
        }
    )
    m15.loc[99, ["close", "rsi"]] = [99.0, 55.0]
    h4 = h1.copy()
    h4["timestamp"] = [now - (101 - i) * 14400 for i in range(100)]
    h4["ema20"] = 99.0
    return {"h1": h1, "m15": m15, "h4": h4}


def test_scan_preserves_clarity_and_closed_trigger_bar(monkeypatch):
    monkeypatch.setattr(scanner.time, "time", lambda: 2_000_000_000)
    frames = signal_frames()
    result = scanner._scan_pair(scanner.Config(), "SOL-USDT", frames)
    plan = result["plan"]
    assert plan["score"] == 100
    assert plan["bar"] == frames["m15"].timestamp.iloc[-1]
    assert (plan["lo"], plan["hi"], plan["stop"], plan["tp1"], plan["tp2"]) == (
        98.4,
        99.0,
        97.5,
        101.25,
        103.5,
    )
    assert scanner._trade_plan(plan).signal_price == 99
    frames["h4"]["ema20"] = 96.0
    blocked = scanner._scan_pair(scanner.Config(), "SOL-USDT", frames)
    assert blocked["plan"] is None
    assert blocked["blocked"]["plan"]["score"] == 66.7
    assert blocked["gap"]["triggered"]


def test_clarity_components_remain_directional():
    closes = [100.0]
    for step in [1.0] * 15 + [-2.0] * 5:
        closes.append(closes[-1] + step)
    h1 = pd.DataFrame({"close": closes, "ema9": 101.15, "ema50": 100.0, "atr": 1.0})
    h4 = pd.DataFrame({"ema20": [101.0], "ema50": [100.0]})
    assert scanner._clarity(h4, h1, "long")["score"] == 63.3
    assert scanner._clarity(h4, h1, "short")["score"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("send", [False, True])
async def test_run_delegates_publication_and_preview_has_no_side_effects(
    monkeypatch, tmp_path, send
):
    monkeypatch.setattr(scanner.time, "time", lambda: 2_000_000_000)
    pairs = ["SOL-USDT", "ETH-USDT", "BTC-USDT", "DOGE-USDT"]
    client = SimpleNamespace(
        market_data=SimpleNamespace(
            get_tickers=AsyncMock(
                return_value={"tickers": {"binance_perpetual": {"ok": 1}}}
            )
        )
    )
    get_client = AsyncMock(return_value=client)
    monkeypatch.setattr(scanner, "get_client", get_client)
    monkeypatch.setattr(scanner, "_universe", lambda *_: pairs)
    monkeypatch.setattr(
        scanner, "_fetch_pair", AsyncMock(side_effect=lambda *_: signal_frames())
    )
    publish = AsyncMock(
        side_effect=lambda plan, *_, **__: (
            SimpleNamespace(plan=plan, status="PROPOSED")
            if plan.trading_pair != "ETH-USDT"
            else None
        )
    )
    monkeypatch.setattr(scanner.trade_cards, "list_cards", Mock(return_value=[]))
    render = Mock(return_value=b"preview")
    monkeypatch.setattr(scanner.trade_cards, "publish", publish)
    monkeypatch.setattr(scanner.trade_cards, "render_chart", render)
    monkeypatch.setattr(scanner.paths, "data_dir", lambda: tmp_path)
    report = Mock()
    report.save = AsyncMock()
    monkeypatch.setattr(scanner, "ReportBuilder", Mock(return_value=report))
    context = RoutineContext(
        chat_id=-100,
        owner_id=7,
        instance_id="test",
        user_data={},
        bot=SimpleNamespace(),
        server_name="pinned-server",
    )
    config = scanner.Config(send=send, mode="mock", max_same_side=2)
    result = await scanner.run(config, context)
    assert result.silent
    get_client.assert_awaited_once_with(-100, user_data={}, server="pinned-server")
    report.save.assert_awaited_once()
    scanner.ReportBuilder.assert_called_once_with("Trend Watch · Binance Perp")
    if send:
        # SOL and BTC are sent, ETH's signal was already reserved, and DOGE
        # would be a third long card while two is the configured limit.
        assert publish.await_count == 3
        assert "Result: 2 cards" in result.text
        assert "ETH Long: a card was already sent for this trigger bar" in result.text
        assert "DOGE Long: 2 long cards are already active" in result.text
        assert publish.await_args.kwargs["owner_id"] == 7
        assert publish.await_args.kwargs["chat_id"] == -100
        assert publish.await_args.kwargs["server_name"] == "pinned-server"
        assert publish.await_args.kwargs["mode"] == "mock"
        assert publish.await_args.kwargs["signal_id"].startswith(
            "trend_watch:pullback:1H:"
        )
        render.assert_not_called()
        assert not list(tmp_path.rglob("*.png"))
    else:
        publish.assert_not_awaited()
        assert render.call_count == 4
        assert "Result: 4 cards" in result.text
        assert len(list(tmp_path.rglob("*.png"))) == 4


def test_config_keeps_only_tunable_parameters():
    assert list(scanner.Config.model_fields) == [
        "top_n",
        "min_quote_volume",
        "min_score",
        "pullback_atr",
        "stop_atr",
        "entry_atr",
        "tp1_r",
        "tp2_r",
        "max_same_side",
        "mode",
        "send",
    ]


def test_no_signal_summary_groups_each_pairs_missing_steps():
    r = {
        "base": "BTC",
        "last": 84412.8,
        "prog": {"side": "long"},
        "clar": {"score": 100},
        "gap": {
            "steps": ["pull back to near EMA21", "15m green close above EMA9"],
            "triggered": False,
        },
    }
    text = scanner._summary(
        "Trend scan · 40 pairs · 6s",
        "Result: no qualified signal; still waiting",
        [
            ("Closest to a card (by clarity)", scanner._closest_lines(r)),
            ("Not scanned", []),
        ],
    )
    assert text == (
        "Trend scan · 40 pairs · 6s\n"
        "Result: no qualified signal; still waiting\n\n"
        "Closest to a card (by clarity)\n"
        "• BTC 84412.80 · 1H bullish · clarity 100\n"
        "  ① pull back to near EMA21\n"
        "  ② 15m green close above EMA9"
    )


def test_fields_carry_readable_titles_for_the_config_form():
    from routines.base import get_routine

    fields = get_routine("trend_watch").get_fields()
    assert fields["pullback_atr"]["title"] == "Pullback (×ATR1H)"
    assert fields["send"]["title"] == "Publish cards"


def test_a_pair_and_side_waits_for_its_previous_card():
    plan = {"pair": "SOL-USDT", "side": "long"}
    card = lambda pair, side: SimpleNamespace(  # noqa: E731
        plan=SimpleNamespace(trading_pair=pair, side=side)
    )
    config = scanner.Config()
    assert "still active" in scanner._limited(plan, [card("SOL-USDT", "long")], config)
    assert scanner._limited(plan, [card("SOL-USDT", "short")], config) is None
    two = [card("BTC-USDT", "long"), card("ETH-USDT", "long")]
    assert "2 long cards" in scanner._limited(plan, two, config)
