"""Chinese captions and inline controls for a single photo message."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from condor.telegram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .models import HIDDEN, Card, CardStatus, management_actions, recovery_actions


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


def caption(card: Card) -> str:
    plan = card.plan
    mode = "演练 · 不下真实订单" if card.mode == "dry_run" else "实盘"
    lines = [
        f"{plan.trading_pair} · {'做多' if plan.side == 'long' else '做空'} · {plan.timeframe}",
        mode,
        f"进场 {number(plan.entry_low)}–{number(plan.entry_high)}",
        f"止损 {number(plan.stop_price)} · 止盈 {number(plan.tp1_price)} / {number(plan.tp2_price)}",
    ]
    if card.status == CardStatus.PROPOSED:
        if plan.score is not None:
            lines.append(f"清晰度 {number(plan.score)}/100")
        expires = datetime.fromtimestamp(card.expires_at, timezone(timedelta(hours=8)))
        lines.append(f"有效至 {expires:%m-%d %H:%M}（UTC+8）· 4 小时")
        lines.append("选择投资金额后查看风险，确认后才会下单。")
    elif card.status == CardStatus.SIZING:
        lines.append(f"选择保证金（USDT）· 10x · 上次 {number(card.margin)}")
        if card.custom_input:
            lines.append("请发送自定义保证金金额；/cancel 返回金额选择。")
    elif card.status == CardStatus.REVIEW:
        q = card.quote
        lines.extend(
            [
                f"数量 {number(q.get('quantity'))} · 保证金 {number(q.get('margin'))} USDT · 10x",
                f"仓位价值 {number(q.get('notional'))} USDT",
                f"按止损价估算亏损 {number(q.get('max_loss'))} USDT · 预计强平价 {number(q.get('liquidation_price'))}",
                f"止盈1预计 {number(q.get('profit_tp1'), True)} / 止盈2预计 {number(q.get('profit_tp2'), True)} USDT",
                "盈亏估计含开平仓手续费；滑点和资金费会影响实际结果。",
                f"入场：{'市价' if q.get('entry_method') == 'MARKET' else '限价'} · 参考价 {number(q.get('entry_price'))}",
                "各一半止盈；余仓到止盈1后按 1R 保本跟随，不设持仓时限。",
            ]
        )
    elif card.status == CardStatus.OPEN:
        e = card.execution
        lines.extend(
            [
                f"已开仓 · 成交 {number(e.get('entry_price'))} · 剩余 {number(e.get('remaining_amount'))}",
                f"盈亏 {number(e.get('pnl'), True)} USDT · 现价 {number(e.get('current_price'))}",
            ]
        )
        if e.get("pnl_complete") is False:
            lines.append("盈亏数据尚未完整核对。")
        liquidation = e.get("liquidation_price")
        if e.get("liquidation_price_kind") == "exchange" and liquidation is not None:
            lines.append(f"交易所强平价 {number(liquidation)}")
        elif card.mode == "live":
            lines.append("交易所强平价暂不可用。")
        try:
            price = Decimal(str(e["current_price"]))
            distance = abs(price - plan.stop_price) / price * 100
            lines.append(f"距计划止损 {number(distance)}%")
        except (KeyError, InvalidOperation, ZeroDivisionError):
            pass
    elif card.status == CardStatus.PENDING:
        lines.append("限价委托等待成交，到期自动撤销未成交部分。")
    elif card.status == CardStatus.CLOSED:
        lines.append(result_row(card))
    elif card.status == CardStatus.FAILED:
        lines.append("❌ 交易未完成，请检查执行端状态。")
    elif card.status == CardStatus.RECONCILING:
        lines.append("正在核对执行结果，禁止新增仓位或重复提交操作。")
        if recovery_actions(card):
            lines.append("已确认的持仓仍可全部平仓；已有入场挂单仍可撤销。")
    elif card.status == CardStatus.CLOSING:
        lines.append("正在处理持仓操作，请等待执行结果。")
    elif card.status == CardStatus.PLACING:
        lines.append("正在提交订单，请勿重复操作。")
    elif card.status == CardStatus.DETACHED:
        lines.append("已停止托管，持仓保留；此卡片不再管理订单。")
    elif card.status in HIDDEN:
        lines.append("卡片已结束。")
    if card.delete_failed:
        lines.append("消息暂未删除，已停用所有按钮，将继续重试删除。")
    if card.confirm_action:
        label = {
            "reduce_half": "平掉一半剩余持仓",
            "close": "全部平仓",
            "detach": "停止托管、保留持仓并删除卡片",
        }[card.confirm_action]
        lines.append(f"再次点击确认：{label}。")
    if card.note:
        lines.append(card.note[:240])
    return "\n".join(lines)[:1024]


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
