/** Agents, their brain/skills/memory, strategies and run records. */

import type { AgentActionRow, DeedIndex, FleetMap, FleetOwner } from "@/lib/agent-attribution";
import { apiFetch } from "@/lib/api";

import type { ReportSummary } from "./reports";

// ── Agents ──

export interface RunningInstance {
  agent_id: string;
  session_num: number;
  status: string;
  agent_key: string;
  tick_count: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  volume: number;
  fees: number;
  open_count: number;
  closed_count: number;
  /** null = no closed executors to derive it from (bot-mode sessions), not 0%. */
  win_rate: number | null;
  server_name: string;
  total_amount_quote: number;
  trading_context: string;
  frequency_sec: number;
  /** Wall-clock budget for one tick's agent session, in seconds. */
  tick_timeout_sec: number;
  execution_mode: "dry_run" | "run_once" | "loop";
  risk_limits: Record<string, unknown>;
  // ── The loop's pulse ──
  // The engine has always computed these; the wire model dropped them, so a
  // strategy view could say "running" and nothing about when it last ran.
  /** Unix seconds the current tick started. 0 before the first one. */
  last_tick_at: number;
  /** 0 = unbounded; otherwise the tick this session stops at. */
  max_ticks: number;
  /** What the loop last *said* — the journal's `Last action:` line. */
  last_action: string;
  /** What the loop last *did* — one mutating tool call, or null. */
  last_did: AgentActionRow | null;
  last_error: string;
}

/**
 * Why a strategy's figures could not be read, or `""` when they could
 * (CORR-706). Without it, all three render as a session that traded nothing:
 * - `no_server` — its config names no server
 * - `no_access` — the caller may not use the server it names (SEC-334)
 * - `unreachable` — the server is offline or its credentials failed
 * Optional on the wire so an older backend reads as `""`.
 */
export type StrategyUnavailable = "" | "no_server" | "no_access" | "unreachable";

export interface StrategySummary {
  slug: string;
  name: string;
  description: string;
  status: string;
  agent_id: string;
  session_count: number;
  experiment_count: number;
  tick_count: number;
  /** PnL of the newest session only; `total_pnl` is the rollup across all sessions. */
  latest_session_pnl: number;
  total_pnl: number;
  total_volume: number;
  open_positions: number;
  /**
   * Where this strategy's records live — its own config's server, `""` when it
   * declared none (ARCH-324).
   *
   * The rollup above was computed *from* this server, so a reader that wants to
   * fold the same records has to fetch them from here. Optional on the wire so
   * an older backend reads as "no server" and the caller shows a dash rather
   * than folding the wrong fleet.
   */
  server_name?: string;
  /** Why the figures above are zero when it is not "nothing traded". */
  unavailable?: StrategyUnavailable;
  instances: RunningInstance[];
}

/**
 * The coordinator's slug — the Agent you get by binding none.
 *
 * Condor lives in the same registry as every specialist (FEAT-033), so it
 * comes back from `/agents` like the rest. But a chat is *with* Condor
 * precisely when `agent_slug` is empty, so any list of specialists to bind has
 * to lift it out or offer the same identity twice.
 */
export const CHAT_SLUG = "condor";

/**
 * The fleet map's wire shape (`GET /agents/fleet-map`, FEAT-096).
 *
 * Snake-cased, like every other payload in this file. `getFleetMap` is the one
 * call that maps rather than returning the wire type: the rule it feeds
 * (`lib/agent-attribution`) is pure domain logic with no HTTP in it, so it
 * speaks the app's casing and the translation happens once, here.
 */
interface FleetOwnerWire {
  run_key: string;
  agent_slug: string;
  agent_name: string;
  strategy_slug: string;
  strategy_name: string;
  namespace: string;
  declared_bots: string[];
  agent_ids: string[];
  live: {
    agent_id: string;
    session_num: number;
    status: string;
    tick_count: number;
    last_tick_at: number;
    frequency_sec: number;
    last_action: string;
    last_did: AgentActionRow | null;
    last_error: string;
  } | null;
}

/**
 * The deed index's wire shape (FEAT-106), snake-cased like everything else here.
 *
 * Absent from an older backend, and the mapper below reads that as an empty
 * index — which attributes nothing and judges nothing, exactly as the page
 * behaved before this field existed.
 */
interface DeedIndexWire {
  bots?: Record<string, { run_key: string; run_id?: string; at?: number }>;
  since?: number;
}

