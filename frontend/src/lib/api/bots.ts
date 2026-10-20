/** Bot lifecycle: list, detail, controllers, logs and runs. */

import { apiFetch, query } from "@/lib/api";

export interface BotInfo {
  id: string;
  name: string;
  status: string;
  connector: string;
  trading_pair: string;
  pnl: number;
  uptime: number;
  controller_type: string;
}

export interface BotDetail {
  bot: BotInfo;
  config: Record<string, unknown>;
  performance: Record<string, unknown>;
}

export interface ControllerInfo {
  controller_name: string;
  /** The coarse bucket upstream sorts every controller into, when the specific
   *  class (`controller_name`) could not be recovered — see `controllerClassOf`. */
  controller_type: string;
  controller_id: string;
  bot_name: string;
  status: string;
  connector: string;
  trading_pair: string;
  realized_pnl_quote: number;
  unrealized_pnl_quote: number;
  global_pnl_quote: number;
  global_pnl_pct: number;
  volume_traded: number;
  close_type_counts: Record<string, number>;
  positions_summary: Record<string, unknown>[];
  deployed_at: string | null;
  config: Record<string, unknown>;
}

export interface BotLogEntry {
  timestamp?: number;
  msg?: string;
  log_category?: string;
  [key: string]: unknown;
}

export interface BotSummary {
  bot_name: string;
  status: string;
  num_controllers: number;
  error_count: number;
  deployed_at: string | null;
  error_logs: BotLogEntry[];
  general_logs: BotLogEntry[];
}

export interface BotsPageResponse {
  controllers: ControllerInfo[];
  bots: BotSummary[];
  total_pnl: number;
  total_volume: number;
  server_online?: boolean;
  error_hint?: string;
}

export interface BotRunInfo {
  bot_name: string;
  bot_run_id: number | null;
  account_name: string;
  strategy_type: string;
  strategy_name: string;
  run_status: string;
  deployment_status: string;
  created_at: string | null;
  stopped_at: string | null;
  realized_pnl_quote: number;
  unrealized_pnl_quote: number;
  global_pnl_quote: number;
  volume_traded: number;
  num_controllers: number;
  /** This run's archived sqlite path, or null when no database survived it. */
  archive_db_path: string | null;
  /**
   * The controller config ids this run was deployed with, from its own
   * `deployment_config` — the authoritative run → controller mapping, and the
   * only one that exists for a run with no snapshots left (FEAT-089).
   */
  controller_ids: string[];
  /**
   * Whether this run is still the live fleet. Derived server-side: upstream
   * never writes `run_status: "RUNNING"`, so reading that string files every
   * bot trading right now under history.
   */
  is_live: boolean;
}

export interface BotRunsResponse {
  runs: BotRunInfo[];
  total: number;
}

/** One finished run's sampled PnL curve, per controller (FEAT-089). */
export interface RunHistoryResponse {
  /** `controller_id -> [[t_ms, realized, unrealized, net, volume, pct], ...]` */
  controllers: Record<string, number[][]>;
  /** `controller_id -> { connector, trading_pair }` — what the fold converts through. */
  identities: Record<string, { connector: string; trading_pair: string }>;
  interval: string;
  /**
   * Which source answered: sampled snapshots, the archived database, or
   * nothing. `"none"` is an answer — a run older than the server's snapshot
   * retention was never recorded — and the chart's notice says which it was
   * rather than asserting one.
   */
  source: "snapshots" | "archive" | "none";
  points: number;
  /** True when it came off Condor's disk rather than off the wire. */
  cached: boolean;
  detail?: string | null;
}

/**
 * Every controller of every run that has finished (FEAT-089).
 *
 * `ControllerInfo`, the same shape the live fleet arrives in, so one tree, one
 * fold and one set of panes describe both populations. Not
 * `ControllerPerformanceSnapshot`: that drops `close_type_counts` on purpose
 * (PERF-261) and the close-type strip leads the scope header.
 */
export interface TerminatedControllersResponse {
  controllers: ControllerInfo[];
  /** How many finished runs contributed a controller — the tree's denominator. */
  runs_seen: number;
  server_online?: boolean;
  error_hint?: string;
}

// ── Deploy Bot ──

export interface ControllerConfigSummary {
  id: string;
  controller_name: string;
  controller_type: string;
  connector_name: string;
  trading_pair: string;
}

export interface AvailableControllersResponse {
  configs: ControllerConfigSummary[];
  controller_types: Record<string, string[]>;
}

export interface ControllerConfigDetail {
  id: string;
  controller_name: string;
  controller_type: string;
  config: Record<string, unknown>;
}

export interface ControllerSourceResponse {
  controller_name: string;
  controller_type: string;
  source: string;
}

export interface DeployBotRequest {
  bot_name: string;
  controllers_config: string[];
  account_name?: string;
  image?: string;
  max_global_drawdown_quote?: number | null;
  max_controller_drawdown_quote?: number | null;
}

