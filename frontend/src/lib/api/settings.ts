/** Gateway, API-server, credential and voice settings. */

import { apiFetch } from "@/lib/api";

// ── Settings ──

export interface GatewayStatus {
  running: boolean;
  info: Record<string, unknown> | null;
  image?: string;
  created_at?: string;
  container_status?: string;
}

export interface GatewayNetworkInfo {
  network_id: string;
  chain: string;
  network: string;
}

export interface GatewayNetworkConfig {
  network_id: string;
  config: Record<string, unknown>;
  /** True when the server masked credential-bearing URL values (non-owner read, SEC-197). */
  redacted?: boolean;
}

export interface GatewayWalletGroup {
  chain: string;
  walletAddresses: string[];
  default_address?: string;
}

/**
 * Source-build labels of the image the API container runs. Every field is null when the
 * image carries no provenance for the rehuony/hummingbot-api repository.
 */
export interface ApiServerBuild {
  source: string | null;
  /** Full 40-character Git SHA. */
  revision: string | null;
  branch: string | null;
  /** UTC start of the build that produced the image. */
  created: string | null;
  /** True: uncommitted changes were built; null: unknown. */
  dirty: boolean | null;
}

/**
 * The API's own container (FEAT-121). Read from the running container's immutable
 * image, so a tag rebuilt on the host does not change it until the stack is redeployed.
 * Fields from older API builds may be missing.
 */
export interface ApiServerContainer {
  id: string | null;
  name: string | null;
  image: string | null;
  /** Local, immutable image ID. Never a stand-in for `digest`. */
  image_id?: string | null;
  /** Registry digest; null is normal for a locally built image. */
  digest: string | null;
  /** Docker's image creation time; a cached build can keep an older value. */
  image_created?: string | null;
  build?: ApiServerBuild | null;
  compose_project: string | null;
  compose_working_dir?: string | null;
  compose_config_files?: string | null;
}

/**
 * What a hummingbot-api server runs and how it was deployed. Offline: reading it never
 * contacts GitHub or a registry. `container` is null when Docker is unavailable or the
 * API cannot identify its own container.
 */
export interface ApiServerInfo {
  api_version: string;
  hummingbot_version: string | null;
  /** The MARKET_DATA_* knobs as the API process resolved them. Read-only. */
  market_data: Record<string, number>;
  docker_available: boolean;
  container: ApiServerContainer | null;
}

export type ApiUpdateStatus =
  | "up_to_date"
  | "update_available"
  | "ahead"
  | "diverged"
  | "unknown"
  | "error";

/**
 * The API's comparison of its running source revision with `main`. Only
 * `update_available` means a newer source exists; `error` is a failed check, not "no
 * update".
 */
export interface ApiUpdateCheck {
  status: ApiUpdateStatus;
  current_revision: string | null;
  latest_revision: string | null;
  checked_at: string;
  reason: string | null;
  compare_url: string | null;
}

/** The client defaults a deploy copies into every new bot. */
export interface ApiClientConfig {
  account_name: string;
  rate_oracle_source: { name: string };
  global_token: { global_token_name: string; global_token_symbol: string };
  rate_limits_share_pct: number;
  /** Which sources this server's bundled hummingbot knows about. */
  available_sources: string[];
}

export interface ApiClientConfigUpdate {
  rate_oracle_source?: string;
  global_token_name?: string;
  global_token_symbol?: string;
  rate_limits_share_pct?: number;
}

export interface CredentialInfo {
  connector_name: string;
  connector_type: string;
}

export interface ConnectorInfo {
  name: string;
  type: string;
  [key: string]: unknown;
}

// ── Voice Settings ──

export interface VoicePrefs {
  whisper_model: string;
  language: string | null;
  auto_send: boolean;
}

export interface VoiceSettingsResponse {
  voice: VoicePrefs;
  available_models: Record<string, string>;
  available_languages: Record<string, string>;
}