export interface AgentSummary {
  slug: string;
  name: string;
  description: string;
  /** Routing hint only — every Agent is delegable and loopable. */
  when_to_consult: string;
  agent_key: string;
  strategy_count: number;
  strategies: StrategySummary[];
  // Rolled-up aggregates across the agent's strategies (FEAT-004) for summary cards.
  status: string;
  agent_id?: string;
  session_count: number;
  experiment_count: number;
  tick_count: number;
  /** PnL of the newest session only; `total_pnl` is the rollup across all sessions. */
  latest_session_pnl: number;
  total_pnl: number;
  total_volume: number;
  open_positions: number;
  /**
   * The agent's server *pin*, `""` when it follows the ambient one (ARCH-324).
   *
   * A strategy's own `server_name` overrides it — the rule `declaredServerOf`
   * (workspace/fleet.ts) owns, and both the home and the workspace call.
   */
  server_name?: string;
  instances: RunningInstance[];
}

export interface AgentExecutorRow {
  id: string;
  type: string;
  connector: string;
  pair: string;
  side: string;
  status: string;
  close_type: string;
  pnl: number;
  volume: number;
  fees: number;
  entry_price: number;
  current_price: number;
  amount: number;
  timestamp: number;
  close_timestamp: number;
  controller_id: string;
  custom_info?: Record<string, unknown>;
  config?: Record<string, unknown>;
}

export interface AgentPerformance {
  agent_id: string;
  session_num: number;
  kind: "session" | "experiment";
  /** For an experiment: `dry_run` or `run_once`. Empty for a session. */
  execution_mode?: string;
  /** An experiment whose snapshot recorded an error. */
  error?: boolean;
  status: string;
  /** When the session started, unix seconds (its config.yml mtime). 0 or
   *  absent = unknown; always 0 for an experiment. */
  started_at?: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  volume: number;
  fees: number;
  trade_count: number;
  /** null = no closed executors to derive it from (bot-mode sessions), not 0%. */
  win_rate: number | null;
  open_count: number;
  closed_count: number;
  executors: AgentExecutorRow[];
  // ── Bot-mode attribution ──
  // A session trading through bots has no rows in the agent_id-keyed executor
  // table: its executors live inside the bot instance's own database, and the
  // only ones Condor can reconstruct are the positions open right now. These say
  // what the session actually operated when `executors` is empty.
  /** Instance names alive now, one per owned base. */
  bot_names?: string[];
  /** Every instance ever deployed under an owned base, oldest first — a name in
   *  here but not in `bot_names` is one this session already stopped. */
  bot_instances?: string[];
  /** Owned bases with no live and no archived instance: their PnL is unknown,
   *  not zero, so the totals are a floor. */
  unresolved_bases?: string[];
  controllers?: AgentControllerRow[];
  /** Raw `CloseType.X -> n` across the operated bots. `trade_count` counts only
   *  round-trip closes and reads a directional controller's risk stop as churn,
   *  so this is what explains "0 trades" on non-zero volume. */
  close_type_counts?: Record<string, number>;
  /** False when `fees` is a floor: the backend reports no cumulative fee column,
   *  so 0 means unknown rather than free. */
  fees_known?: boolean;
}

/** One controller of one bot instance a session operated. */
export interface AgentControllerRow {
  /** The deploy this controller ran under. */
  bot_name: string;
  controller_id: string;
  controller_name: string;
  connector: string;
  trading_pair: string;
  /** From the performance snapshot, which reports "running" even for archived
   *  instances — never render this as live truth. Derive liveness from whether
   *  `bot_name` appears in `AgentPerformance.bot_names`. */
  status: string;
  realized_pnl_quote: number;
  unrealized_pnl_quote: number;
  volume_traded: number;
  cum_fees_quote: number;
  closed_trades: number;
  close_type_counts: Record<string, number>;
}

/**
 * One thing a run put into the world (FEAT-100).
 *
 * The wire shape of `condor.web.routes.agents.DeploymentRow`, mapped straight
 * through. A bot the run deployed, a controller one of those bots ran, or a
 * standalone executor it created — the three kinds together answer "what did
 * this run actually do out there", which used to require leaving the agent for
 * the fleet browser and reading the strategy's whole lifetime instead.
 */
