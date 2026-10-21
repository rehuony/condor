"""
Command handlers for Condor Telegram bot
"""

from condor.telegram.types import Context, Update


def is_gateway_network(connector_name: str) -> bool:
    """
    Check if a connector name is a Gateway network (DEX) vs a CEX connector.

    Gateway networks: solana-mainnet-beta, ethereum-mainnet, base, arbitrum, etc.
    CEX connectors: binance, binance_perpetual, hyperliquid, kucoin, etc.
    """
    if not connector_name:
        return False

    connector_lower = connector_name.lower()

    # Known Gateway network patterns
    gateway_patterns = [
        "solana",
        "ethereum",
        "base",
        "arbitrum",
        "polygon",
        "optimism",
        "avalanche",
        "mainnet",
        "devnet",
        "testnet",
    ]

    for pattern in gateway_patterns:
        if pattern in connector_lower:
            return True

    return False


def clear_all_input_states(context: Context) -> None:
    """
    Clear ALL input-related states from user context.

    Call this at the start of any new command or major flow transition
    to prevent state pollution between different features.

    This is the MASTER state cleaner - it clears:
    - CLOB trading states
    - DEX trading states
    - Config states (servers, API keys, gateway)
    - Gateway wallet/pool/connector states
    """
    # CEX trading states
    pending_trade_card = context.user_data.pop("trade_card_amount_input", None)
    if pending_trade_card and context.user_data.get("_user_id"):
        from condor.trade_cards.service import cancel_input_state

        cancel_input_state(pending_trade_card, owner_id=context.user_data["_user_id"])
    context.user_data.pop("cex_state", None)
    context.user_data.pop("place_order_params", None)
    context.user_data.pop("current_positions", None)
    context.user_data.pop("cex_previous_state", None)
    context.user_data.pop("trade_params", None)
    context.user_data.pop("current_orders", None)
    context.user_data.pop("trade_menu_message_id", None)
    context.user_data.pop("trade_menu_chat_id", None)
    context.user_data.pop("current_market_price", None)
    context.user_data.pop("swap_params", None)
    context.user_data.pop("swap_menu_message_id", None)
    context.user_data.pop("swap_menu_chat_id", None)

    # DEX trading states
    context.user_data.pop("dex_state", None)
    context.user_data.pop("swap_quote_params", None)
    context.user_data.pop("swap_execute_params", None)
    context.user_data.pop("add_position_params", None)
    context.user_data.pop("selected_pool", None)
    context.user_data.pop("pool_list_data", None)
    context.user_data.pop("position_list_data", None)
    context.user_data.pop("add_position_menu_msg_id", None)
    context.user_data.pop("add_position_menu_chat_id", None)
    context.user_data.pop("quote_swap_params", None)
    context.user_data.pop("execute_swap_params", None)
    context.user_data.pop("dex_previous_state", None)
    context.user_data.pop("add_position_prompt_msg_id", None)
    context.user_data.pop("pool_list_message_id", None)
    context.user_data.pop("pool_list_chat_id", None)
    context.user_data.pop("pool_detail_message_id", None)
    context.user_data.pop("pool_detail_chat_id", None)

    # Config - server modification states
    context.user_data.pop("modifying_server", None)
    context.user_data.pop("modifying_field", None)
    context.user_data.pop("awaiting_modify_input", None)
    context.user_data.pop("adding_server", None)
    context.user_data.pop("awaiting_add_server_input", None)

    # Config - API keys states
    context.user_data.pop("configuring_api_key", None)
    context.user_data.pop("awaiting_api_key_input", None)
    context.user_data.pop("api_key_config_data", None)

    # Config - gateway states
    context.user_data.pop("gateway_state", None)
    context.user_data.pop("awaiting_gateway_input", None)

    # Gateway - wallet states
    context.user_data.pop("awaiting_wallet_input", None)
    context.user_data.pop("wallet_chain", None)
    context.user_data.pop("wallet_address", None)
    context.user_data.pop("editing_wallet_networks", None)

    # Gateway - pool states
    context.user_data.pop("awaiting_pool_input", None)
    context.user_data.pop("pool_connector", None)
    context.user_data.pop("pending_pool_delete", None)

    # Gateway - connector states
    context.user_data.pop("awaiting_connector_input", None)
    context.user_data.pop("connector_name", None)

    # Gateway - network states
    context.user_data.pop("awaiting_network_input", None)
    context.user_data.pop("network_list", None)
    context.user_data.pop("configuring_network", None)
    context.user_data.pop("network_config_data", None)
    context.user_data.pop("network_message_id", None)
    context.user_data.pop("network_chat_id", None)

    # Gateway - token states
    context.user_data.pop("awaiting_token_input", None)
    context.user_data.pop("token_network", None)
    context.user_data.pop("pending_token_delete", None)

    # Gateway - RPC provider states
    context.user_data.pop("awaiting_rpc_input", None)
    context.user_data.pop("rpc_message_id", None)
    context.user_data.pop("rpc_chat_id", None)
    context.user_data.pop("rpc_url_network_list", None)
    context.user_data.pop("rpc_edit_network_id", None)
    context.user_data.pop("rpc_edit_network_idx", None)

    # Bots - controller config states
    context.user_data.pop("bots_state", None)
    context.user_data.pop("controller_config_params", None)
    context.user_data.pop("controller_configs_list", None)
    context.user_data.pop("selected_controllers", None)
    context.user_data.pop("editing_controller_field", None)
    context.user_data.pop("editing_ctrl_field", None)
    context.user_data.pop("cfg_edit_field", None)
    context.user_data.pop("deploy_current_field", None)
    context.user_data.pop("deploy_params", None)
    context.user_data.pop("editing_deploy_field", None)
    context.user_data.pop("selected_configs", None)
    context.user_data.pop("configs_controller_type", None)
    context.user_data.pop("configs_type_filtered", None)
    context.user_data.pop("configs_page", None)

    # Bots - archived states
    context.user_data.pop("archived_databases", None)
    context.user_data.pop("archived_current_db", None)
    context.user_data.pop("archived_page", None)
    context.user_data.pop("archived_summaries", None)
    context.user_data.pop("archived_total_count", None)

    # Agent states — only clear transient UI states, NOT session/preference state.
    # agent_llm is a persistent user preference (always-on agent). A stale
    # agent_mode may still sit in pickled user_data; nothing reads it since the
    # persona axis was deleted (FEAT-033), so it is left alone rather than
    # migrated.
    # agent_state is no longer used for routing (agent is the default fallback).
    context.user_data.pop("agent_compact_custom", None)
    context.user_data.pop("agent_chat_target", None)

    # Agent — armed text-input modes. These make the next plain message mean
    # something other than "talk to the agent", so a stale one silently eats
    # the user's next message (and, worse, re-arms itself on a parse failure).
    context.user_data.pop("_openrouter_typing_slug", None)
    context.user_data.pop("_openrouter_typed_slug", None)
    context.user_data.pop("_custom_typing_url", None)
    context.user_data.pop("_custom_typing_key", None)
    context.user_data.pop("_custom_typing_search", None)
    context.user_data.pop("_custom_pending_url", None)
    context.user_data.pop("_custom_pending_name", None)
    context.user_data.pop("_custom_rekey_provider", None)
    context.user_data.pop("_custom_filter", None)

    # Routines states
    context.user_data.pop("routines_state", None)
    context.user_data.pop("routines_editing", None)

    # Access share states
    context.user_data.pop("sharing_server", None)
    context.user_data.pop("awaiting_share_user_id", None)
    context.user_data.pop("share_target_user_id", None)
    context.user_data.pop("share_message_id", None)
    context.user_data.pop("share_chat_id", None)

    # Trading agent states
    context.user_data.pop("ta_state", None)
    context.user_data.pop("ta_selected_strategy", None)
    context.user_data.pop("ta_selected_agent", None)
    context.user_data.pop("ta_config_params", None)

    # Executors states
    context.user_data.pop("executors_state", None)
    context.user_data.pop("executor_config_params", None)
    context.user_data.pop("executor_wizard_step", None)
    context.user_data.pop("executor_wizard_data", None)
    context.user_data.pop("executor_list_page", None)
    context.user_data.pop("executor_chart_interval", None)
    context.user_data.pop("executor_wizard_type", None)
    context.user_data.pop("history_executors", None)

    # Clear SDS subscriptions for this user (resets active polling)
    user_id = context.user_data.get("_user_id")
    if user_id:
        try:
            from condor.server_data_service import get_server_data_service

            get_server_data_service().unsubscribe_all(f"tg_trade_{user_id}")
        except Exception:
            pass

    # The chat the armed input belonged to (see handlers.messages)
    context.user_data.pop("_input_chat_id", None)

    # Portfolio snapshot states (large transient data)
    context.user_data.pop("portfolio_text_message_id", None)
    context.user_data.pop("portfolio_photo_message_id", None)
    context.user_data.pop("portfolio_chat_id", None)
    context.user_data.pop("portfolio_graph_interval", None)
    context.user_data.pop("portfolio_server_name", None)
    context.user_data.pop("portfolio_server_status", None)
    context.user_data.pop("portfolio_current_value", None)
    context.user_data.pop("portfolio_balances", None)
    context.user_data.pop("portfolio_accounts_distribution", None)
    context.user_data.pop("portfolio_changes_24h", None)
    context.user_data.pop("portfolio_pnl_indicators", None)
    context.user_data.pop("portfolio_connector_keys", None)
    context.user_data.pop("portfolio_view_mode", None)
    context.user_data.pop("_portfolio_refresh", None)