export const settingsApi = {
  getGatewayStatus: (server: string) =>
    apiFetch<GatewayStatus>(
      `/api/v1/settings/gateway/status?server=${encodeURIComponent(server)}`,
    ),

  startGateway: (server: string, data: { image: string; port?: number }) =>
    apiFetch<{ started: boolean }>(
      `/api/v1/settings/gateway/start?server=${encodeURIComponent(server)}`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    ),

  stopGateway: (server: string) =>
    apiFetch<{ stopped: boolean }>(
      `/api/v1/settings/gateway/stop?server=${encodeURIComponent(server)}`,
      {
        method: "POST",
      },
    ),

  restartGateway: (server: string) =>
    apiFetch<{ restarted: boolean }>(
      `/api/v1/settings/gateway/restart?server=${encodeURIComponent(server)}`,
      {
        method: "POST",
      },
    ),

  pullGatewayImage: (server: string, data: { image: string }) =>
    apiFetch<{ pulled: boolean; image: string }>(
      `/api/v1/settings/gateway/pull?server=${encodeURIComponent(server)}`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    ),

  getGatewayPullStatus: (server: string) =>
    apiFetch<{
      pull_operations: Record<
        string,
        {
          status: string;
          progress: string;
          duration_seconds: number;
          started_at: number;
        }
      >;
      total_operations: number;
    }>(
      `/api/v1/settings/gateway/pull-status?server=${encodeURIComponent(server)}`,
    ),

  getGatewayLogs: (server: string) =>
    apiFetch<{ logs: string }>(
      `/api/v1/settings/gateway/logs?server=${encodeURIComponent(server)}`,
    ),

  getGatewayNetworks: (server: string) =>
    apiFetch<{ networks: GatewayNetworkInfo[] }>(
      `/api/v1/settings/gateway/networks?server=${encodeURIComponent(server)}`,
    ),

  getApiServerInfo: (server: string) =>
    apiFetch<ApiServerInfo>(
      `/api/v1/settings/api/info?server=${encodeURIComponent(server)}`,
    ),

  getApiClientConfig: (server: string) =>
    apiFetch<ApiClientConfig>(
      `/api/v1/settings/api/client-config?server=${encodeURIComponent(server)}`,
    ),

  /** Contacts GitHub through the API; call only on an explicit user action. */
  checkApiUpdate: (server: string) =>
    apiFetch<ApiUpdateCheck>(
      `/api/v1/settings/api/update-check?server=${encodeURIComponent(server)}`,
    ),

  /** Partial: only the fields present are written, the rest keep their values. */
  updateApiClientConfig: (server: string, changes: ApiClientConfigUpdate) =>
    apiFetch<{ success: boolean; message: string; config: ApiClientConfig }>(
      `/api/v1/settings/api/client-config?server=${encodeURIComponent(server)}`,
      { method: "PUT", body: JSON.stringify(changes) },
    ),

  getGatewayNetworkConfig: (server: string, networkId: string) =>
    apiFetch<GatewayNetworkConfig>(
      `/api/v1/settings/gateway/networks/${encodeURIComponent(networkId)}?server=${encodeURIComponent(server)}`,
    ),

  updateGatewayNetworkConfig: (
    server: string,
    networkId: string,
    config: Record<string, unknown>,
  ) =>
    apiFetch<{ updated: boolean; network_id: string }>(
      `/api/v1/settings/gateway/networks/${encodeURIComponent(networkId)}?server=${encodeURIComponent(server)}`,
      { method: "POST", body: JSON.stringify({ config }) },
    ),

  getGatewayWallets: (server: string) =>
    apiFetch<{ wallets: GatewayWalletGroup[] }>(
      `/api/v1/settings/gateway/wallets?server=${encodeURIComponent(server)}`,
    ),

  addGatewayWallet: (
    server: string,
    data: { chain: string; private_key: string; set_default?: boolean },
  ) =>
    apiFetch<{
      added: boolean;
      chain: string;
      address: string | null;
      is_default: boolean;
    }>(
      `/api/v1/settings/gateway/wallets?server=${encodeURIComponent(server)}`,
      { method: "POST", body: JSON.stringify(data) },
    ),

  setDefaultGatewayWallet: (
    server: string,
    data: { chain: string; address: string },
  ) =>
    apiFetch<{ default: boolean }>(
      `/api/v1/settings/gateway/wallets/default?server=${encodeURIComponent(server)}`,
      { method: "POST", body: JSON.stringify(data) },
    ),

  removeGatewayWallet: (server: string, chain: string, address: string) =>
    apiFetch<{ removed: boolean }>(
      `/api/v1/settings/gateway/wallets/${encodeURIComponent(chain)}/${encodeURIComponent(address)}?server=${encodeURIComponent(server)}`,
      { method: "DELETE" },
    ),

  getCredentials: (server: string) =>
    apiFetch<{ credentials: (CredentialInfo | string)[] }>(
      `/api/v1/settings/credentials?server=${encodeURIComponent(server)}`,
    ),

  getAvailableConnectors: (server: string, type?: string) => {
    let url = `/api/v1/settings/connectors?server=${encodeURIComponent(server)}`;
    if (type) url += `&type=${encodeURIComponent(type)}`;
    return apiFetch<{ connectors: ConnectorInfo[] }>(url);
  },

  getConnectorConfigMap: (server: string, name: string) =>
    apiFetch<{ config_map: Record<string, unknown> }>(
      `/api/v1/settings/connectors/${encodeURIComponent(name)}/config-map?server=${encodeURIComponent(server)}`,
    ),

  addCredential: (
    server: string,
    data: { connector_name: string; credentials: Record<string, string> },
  ) =>
    apiFetch<{ added: boolean }>(
      `/api/v1/settings/credentials?server=${encodeURIComponent(server)}`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    ),

  deleteCredential: (server: string, connector: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/settings/credentials/${encodeURIComponent(connector)}?server=${encodeURIComponent(server)}`,
      { method: "DELETE" },
    ),

  // ── Voice Settings ──

  getVoiceSettings: () =>
    apiFetch<VoiceSettingsResponse>("/api/v1/settings/voice"),

  updateVoiceSettings: (data: Partial<VoicePrefs>) =>
    apiFetch<{ voice: VoicePrefs }>("/api/v1/settings/voice", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
};