export interface DeploymentRow {
  kind: "bot" | "controller" | "executor";
  /** The base name, the controller id, or `"grid SOL-USDC"`. */
  label: string;
  /** Origin for a bot, connector·pair for a controller, connector otherwise. */
  detail: string;
  /**
   * The tick whose creating call most likely produced this, or `null`.
   *
   * The actions log records a call's arguments and never its result, so there
   * is no id to join on and the tick is matched on time. `null` is the honest
   * answer — no log, a stale window, or a bot adopted rather than deployed —
   * and is rendered as `—`, never as a guess.
   */
  created_tick: number | null;
  started_at: number;
  /** When the run stopped owning it; `null` while it still does. */
  ended_at: number | null;
  /** Read off ownership, never off a performance snapshot's `status`. */
  live: boolean;
  pnl: number;
  volume: number;
  /** The fleet address this row links to: `bot:` / `ctrl:` / `exec:`. */
  scope: string;
  /**
   * `false` when `pnl`/`volume` are not USD: a controller whose quote had no
   * USD rate is left at face value (CORR-707). Rendered as `—`, never as `$`.
   */
  usd_converted: boolean;
}

export interface SessionCanvas {
  sections: Record<string, string>;
  section_titles: Record<string, string>;
  section_order: string[];
  last_revised_tick: number;
  revisions: { tick: number; section: string; text: string }[];
}

export interface AgentPerformanceResponse {
  slug: string;
  sessions: AgentPerformance[];
  totals: Record<string, number>;
}

export interface StrategySessionInfo {
  number: number;
  snapshot_count: number;
  created_at: string;
}

export interface ExperimentInfo {
  number: number;
  execution_mode: string;
  agent_key: string;
  snapshot_count: number;
  created_at: string;
  error?: boolean;
}

// Agent = identity + brain (AGENT.md, tools) that owns strategies.
export interface AgentDetail {
  slug: string;
  name: string;
  description: string;
  agent_md: string;
  agent_key: string;
  tools: string[];
  when_to_consult: string;
  server_required: boolean;
  server_name: string;
  strategies: StrategySummary[];
}

/**
 * The Agent's brain, read back for a human.
 *
 * The four libraries the model is handed at the top of every turn — its
 * playbooks, what it remembers about you, the scripts it can run and the
 * strategies it owns — plus its identity and tool allowlist. Metadata only:
 * a skill's or memory's body costs a deliberate fetch (`getAgentSkill` /
 * `getAgentMemory`), so opening the panel never pulls the whole library.
 */
/**
 * One tool of this agent's seat's ring (FEAT-091).
 *
 * Two things keep it from being mounted, on every backend alike: `muted`, and —
 * when the agent names an allowlist (`tools_unrestricted` false) — `allowlisted`
 * being false. Either way the next session does not register it at all.
 */
export interface ToolCard {
  name: string;
  /** Which subprocess registers it: `condor` or `hummingbot`. */
  server: string;
  description: string;
  /** Switched off by the operator — the next session never mounts it. */
  muted: boolean;
  /** The AGENT.md allowlist names it. Under an allowlist, a row it omits is never mounted. */
  allowlisted: boolean;
}

export interface AgentBrain {
  slug: string;
  name: string;
  description: string;
  agent_md: string;
  agent_key: string;
  when_to_consult: string;
  server_required: boolean;
  server_name: string;
  /** Every tool this agent's seat mounts, each with its switch. */
  tools: ToolCard[];
  /** Empty AGENT.md allowlist = every mounted tool, which is not "no tools". */
  tools_unrestricted: boolean;
  skills: SkillCard[];
  /** The one playbook this agent has offered and nobody has ruled on yet. */
  skill_proposal: SkillProposal | null;
  memories: MemoryCard[];
  routines: RoutineCard[];
  strategies: StrategyCard[];
  /**
   * Controllers it owns or inherits from `_shared` (FEAT-127) — disk only.
   * Optional because a server running code from before FEAT-127 leaves it out.
   */
  controllers?: ControllerCard[];
}

/**
 * A playbook the agent proposed from a conversation (FEAT-074).
 *
 * It is not in the library and reaches no prompt until somebody accepts it, so
 * unlike a `SkillCard` it carries its body: the whole point of the card is
 * reading what you are about to put in every future turn before saying yes.
 */
export interface SkillProposal {
  name: string;
  description: string;
  when_to_use: string;
  body: string;
  source: string;
  /** The conversation it was worked out in. */
  from_conversation: string;
  created: string;
}

