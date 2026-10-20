import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import { TOKEN_KEY, authHeaders } from "./auth-token";
import { queryClient } from "./queryClient";
import { clearSessionState } from "./sessionState";

export interface User {
  id: number;
  username: string;
  first_name: string;
  role: string;
}

export interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  loginWithToken: (loginToken: string) => Promise<void>;
  /** Local mode (FEAT-049): claim the local admin's session. 404s elsewhere. */
  loginLocal: () => Promise<void>;
  logout: () => void;
}

const USER_KEY = "condor_user";

/** Selected Hummingbot API server. Session state: cleared on every session boundary. */
export const SERVER_KEY = "condor_selected_server";

/**
 * Clear cached responses, the selected server, and stored forms when login
 * replaces another user's session. Re-login as the same user keeps its state.
 * Run before setUser so ServerProvider remounts into cleared storage.
 */
function resetForNewUser(incoming: User) {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return;
  try {
    if ((JSON.parse(raw) as User).id === incoming.id) return;
  } catch {
    // Cannot prove the two sessions are one person: treat it as a boundary.
  }
  localStorage.removeItem(SERVER_KEY);
  queryClient.clear();
  clearSessionState();
}

export const AuthContext = createContext<AuthState>({
  user: null,
  token: null,
  isAuthenticated: false,
  loginWithToken: async () => {},
  loginLocal: async () => {},
  logout: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

export function useAuthState(): AuthState {
  const [token, setToken] = useState<string | null>(
    () => localStorage.getItem(TOKEN_KEY),
  );
  const [user, setUser] = useState<User | null>(() => {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  });

  const loginWithToken = useCallback(async (loginToken: string) => {
    const res = await fetch("/api/v1/auth/token-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: loginToken }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Login failed");
    }
    const data = await res.json();
    resetForNewUser(data.user);
    localStorage.setItem(TOKEN_KEY, data.token);
    localStorage.setItem(USER_KEY, JSON.stringify(data.user));
    setToken(data.token);
    setUser(data.user);
  }, []);

  // Local mode: the dashboard runs on this machine with no login at all, so
  // the session is claimed rather than presented. Stores exactly what
  // loginWithToken stores — downstream there is no such thing as a "local"
  // session, only a session.
  const loginLocal = useCallback(async () => {
    const res = await fetch("/api/v1/auth/local-login", { method: "POST" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Local login failed");
    }
    const data = await res.json();
    resetForNewUser(data.user);
    localStorage.setItem(TOKEN_KEY, data.token);
    localStorage.setItem(USER_KEY, JSON.stringify(data.user));
    setToken(data.token);
    setUser(data.user);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(SERVER_KEY);
    // Every cached response belongs to the session that fetched it. Logging out
    // is a pure client-side transition (no page reload), so without this the
    // next user to log in renders the previous user's portfolio, bots, API keys
    // and conversations straight from the cache.
    queryClient.clear();
    // Order sizes, leverage, saved routine configs and starred pairs: what the
    // outgoing user typed, not how this device renders. Device preferences —
    // theme, display currency, panel layout — deliberately survive.
    clearSessionState();
    setToken(null);
    setUser(null);
  }, []);

  // Validate token on mount
  useEffect(() => {
    if (!token) return;
    fetch("/api/v1/auth/me", {
      headers: authHeaders(),
    }).then((res) => {
      // Only the server's verdict on the token ends the session (401 invalid,
      // 403 revoked). A 5xx from a proxy whose backend is restarting says
      // nothing about the token, so it is treated like the network failure below.
      if (res.status === 401 || res.status === 403) {
        logout();
      }
    }).catch(() => {
      // server not available, keep token
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    user,
    token,
    isAuthenticated: !!token && !!user,
    loginWithToken,
    loginLocal,
    logout,
  };
}
