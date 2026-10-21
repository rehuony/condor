"""Chinese captions and inline controls for a single photo message."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape

from condor.telegram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .models import HIDDEN, Card, CardStatus, management_actions, recovery_actions


def venue_label(connector_name: str) -> str:
    """``binance_perpetual`` → ``Binance Perp``, as the dashboard's
    ``formatConnectorName`` names it."""
    name = re.sub(r"_perpetual$", " perp", connector_name).replace("_", " ")
    return re.sub(r"\b[a-z]", lambda m: m.group().upper(), name)


def number(value, signed: bool = False) -> str:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return "—"
    except (InvalidOperation, TypeError):
        return "—"
    text = format(amount, ".6f").rstrip("0").rstrip(".") or "0"
    return ("+" if signed and amount > 0 else "") + text


def result_row(card: Card) -> str:
    info = card.execution
    reason = str(info.get("close_reason") or "").upper()
    label = {
        "TAKE_PROFIT": "止盈",
        "TP1": "止盈1",
        "TP2": "止盈2",
        "STOP_LOSS": "止损",
        "TRAILING_STOP": "保本跟随",
        "MANUAL": "手动平仓",
        "MANUAL_CLOSE": "手动平仓",
        "CLOSED": "已平仓",
        "CANCELLED": "已撤单",
        "CANCELED": "已撤单",
    }.get(reason, "已平仓")
    if reason == "TAKE_PROFIT":
        targets = {
            str(leg.get("number") or str(leg.get("level_id", "")).rsplit(":", 1)[-1])
            for leg in info.get("legs", [])
            if str(leg.get("close_reason", "")).upper() == "TAKE_PROFIT"
        }
        label = "止盈2" if "2" in targets else "止盈1" if "1" in targets else "止盈"
    pnl = info.get("pnl")
    if info.get("pnl_complete") is False or pnl is None:
        return f"{label} · 盈亏待核对"
    try:
        risk = Decimal(str(card.quote.get("max_loss", 0)))
        multiple = f"（{number(Decimal(str(pnl)) / risk, True)}R）" if risk > 0 else ""
        icon = "✅" if Decimal(str(pnl)) >= 0 else "❌"
    except InvalidOperation:
        icon, multiple = "✅", ""
    return f"{icon} {label} · {number(pnl, True)} USDT{multiple}"


def tree(title: str, rows: list[tuple[str, str]]) -> list[str]:
    """A bold section header followed by indented ``label value`` rows."""
    lines = [f"<b>{title}</b>"]
    for i, (label, value) in enumerate(rows):
        branch = "└" if i == len(rows) - 1 else "├"
        lines.append(f"{branch} {label}  <b>{escape(value)}</b>")
    return lines


def caption(card: Card) -> str:
    """Telegram HTML caption: title, plan, then the block for the card's status."""
    plan = card.plan
    side = "🟢 做多" if plan.side == "long" else "🔴 做空"
    lines = [
        f"<b>{escape(plan.trading_pair)} · {side} · {escape(plan.timeframe)}</b>",
        "<i>演练 · 不下真实订单</i>" if card.mode == "dry_run" else "<b>⚡ 实盘</b>",
        "",
        *tree(
            "📍 交易计划",
            [
                ("进场", f"{number(plan.entry_low)} – {number(plan.entry_high)}"),
                ("止损", number(plan.stop_price)),
                ("止盈", f"{number(plan.tp1_price)} / {number(plan.tp2_price)}"),
            ],
        ),
        "",
    ]
    notes: list[str] = []
    if card.status == CardStatus.PROPOSED:
        expires = datetime.fromtimestamp(card.expires_at, timezone(timedelta(hours=8)))
        rows = [("有效至", f"{expires:%m-%d %H:%M}（UTC+8）")]
        if plan.score is not None:
            rows.insert(0, ("清晰度", f"{number(plan.score)}/100"))
        lines += tree("⏳ 待确认", rows)
        notes.append("选择投资金额后查看风险，确认后才会下单。")
    elif card.status == CardStatus.SIZING:
        lines += tree(
            "💰 选择保证金",
            [("杠杆", "10x 逐仓"), ("上次", f"{number(card.margin)} USDT")],
        )
        if card.custom_input:
            notes.append("请发送自定义保证金金额；/cancel 返回金额选择。")
    elif card.status == CardStatus.REVIEW:
        q = card.quote
        entry = "市价" if q.get("entry_method") == "MARKET" else "限价"
        lines += tree(
            "🧮 下单确认",
            [
                ("入场", f"{entry} · 参考价 {number(q.get('entry_price'))}"),
                ("数量", number(q.get("quantity"))),
                ("保证金", f"{number(q.get('margin'))} USDT · 10x"),
                ("仓位价值", f"{number(q.get('notional'))} USDT"),
                ("止损亏损", f"{number(q.get('max_loss'))} USDT"),
                ("预计强平价", number(q.get("liquidation_price"))),
                (
                    "止盈预计",
                    f"{number(q.get('profit_tp1'), True)} / "
                    f"{number(q.get('profit_tp2'), True)} USDT",
                ),
            ],
        )
        notes += [
            "各一半止盈；余仓到止盈1后按 1R 保本跟随，不设持仓时限。",
            "盈亏估计含开平仓手续费；滑点和资金费会影响实际结果。",
        ]
    elif card.status == CardStatus.OPEN:
        e = card.execution
        rows = [
            ("成交价", number(e.get("entry_price"))),
            ("剩余数量", number(e.get("remaining_amount"))),
            ("现价", number(e.get("current_price"))),
            ("盈亏", f"{number(e.get('pnl'), True)} USDT"),
        ]
        liquidation = e.get("liquidation_price")
        if e.get("liquidation_price_kind") == "exchange" and liquidation is not None:
            rows.append(("交易所强平价", number(liquidation)))
        elif card.mode == "live":
            notes.append("交易所强平价暂不可用。")
        try:
            price = Decimal(str(e["current_price"]))
            distance = abs(price - plan.stop_price) / price * 100
            rows.append(("距计划止损", f"{distance:.2f}%"))
        except (KeyError, InvalidOperation, ZeroDivisionError):
            pass
        lines += tree("📈 持仓中", rows)
        if e.get("pnl_complete") is False:
            notes.insert(0, "盈亏数据尚未完整核对。")
    elif card.status == CardStatus.CLOSED:
        lines.append(f"<b>{escape(result_row(card))}</b>")
    elif card.status == CardStatus.PENDING:
        lines.append("<b>🕒 限价委托等待成交</b>")
        notes.append("到期自动撤销未成交部分。")
    elif card.status == CardStatus.FAILED:
        lines.append("<b>❌ 交易未完成</b>")
        notes.append("请检查执行端状态。")
    elif card.status == CardStatus.RECONCILING:
        lines.append("<b>🔍 正在核对执行结果</b>")
        notes.append("禁止新增仓位或重复提交操作。")
        if recovery_actions(card):
            notes.append("已确认的持仓仍可全部平仓；已有入场挂单仍可撤销。")
    elif card.status == CardStatus.CLOSING:
        lines.append("<b>⏳ 正在处理持仓操作</b>")
        notes.append("请等待执行结果。")
    elif card.status == CardStatus.PLACING:
        lines.append("<b>⏳ 正在提交订单</b>")
        notes.append("请勿重复操作。")
    elif card.status == CardStatus.DETACHED:
        lines.append("<b>⏹ 已停止托管</b>")
        notes.append("持仓保留；此卡片不再管理订单。")
    elif card.status in HIDDEN:
        lines.append("<b>卡片已结束</b>")
    if card.delete_failed:
        notes.append("消息暂未删除，已停用所有按钮，将继续重试删除。")
    if card.note:
        notes.append(card.note[:240])
    lines += [f"<i>{escape(note)}</i>" for note in notes]
    if card.confirm_action:
        label = {
            "reduce_half": "平掉一半剩余持仓",
            "close": "全部平仓",
            "detach": "停止托管、保留持仓并删除卡片",
        }[card.confirm_action]
        lines += ["", f"<b>⚠️ 再次点击确认：{label}</b>"]
    return "\n".join(lines)