/**
 * One opener this Agent learned the caller keeps asking it for (FEAT-073).
 *
 * Learned rows only: the server never sends the static defaults, because the
 * client has always owned its own cold-start copy. An empty list is therefore
 * the correct answer for a new user, not a missing one.
 */
export interface StarterRow {
  title: string;
  hint: string;
  /** Sent verbatim on click. */
  prompt: string;
  /** A keyword from the icon vocabulary, or "" — see `Starters.tsx`. */
  icon: string;
  /** The playbook this intent maps to, when it maps to one. */
  skill: string;
}

export interface SkillCard {
  slug: string;
  name: string;
  description: string;
  when_to_use: string;
  /** From `agents/_shared/skills` rather than this Agent's own library. */
  shared: boolean;
  /** Shared *and* not writable from here — read-only for this Agent. */
  inherited: boolean;
  /** Switched off for this Agent: listed here, and nowhere the agent looks. */
  muted: boolean;
  references_routine: string;
  routine_ok: boolean;
}

export interface MemoryCard {
  name: string;
  description: string;
  type: string;
  created: string;
  source: string;
}

export interface RoutineCard {
  name: string;
  description: string;
  continuous: boolean;
  /** `"global"` for the shared library, `"agent:<slug>"` for its own. */
  source: string;
  category: string;
  /** Switched off for this Agent. `/routines` still lists and runs it. */
  muted: boolean;
}

/** One controller an agent can sync to a server (FEAT-127). */
export interface ControllerCard {
  name: string;
  /** `null` when the type cannot be resolved — `type_error` says why. */
  controller_type: string | null;
  description: string;
  /** Sample config stems. */
  styles: string[];
  /** Inherited from the `_shared` library. */
  shared: boolean;
  stock: boolean;
  type_error: string;
}

export type ControllerVerdict = "in_sync" | "missing" | "drift" | "unreachable";

/** One row of `GET /agents/{slug}/controllers?server_name=`. */
export interface ControllerStatusRow {
  name: string;
  controller_type: string | null;
  styles: string[];
  shared: boolean;
  digest: string;
  type_error?: string;
  server?: {
    verdict: ControllerVerdict;
    server_digest?: string;
    server_type?: string;
    detail?: string;
  };
}

/**
 * What a sync or a config upload answers. A refusal (drift, missing,
 * unreachable, a validation error) is a 200 with `refused: true` and the
 * `reason` — plus the `diff` or `error` to read before deciding.
 */
export interface ControllerActionResult {
  name: string;
  verdict?: ControllerVerdict;
  changed?: boolean;
  refused?: boolean;
  reason?: string;
  diff?: string;
  error?: string;
  message?: string;
  warning?: string;
  config_name?: string;
  overwritten?: boolean;
  backup?: string;
  /** Who a push affects (FEAT-129): drift refusals and completed pushes. */
  impact?: ControllerImpact;
  /** The same impact as prose — the words the agent and the approver read. */
  impact_text?: string;
  /** An overwrite refused for want of a fresh preview (this refusal is one). */
  preview_required?: boolean;
}

/** Who a controller push affects, as `ControllerImpact.to_dict` sends it. */
export interface ControllerImpact {
  controller: string;
  server_name: string;
  shared_owners: { agent: string; same_code: boolean }[];
  /** `null` = could not check — never read as "no bots". */
  live_bots: { bot_name: string; config_ids: string[] }[] | null;
  live_bots_error: string;
  server_had_copy: boolean;
}

export interface StrategyCard {
  slug: string;
  name: string;
  description: string;
  status: string;
}

export interface SkillBody extends SkillCard {
  body: string;
  files: string[];
}

// Strategy = a playbook that loops under an Agent. Holds the operational
// history: sessions, experiments, live instances, config and learnings.
export interface StrategyDetail {
  slug: string;
  agent_slug: string;
  name: string;
  description: string;
  strategy_md: string;
  config: Record<string, unknown>;
  default_trading_context: string;
  learnings: string;
  status: string;
  agent_id: string;
  sessions: StrategySessionInfo[];
  experiments: ExperimentInfo[];
  instances: RunningInstance[];
  /** Same meaning as `StrategySummary.unavailable`. */
  unavailable?: StrategyUnavailable;
}

export interface SnapshotSummary {
  tick: number;
  timestamp: string;
  file: string;
}

