"""In-memory controller API and source fixtures for sync and impact tests."""

MM_SOURCE = '''"""Pure maker around mid."""
from x import MarketMakingControllerBase


class PmmKing(MarketMakingControllerBase):
    pass
'''


class FakeControllers:
    """The controllers router, in memory. Raises when ``down``."""

    def __init__(self):
        self.code: dict[tuple[str, str], str] = {}
        self.configs: dict[str, dict] = {}
        self.posts: list[tuple] = []
        self.config_posts: list[tuple] = []
        self.down = False
        self.reject_config: str | None = None

    def _check(self):
        if self.down:
            raise ConnectionError("server down")

    async def list_controllers(self):
        self._check()
        out: dict[str, list[str]] = {
            "directional_trading": [],
            "market_making": [],
            "generic": [],
        }
        for ctype, name in self.code:
            out[ctype].append(name)
        return out

    async def get_controller(self, controller_type, controller_name):
        self._check()
        key = (controller_type, controller_name)
        if key not in self.code:
            raise KeyError(f"404 {controller_name}")
        return {
            "name": controller_name,
            "type": controller_type,
            "content": self.code[key],
        }

    async def create_or_update_controller(
        self, controller_type, controller_name, controller_data
    ):
        self._check()
        self.posts.append((controller_type, controller_name, controller_data))
        self.code[(controller_type, controller_name)] = controller_data["content"]
        return {"message": "saved"}

    async def get_controller_config_template(self, controller_type, controller_name):
        return {}

    async def validate_controller_config(self, controller_type, controller_name, cfg):
        if self.reject_config:
            raise ValueError(self.reject_config)
        return {"message": "Configuration is valid"}

    async def list_controller_configs(self):
        self._check()
        return [{"id": k, **v} for k, v in self.configs.items()]

    async def get_controller_config(self, config_name):
        self._check()
        if config_name not in self.configs:
            raise KeyError(f"404 {config_name}")
        return {**self.configs[config_name], "id": config_name}

    async def create_or_update_controller_config(self, config_name, config):
        self.config_posts.append((config_name, config))
        self.configs[config_name] = {k: v for k, v in config.items() if k != "id"}
        return {"message": "saved"}
