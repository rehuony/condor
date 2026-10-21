"""``/start``: access requests for new users, a welcome for approved ones."""

from condor.telegram.types import NO_LINK_PREVIEW, Context, Update
from handlers import clear_all_input_states
from utils.auth import restricted


async def start_command(update: Update, context: Context) -> None:
    """Start the conversation and display available commands (BotFather style)."""
    from config_manager import UserRole, get_config_manager
    from utils.auth import _capture_identity, _notify_admin_new_user

    tg_user = update.effective_user
    user_id = tg_user.id
    username = tg_user.username or ""
    # What to print where the reply says "Username:". An absent handle is shown
    # as absent rather than stored as the "No username" string the record used
    # to carry (FEAT-088) — the sentinel was a display default that leaked into
    # config.yml and became a name the admin panel then had to render.
    handle_display = f"@{username}" if username else "not set"

    cm = get_config_manager()
    role = cm.get_user_role(user_id)

    # Handle blocked users
    if role == UserRole.BLOCKED:
        await update.message.answer("Access denied.")
        return

    # Handle pending users
    if role == UserRole.PENDING:
        reply_text = f"""Access Pending

Your access request is awaiting admin approval.

Your Info:
User ID: {user_id}
Username: {handle_display}

You will be notified when approved."""
        await update.message.answer(reply_text)
        return

    # Handle new users - register as pending
    if role is None:
        is_new = cm.register_pending(
            user_id,
            username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
        )
        if is_new:
            await _notify_admin_new_user(context, user_id, username)

        reply_text = f"""Access Request Submitted

Your request has been sent to the admin for approval.

Your Info:
User ID: {user_id}
Username: {handle_display}

You will be notified when approved."""
        await update.message.answer(reply_text)
        return

    # User is approved (USER or ADMIN role)
    _capture_identity(update)
    clear_all_input_states(context)

    reply_text = """I can help you create and manage trading bots on any CEX or DEX using Hummingbot API servers\\.

See [this manual](https://github.com/rehuony/condor#readme) if you're new to Condor\\.

You can control me by sending these commands:

/keys \\- add exchange API keys
/portfolio \\- view balances across exchanges
/bots \\- deploy and manage trading bots
/trade \\- place CEX and DEX orders
/agent \\- AI trading assistant
/web \\- open the web dashboard"""

    await update.message.answer(
        reply_text, parse_mode="MarkdownV2", link_preview_options=NO_LINK_PREVIEW
    )


@restricted
async def start_callback_handler(update: Update, context: Context) -> None:
    """Handle callbacks from the start menu."""
    query = update.callback_query
    await query.answer()

    data = query.data
    action = data.split(":")[1] if ":" in data else data

    # Handle cancel - delete the message
    if action == "cancel":
        await query.message.delete()
        return

    # Handle navigation to config options
    if data.startswith("start:"):
        if action == "config_servers":
            from handlers.config.servers import show_api_servers

            clear_all_input_states(context)
            await show_api_servers(query, context)
        elif action == "config_keys":
            from handlers.config.api_keys import show_api_keys

            clear_all_input_states(context)
            await show_api_keys(query, context)
        elif action == "config_gateway":
            from handlers.config.gateway import show_gateway_menu

            clear_all_input_states(context)
            context.user_data.pop("dex_state", None)
            context.user_data.pop("cex_state", None)
            await show_gateway_menu(query, context)
        elif action == "admin":
            from handlers.admin import _show_admin_menu

            clear_all_input_states(context)
            await _show_admin_menu(query, context)