/**
 * One run of one strategy — a session or a dry run (FEAT-099).
 *
 * The unit the Lab is keyed on. Snake-cased like every other payload here, and
 * deliberately carrying **no money**: pricing a run is a per-session backend
 * fan-out, and putting it on a rail that polls every five seconds would either
 * make the rail slow or make it lie. A run's PnL is read in the run overview,
 * from the strategy's `/performance` query.
 */
export interface AgentRunRow {
  /**
   * `s:3` | `e:1` | `d:abc123` | `c:7f3a` — the `?run=` value, unique across
   * every kind. The pre-FEAT-111 form (`s3`) is still parsed, never written.
   */
  run_id: string;
  /**
   * The two kinds that live under a strategy, and the two that do not: a
   * delegation's own record and a conversation (FEAT-111).
   */
  kind: "session" | "experiment" | "delegation" | "conversation";
  /** The opaque half of `run_id`: `"3"` for a loop run, an id for the rest. */
  id: string;
  /** The ordinal, for the two kinds that have one. `0` otherwise. */
  number: number;
  agent_id: string;
  /** `running|paused|stopped|interrupted|idle|error`. */
  status: string;
  /** `dry_run` | `run_once` for an experiment; empty for a session. */
  execution_mode: string;
  tick_count: number;
  snapshot_count: number;
  /** Epoch **seconds** — `journal.md`'s ctime, not the first tick's time. */
  started_at: number | null;
  /** Epoch **seconds**, or null while the run is still live. */
  ended_at: number | null;
  error: boolean;
  /**
   * Whether `actions.jsonl` exists for this run at all.
   *
   * A run written before FEAT-097 has none, and nothing is backfilled — so its
   * ticks must read as *unrecorded*, never as ticks that did nothing.
   */
  has_actions_log: boolean;
  /** Empty for a delegation and a conversation: a strategy is a loop concept. */
  strategy_slug: string;
  strategy_name: string;
  /**
   * What this run was about, for the kinds with no strategy to name — a
   * conversation's title, a delegation's ask. Empty for a loop run.
   */
  title: string;
}