async def cancel_command(update: Update, context: Context) -> None:
    """Handle /cancel — abandon any in-progress input flow.

    Several flows arm a "your next message is the answer" mode and tell the
    user to send /cancel to back out. That only works if something actually
    handles the command: the unified text handler only sees non-command
    messages, so without this a typed /cancel matches no handler at all and
    is silently dropped, leaving the user stuck in the flow.
    """
    from utils.auth import restricted

    @restricted
    async def _cancel(update: Update, context: Context) -> None:
        from handlers.agents.input import _ARMED_TEXT_INPUT_KEYS
        from handlers.messages import _input_snapshot
        from handlers.trade_cards import cancel_amount_input
        from utils.telegram_ui import notice

        armed = bool(_input_snapshot(context.user_data)) or any(
            context.user_data.get(key) for key in _ARMED_TEXT_INPUT_KEYS
        )
        cancelled_card_input = await cancel_amount_input(update, context)
        clear_all_input_states(context)
        if cancelled_card_input:
            text = notice(
                "cancelled",
                "Custom amount cancelled",
                "The card is back to amount selection.",
            )
        elif armed:
            text = notice("cancelled", "Input cancelled")
        else:
            text = notice("info", "Nothing to cancel")
        await update.message.answer(text, parse_mode="MarkdownV2")

    await _cancel(update, context)