export const botsApi = {
  getBots: (server: string) =>
    apiFetch<BotsPageResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/bots`,
    ),

  getBot: (server: string, botId: string) =>
    apiFetch<BotDetail>(
      `/api/v1/servers/${encodeURIComponent(server)}/bots/${encodeURIComponent(botId)}`,
    ),

  getAvailableConfigs: (server: string) =>
    apiFetch<AvailableControllersResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/configs`,
    ),

  getConfigDetail: (server: string, configId: string) =>
    apiFetch<ControllerConfigDetail>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/configs/${encodeURIComponent(configId)}`,
    ),

  updateConfig: (
    server: string,
    configId: string,
    data: Record<string, unknown>,
  ) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/configs/${encodeURIComponent(configId)}`,
      {
        method: "PUT",
        body: JSON.stringify(data),
      },
    ),

  updateBotControllerConfig: (
    server: string,
    botName: string,
    configId: string,
    data: Record<string, unknown>,
  ) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/servers/${encodeURIComponent(server)}/bots/${encodeURIComponent(botName)}/controllers/${encodeURIComponent(configId)}/config`,
      {
        method: "PUT",
        body: JSON.stringify(data),
      },
    ),

  updateConfigYaml: (server: string, configId: string, yamlContent: string) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/configs/${encodeURIComponent(configId)}`,
      {
        method: "PUT",
        body: JSON.stringify({ yaml_content: yamlContent }),
      },
    ),

  getControllerConfigTemplate: (
    server: string,
    controllerType: string,
    controllerName: string,
  ) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/${encodeURIComponent(controllerType)}/${encodeURIComponent(controllerName)}/template`,
    ),

  createControllerConfig: (
    server: string,
    configId: string,
    data: Record<string, unknown>,
  ) =>
    apiFetch<{ created: boolean; config_id: string }>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/configs`,
      {
        method: "POST",
        body: JSON.stringify({ ...data, id: configId }),
      },
    ),

  deleteControllerConfig: (server: string, configId: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/configs/${encodeURIComponent(configId)}`,
      {
        method: "DELETE",
      },
    ),

  deleteController: (
    server: string,
    controllerType: string,
    controllerName: string,
  ) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/${encodeURIComponent(controllerType)}/${encodeURIComponent(controllerName)}`,
      { method: "DELETE" },
    ),

  getControllerSource: (
    server: string,
    controllerType: string,
    controllerName: string,
  ) =>
    apiFetch<ControllerSourceResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/${encodeURIComponent(controllerType)}/${encodeURIComponent(controllerName)}/source`,
    ),

  updateControllerSource: (
    server: string,
    controllerType: string,
    controllerName: string,
    source: string,
  ) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/servers/${encodeURIComponent(server)}/controllers/${encodeURIComponent(controllerType)}/${encodeURIComponent(controllerName)}/source`,
      { method: "PUT", body: JSON.stringify({ source }) },
    ),

  deployBot: (server: string, data: DeployBotRequest) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/servers/${encodeURIComponent(server)}/bots/deploy`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    ),

  stopBot: (server: string, botName: string) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/servers/${encodeURIComponent(server)}/bots/${encodeURIComponent(botName)}/stop`,
      { method: "POST" },
    ),

  stopControllers: (
    server: string,
    botName: string,
    controllerNames: string[],
  ) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/servers/${encodeURIComponent(server)}/bots/${encodeURIComponent(botName)}/controllers/stop`,
      {
        method: "POST",
        body: JSON.stringify({ controller_names: controllerNames }),
      },
    ),

  startControllers: (
    server: string,
    botName: string,
    controllerNames: string[],
  ) =>
    apiFetch<Record<string, unknown>>(
      `/api/v1/servers/${encodeURIComponent(server)}/bots/${encodeURIComponent(botName)}/controllers/start`,
      {
        method: "POST",
        body: JSON.stringify({ controller_names: controllerNames }),
      },
    ),

  getBotRuns: (
    server: string,
    params: {
      bot_name?: string;
      run_status?: string;
      deployment_status?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    return apiFetch<BotRunsResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/bot-runs${query(params)}`,
    );
  },

  /**
   * The controllers of every finished run.
   *
   * One upstream call for the whole terminated population — the latest
   * snapshot of every controller of every bot the API has ever orchestrated —
   * joined to the runs and cached warm server-side, so walking the Terminated
   * tree costs nothing.
   */
  getTerminatedControllers: (server: string) =>
    apiFetch<TerminatedControllersResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/terminated/controllers`,
    ),

  /**
   * One finished run's curve, walked once by Condor and cached for ever.
   *
   * Immutable by construction — the bot has stopped — so the caller should tell
   * react-query so: `staleTime: Infinity`. The first open pays one walk behind
   * a spinner; every one after it is a file open.
   */
  getRunHistory: (
    server: string,
    botName: string,
    deployedAt: string,
    /** The run's archived database, for a run older than the snapshot table. */
    dbPath?: string | null,
  ) => {
    const qs = new URLSearchParams({ bot_name: botName, deployed_at: deployedAt });
    if (dbPath) qs.set("db_path", dbPath);
    return apiFetch<RunHistoryResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/terminated/history?${qs}`,
    );
  },

  deleteBotRun: (server: string, botRunId: number) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/servers/${encodeURIComponent(server)}/bot-runs/${botRunId}`,
      {
        method: "DELETE",
      },
    ),
};