export const agentsApi = {
  // ── Agents (identity + brain) ──

  getAgents: () => apiFetch<AgentSummary[]>("/api/v1/agents"),

  /**
   * Who owns which trading, and what their loop is doing (FEAT-096).
   *
   * Deliberately not `getAgents`, which fans out a performance fetch per
   * session of every strategy and is the most expensive read in the app. This
   * one makes no Hummingbot call at all, which is what lets `/bots` poll it.
   */
  getFleetMap: async (): Promise<FleetMap> => {
    const data = await apiFetch<{ owners: FleetOwnerWire[]; deeds?: DeedIndexWire }>(
      "/api/v1/agents/fleet-map",
    );
    const owners: FleetOwner[] = (data.owners ?? []).map((owner) => ({
      runKey: owner.run_key,
      agentSlug: owner.agent_slug,
      agentName: owner.agent_name,
      strategySlug: owner.strategy_slug,
      strategyName: owner.strategy_name,
      namespace: owner.namespace,
      declaredBots: owner.declared_bots ?? [],
      agentIds: owner.agent_ids ?? [],
      live: owner.live
        ? {
            agentId: owner.live.agent_id,
            sessionNum: owner.live.session_num,
            status: owner.live.status,
            tickCount: owner.live.tick_count,
            lastTickAt: owner.live.last_tick_at,
            frequencySec: owner.live.frequency_sec,
            lastAction: owner.live.last_action,
            lastDid: owner.live.last_did ?? null,
            lastError: owner.live.last_error,
          }
        : null,
    }));
    const deeds: DeedIndex = { bots: {}, since: data.deeds?.since ?? 0 };
    for (const [name, ref] of Object.entries(data.deeds?.bots ?? {})) {
      deeds.bots[name] = { runKey: ref.run_key, runId: ref.run_id ?? "", at: ref.at ?? 0 };
    }
    return { owners, deeds };
  },

  getAgent: (slug: string) =>
    apiFetch<AgentDetail>(`/api/v1/agents/${encodeURIComponent(slug)}`),

  /** Identity + the four libraries, for the panel behind a conversation. */
  getAgentBrain: (slug: string) =>
    apiFetch<AgentBrain>(`/api/v1/agents/${encodeURIComponent(slug)}/brain`),

  /** The agent's controllers, each with its verdict on `serverName`. */
  getAgentControllers: (slug: string, serverName: string) =>
    apiFetch<{
      agent: string;
      server_name: string;
      controllers: ControllerStatusRow[];
    }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/controllers?server_name=${encodeURIComponent(serverName)}`,
    ),

  /** Upload a controller where the server lacks it; `overwrite` replaces a
   *  drifted server copy (backed up first). */
  syncAgentController: (
    slug: string,
    name: string,
    serverName: string,
    overwrite = false,
  ) =>
    apiFetch<ControllerActionResult>(
      `/api/v1/agents/${encodeURIComponent(slug)}/controllers/${encodeURIComponent(name)}/sync`,
      {
        method: "POST",
        body: JSON.stringify({ server_name: serverName, overwrite }),
      },
    ),

  /** Save a style as a config on the server (`{name}__{sample}`). */
  uploadAgentControllerConfig: (
    slug: string,
    name: string,
    sample: string,
    serverName: string,
    overwrite = false,
  ) =>
    apiFetch<ControllerActionResult>(
      `/api/v1/agents/${encodeURIComponent(slug)}/controllers/${encodeURIComponent(name)}/configs/${encodeURIComponent(sample)}`,
      {
        method: "POST",
        body: JSON.stringify({ server_name: serverName, overwrite }),
      },
    ),

  getAgentControllerSource: (slug: string, name: string) =>
    apiFetch<{
      name: string;
      controller_type: string | null;
      description: string;
      shared: boolean;
      digest: string;
      source: string;
      styles: string[];
    }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/controllers/${encodeURIComponent(name)}/source`,
    ),

  getAgentControllerSample: (slug: string, name: string, sample: string) =>
    apiFetch<{ name: string; sample: string; yaml: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/controllers/${encodeURIComponent(name)}/configs/${encodeURIComponent(sample)}`,
    ),

  getAgentSkill: (slug: string, name: string) =>
    apiFetch<SkillBody>(
      `/api/v1/agents/${encodeURIComponent(slug)}/skills/${encodeURIComponent(name)}`,
    ),

  getAgentMemory: (slug: string, name: string) =>
    apiFetch<{ name: string; body: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/memories/${encodeURIComponent(name)}`,
    ),

  /** What this Agent learned *you* ask it for — the openers on an empty chat. */
  getAgentStarters: (slug: string) =>
    apiFetch<{ starters: StarterRow[] }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/starters`,
    ),

  /** Add a playbook to this Agent's own library. Publishing to the shared one
   *  is Condor's decision alone, so there is no `shared` flag to pass. */
  createAgentSkill: (
    slug: string,
    data: {
      name: string;
      description: string;
      when_to_use: string;
      body: string;
      references_routine?: string;
    },
  ) =>
    apiFetch<{ saved: boolean; name: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/skills`,
      { method: "POST", body: JSON.stringify(data) },
    ),

  /** Patch a playbook. Omitted fields are left alone; `references_routine: ""`
   *  unlinks the routine, which is why it is only sent when it changed. */
  updateAgentSkill: (
    slug: string,
    name: string,
    data: {
      description?: string;
      when_to_use?: string;
      body?: string;
      references_routine?: string;
    },
  ) =>
    apiFetch<SkillBody>(
      `/api/v1/agents/${encodeURIComponent(slug)}/skills/${encodeURIComponent(name)}`,
      { method: "PUT", body: JSON.stringify(data) },
    ),

  /** Switch one playbook, routine or tool off for this Agent, or back on.
   *
   *  Curation, not deletion: the item stays on disk, stays in this panel and
   *  stays editable, and every other Agent reading the same shared file is
   *  untouched. It takes effect on the Agent's next tick or next session — a
   *  system prompt already built is not rewritten, and a tool is mounted when
   *  the MCP subprocess starts, so a muted one goes with the next session. */
  setAgentMute: (
    slug: string,
    data: { kind: "skill" | "routine" | "tool"; name: string; muted: boolean },
  ) =>
    apiFetch<{ kind: string; name: string; muted: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/mutes`,
      { method: "PUT", body: JSON.stringify(data) },
    ),

  deleteAgentSkill: (slug: string, name: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/skills/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),

  /** Accept the offered playbook: it becomes an ordinary skill in this Agent's
   *  own library. The only path by which a proposal ever reaches a prompt. */
  acceptAgentSkillProposal: (slug: string) =>
    apiFetch<{ accepted: boolean; name: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/skill-proposals/accept`,
      { method: "POST" },
    ),

  /** Throw the offered playbook away. The library is untouched either way. */
  discardAgentSkillProposal: (slug: string) =>
    apiFetch<{ discarded: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/skill-proposals`,
      { method: "DELETE" },
    ),

  /** Create or overwrite one of your memories with this Agent — the store keys
   *  on the slug, so one call serves both. */
  saveAgentMemory: (
    slug: string,
    name: string,
    data: { content: string; description: string; type?: string },
  ) =>
    apiFetch<{ saved: boolean; name: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/memories/${encodeURIComponent(name)}`,
      { method: "PUT", body: JSON.stringify(data) },
    ),

  deleteAgentMemory: (slug: string, name: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/memories/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),

  updateAgentMd: (slug: string, content: string) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}`,
      {
        method: "PUT",
        body: JSON.stringify({ content }),
      },
    ),

  /** Set or clear the Agent's server pin and model. An empty `server_name`
   *  clears the pin, so the Agent follows whatever server the chat is pointed
   *  at; an empty `agent_key` clears the model, falling back to the chat's. */
  updateAgentConfig: (
    slug: string,
    data: {
      server_name?: string;
      server_required?: boolean;
      agent_key?: string;
    },
  ) =>
    apiFetch<{
      updated: boolean;
      server_name: string;
      server_required: boolean;
      agent_key: string;
    }>(`/api/v1/agents/${encodeURIComponent(slug)}/config`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  deleteAgent: (slug: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}`,
      {
        method: "DELETE",
      },
    ),

  // ── Strategies (playbooks that loop under an Agent) ──

  getStrategy: (slug: string, sslug: string) =>
    apiFetch<StrategyDetail>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}`,
    ),

  /** Materialize the Agent's default playbook (idempotent) so it can be tuned/looped. */
  createDefaultStrategy: (slug: string) =>
    apiFetch<StrategySummary>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/default`,
      { method: "POST" },
    ),

  createStrategy: (
    slug: string,
    data: {
      name: string;
      description?: string;
      instructions?: string;
      agent_key?: string;
      default_trading_context?: string;
      config?: Record<string, unknown>;
    },
  ) =>
    apiFetch<StrategySummary>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies`,
      { method: "POST", body: JSON.stringify(data) },
    ),

  updateStrategyMd: (slug: string, sslug: string, content: string) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}`,
      { method: "PUT", body: JSON.stringify({ content }) },
    ),

  deleteStrategy: (slug: string, sslug: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}`,
      { method: "DELETE" },
    ),

  getStrategyPerformance: (slug: string, sslug: string) =>
    apiFetch<AgentPerformanceResponse>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/performance`,
    ),

  getStrategySessionExecutors: (
    slug: string,
    sslug: string,
    sessionNum: number,
  ) =>
    apiFetch<{
      executors: AgentExecutorRow[];
      performance: AgentPerformance;
      // Realized-PnL curve sliced from the session's bot-ownership window. Derived
      // from the bots' own history, not from the journal's per-tick snapshots,
      // which only record what the aggregator believed at the time.
      pnl_series?: { timestamp: string; pnl: number; volume: number }[];
      /** What this run put into the world (FEAT-100). Empty for a run that
       *  deployed nothing, and for a session whose server is unreachable. */
      deployments?: DeploymentRow[];
      /** Why `executors` is empty when it is not "traded nothing" (CORR-706). */
      unavailable?: StrategyUnavailable;
    }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/executors`,
    ),

  startStrategy: (
    slug: string,
    sslug: string,
    config: Record<string, unknown> = {},
    trading_context = "",
  ) =>
    apiFetch<{ started: boolean; agent_id: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/start`,
      { method: "POST", body: JSON.stringify({ config, trading_context }) },
    ),

  stopStrategy: (slug: string, sslug: string) =>
    apiFetch<{ stopped: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/stop`,
      { method: "POST" },
    ),

  pauseStrategy: (slug: string, sslug: string) =>
    apiFetch<{ paused: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/pause`,
      { method: "POST" },
    ),

  resumeStrategy: (slug: string, sslug: string) =>
    apiFetch<{ resumed: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/resume`,
      { method: "POST" },
    ),

  /**
   * Whether this strategy's loop comes back after Condor restarts.
   *
   * Writes the stored config (so the next session starts opted in) *and* every
   * live engine's status file (so the session already running is covered
   * without being stopped first). `applied_to_running` says how many of the
   * latter there were, which is what lets the caller say "from the next
   * restart" rather than guess.
   */
  setRestartOnBoot: (slug: string, sslug: string, enabled: boolean) =>
    apiFetch<{ restart_on_boot: boolean; applied_to_running: number }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/restart-on-boot`,
      { method: "POST", body: JSON.stringify({ enabled }) },
    ),

  /**
   * Attribute an already-running bot to this strategy's newest session.
   *
   * The repair for a run whose ownership claim never landed — every run from
   * before the ACP arguments fix, whose `owned_bots.json` was never written, so
   * the fleet trades on unattributed and every money surface reads $0.
   *
   * `since` is what makes it a back-fill rather than a fresh start: the ledger
   * slices PnL over the window it owns the bot for, so claiming at "now"
   * credits the strategy with nothing it has already made.
   */
  claimBot: (slug: string, sslug: string, botName: string, since = 0) =>
    apiFetch<{ claimed: string; session: string; owned: string[] }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/claim-bot`,
      { method: "POST", body: JSON.stringify({ bot_name: botName, since }) },
    ),

  /**
   * Take a bot back off this strategy — the undo of `claimBot`.
   *
   * Claiming was one-way, so a misclick put another strategy's trading on this
   * one's book permanently. It clears the bot from *every* session of the
   * strategy, not just the newest: ownership is re-derived on each boot from
   * prior sessions and from recorded deploys, so a single-session delete undoes
   * itself on the next restart.
   *
   * `sessions` names the runs that actually changed and `live_runs` how many
   * running loops were corrected in memory — an empty pair means the bot was
   * not this strategy's to begin with.
   */
  unclaimBot: (slug: string, sslug: string, botName: string) =>
    apiFetch<{ unclaimed: string; sessions: string[]; live_runs: number }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/unclaim-bot`,
      { method: "POST", body: JSON.stringify({ bot_name: botName }) },
    ),

  updateStrategyLearnings: (slug: string, sslug: string, content: string) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/learnings`,
      { method: "PUT", body: JSON.stringify({ content }) },
    ),

  /**
   * Every run this agent ever had, across its strategies, newest first
   * (FEAT-099).
   *
   * The rail's whole query. Like `getFleetMap`, it makes no Hummingbot call —
   * which is what lets the Lab poll it.
   */
  /**
   * Every stretch of work an agent has done, newest first (FEAT-111).
   *
   * `limit` is the rail's window over chats and delegations, not a filter: a
   * chatty install has hundreds of conversations, and the rail asks for a
   * bigger page rather than pulling the archive on every five-second poll.
   * Loop runs (sessions, experiments) are always carried, so the list can be
   * longer than `limit` (CORR-376).
   */
  getAgentRuns: async (slug: string, limit?: number): Promise<AgentRunRow[]> => {
    const data = await apiFetch<{ runs: AgentRunRow[] }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/runs${limit ? `?limit=${limit}` : ""}`,
    );
    return data.runs ?? [];
  },

  getSessionJournal: (slug: string, sslug: string, sessionNum: number) =>
    apiFetch<{ content: string }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/journal`,
    ),

  getSessionSnapshots: (slug: string, sslug: string, sessionNum: number) =>
    apiFetch<{ snapshots: SnapshotSummary[] }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/snapshots`,
    ),

  /**
   * What this session actually *did*, oldest-first (FEAT-097).
   *
   * Structured rows, so nothing downstream runs a regex over markdown to find
   * out. Makes no Hummingbot call — it is one tail read of a JSONL sidecar.
   */
  getSessionActions: (slug: string, sslug: string, sessionNum: number, limit?: number) =>
    apiFetch<{ actions: AgentActionRow[] }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/actions${limit ? `?limit=${limit}` : ""}`,
    ),

  getSessionCanvas: (slug: string, sslug: string, sessionNum: number) =>
    apiFetch<SessionCanvas>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/canvas`,
    ),

  // The live report this session keeps — `{report: null}` when it has none.
  getSessionReport: (slug: string, sslug: string, sessionNum: number) =>
    apiFetch<{ report: ReportSummary | null }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/report`,
    ),

  getSnapshot: (
    slug: string,
    sslug: string,
    sessionNum: number,
    tick: number,
  ) =>
    apiFetch<{ content: string; tick: number }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/sessions/${sessionNum}/snapshots/${tick}`,
    ),

  // ── Experiments ──

  getExperiment: (slug: string, sslug: string, expNum: number) =>
    apiFetch<{ content: string; number: number }>(
      `/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/experiments/${expNum}`,
    ),
};
