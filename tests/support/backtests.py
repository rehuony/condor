"""Scripted backtest API, context, reports, and result fixtures."""

from datetime import datetime, timedelta, timezone

CONFIG_NAME = "ema_eth_20_100"
SERVER = "srv-a"

# The exact envelope GET /backtesting/tasks/{id} returns — BacktestTask.to_dict():
# the payload sits under "result" (singular), the parameters under "config".
PAYLOAD = {
    "executors": [],
    "processed_data": {
        "timestamp": [1745280000, 1745280060],
        "open": [1800.0, 1801.0],
        "high": [1802.0, 1803.0],
        "low": [1799.0, 1800.0],
        "close": [1801.0, 1802.5],
    },
    "results": {
        "net_pnl_quote": 12.5,
        "net_pnl": 0.0125,
        "total_fees_quote": 1.25,
        "max_drawdown_usd": -3.0,
        "max_drawdown_pct": -0.0147,
        "total_volume": 5000.0,
        "sharpe_ratio": 1.42,
        "profit_factor": 1.31,
        "total_executors": 74,
        "accuracy": 0.4459,
        "win_signals": 33,
        "loss_signals": 41,
    },
    "pnl_timeseries": [],
    "position_held_timeseries": [],
}


# Inside the store's payload retention window by construction
# (``DEFAULT_RETENTION_DAYS`` in condor/backtest_store.py). This was a literal
# date until it aged past that window and turned every test in this file red
# exactly 30 days after it was written — a date that is always "yesterday"
# cannot expire.
_CREATED_AT = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


def task_envelope(task_id: str, status: str = "completed", error=None) -> dict:
    return {
        "task_id": task_id,
        "status": status,
        "config": {
            "start_time": 1745280000,  # 2025-04-22
            "end_time": 1745366400,  # 2025-04-23
            "backtesting_resolution": "1m",
            "trade_cost": 0.0002,
            "config": {"id": CONFIG_NAME, "controller_name": "ema_trend_v1"},
        },
        "created_at": _CREATED_AT,
        "error": error,
        **({"result": PAYLOAD} if status == "completed" else {}),
    }


class FakeBacktesting:
    """Submit/poll against a scripted sequence of task states."""

    def __init__(self, task_id: str, states: list[dict]):
        self.task_id = task_id
        self.states = list(states)
        self.submitted: dict | None = None
        self.polls = 0

    async def submit_task(self, **kwargs):
        self.submitted = kwargs
        return {"task_id": self.task_id, "status": "pending"}

    async def get_task(self, task_id):
        self.polls += 1
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


class FakeControllers:
    async def get_controller_config(self, name):
        # A YAML-loaded config: the numbers arrive as strings.
        return {
            "id": name,
            "controller_name": "ema_trend_v1",
            "total_amount_quote": "100",
        }


class FakeClient:
    def __init__(self, backtesting):
        self.backtesting = backtesting
        self.controllers = FakeControllers()


class FakeBot:
    def __init__(self):
        self.photos = 0

    async def send_photo(self, **kwargs):
        self.photos += 1


class FakeContext:
    """A Telegram chat: the server comes from the active-server preference."""

    def __init__(self, chat_id=42):
        self.chat_id = chat_id
        self.owner_id = chat_id
        self.instance_id = ""
        self.server_name = ""
        self.bot = FakeBot()
        self.user_data = {"user_preferences": {"general": {"active_server": SERVER}}}


class FakeReportBuilder:
    saved: list[str] = []

    def __init__(self, title="Report"):
        self.title = title

    def source(self, *a, **k):
        return self

    def tags(self, *a, **k):
        return self

    def markdown(self, *a, **k):
        return self

    def plotly(self, *a, **k):
        return self

    async def save(self, report_id=None):
        FakeReportBuilder.saved.append(self.title)
        return "report-1"
