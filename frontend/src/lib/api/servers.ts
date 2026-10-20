/** Server inventory: list, add, edit, default selection and the relaunch probe. */

import { apiFetch } from "@/lib/api";

// ── Types ──

/** Someone a server is shared with, named rather than numbered. */
export interface ServerMember {
  user_id: number;
  display_name: string;
}

export interface ServerInfo {
  name: string;
  host: string;
  port: number;
  online: boolean;
  permission: string;
  /** The server commands fall back to when none is named — shared with Telegram. */
  is_default: boolean;
  /**
   * Who else reaches this server (FEAT-088). The backend populates it only for
   * the owner and for an admin, so an empty list on a server you merely trade
   * on means "not your business" rather than "nobody" — which is why only the
   * owner's card renders the line.
   */
  shared_with?: ServerMember[];
}

// ── The running build ──

/**
 * Whether the running process is older than the code on disk.
 *
 * A Condor update lands the new commit and rebuilds this bundle but does not
 * exec the server (`condor/updates/run.py`), so until someone relaunches it the
 * browser is new and the backend is old. Everything but `required` is absent
 * when nothing is owed.
 */
export interface RelaunchResponse {
  required: boolean;
  branch?: string;
  /** Short sha the process is still running. */
  from_commit?: string;
  /** Short sha on disk, waiting for the relaunch. */
  target_commit?: string;
  at?: number | null;
}

export const serversApi = {
  getServers: () => apiFetch<ServerInfo[]>("/api/v1/servers"),

  /** Whether an update has landed that this process is not running yet. */
  getRelaunch: () => apiFetch<RelaunchResponse>("/api/v1/meta/relaunch"),

  // ── Settings ──

  getSettingsServers: () => apiFetch<ServerInfo[]>("/api/v1/settings/servers"),

  addServer: (data: {
    name: string;
    host: string;
    port: number;
    username: string;
    password: string;
  }) =>
    apiFetch<{ added: boolean; name: string }>("/api/v1/settings/servers", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateServer: (
    name: string,
    data: {
      host?: string;
      port?: number;
      username?: string;
      password?: string;
    },
  ) =>
    apiFetch<{ updated: boolean }>(
      `/api/v1/settings/servers/${encodeURIComponent(name)}`,
      {
        method: "PUT",
        body: JSON.stringify(data),
      },
    ),

  deleteServer: (name: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/settings/servers/${encodeURIComponent(name)}`,
      {
        method: "DELETE",
      },
    ),

  setDefaultServer: (name: string) =>
    apiFetch<{ default: boolean }>(
      `/api/v1/settings/servers/${encodeURIComponent(name)}/default`,
      {
        method: "POST",
      },
    ),
};