def keyboard(card: Card) -> InlineKeyboardMarkup | None:
    def button(text: str, action: str):
        return InlineKeyboardButton(text=text, callback_data=f"tc:{action}:{card.id}")

    if card.status in HIDDEN:
        return None
    actions = management_actions(card)
    if card.status == CardStatus.PROPOSED:
        rows = [[button("❌ 拒绝", "reject"), button("💰 投资", "invest")]]
    elif card.status == CardStatus.SIZING:
        rows = [
            [
                button(
                    f"{'✓ ' if card.margin == amount else ''}{amount} USDT",
                    f"m{amount}",
                )
                for amount in ("10", "20", "50")
            ],
            [button("✏️ 自定义", "custom"), button("↩️ 返回", "back")],
        ]
        if card.margin not in {"10", "20", "50"}:
            rows.insert(1, [button(f"上次：{number(card.margin)} USDT", "last")])
    elif card.status == CardStatus.REVIEW:
        rows = [[button("✅ 确认下单", "confirm"), button("↩️ 修改金额", "invest")]]
    elif card.status == CardStatus.PENDING:
        row = [button("🔄 刷新", "refresh")]
        if "cancel_entry" in actions:
            row.insert(0, button("🛑 撤单", "cancel_entry"))
        rows = [row]
    elif card.confirm_action and card.confirm_action in actions:
        rows = [
            [
                button("✅ 确认操作", f"yes_{card.confirm_action}"),
                button("↩️ 返回", "back"),
            ]
        ]
    elif card.status == CardStatus.OPEN:
        pnl = number(card.execution.get("pnl"), True)
        rows = []
        close_buttons = [
            button(text, action)
            for text, action in (
                (f"平一半 · {pnl}", "reduce_half"),
                (f"全部平仓 · {pnl}", "close"),
            )
            if action in actions
        ]
        if close_buttons:
            rows.append(close_buttons)
        if "cancel_entry" in actions:
            rows.append([button("🛑 撤销入场余单", "cancel_entry")])
        row = [button("🔄 刷新", "refresh")]
        if "detach" in actions:
            row.insert(0, button("⏹ 停止托管", "detach"))
        rows.append(row)
    elif card.status == CardStatus.CLOSED:
        rows = [[button(result_row(card), "noop")]]
    elif card.status == CardStatus.FAILED:
        rows = [[button("❌ 交易未完成", "noop")]]
    elif card.status == CardStatus.RECONCILING and recovery_actions(card):
        actions = recovery_actions(card)
        rows = [[button("🔄 核对状态", "refresh")]]
        if "close" in actions:
            rows.insert(0, [button("全部平仓", "close")])
        if "cancel_entry" in actions:
            rows.insert(0, [button("撤销入场挂单", "cancel_entry")])
    else:
        rows = [[button("🔄 核对状态", "refresh")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)
