import { QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense, useCallback, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { EXECUTION_PATH } from "@/components/chat/accountPanels";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { SpinnerBlock } from "@/components/ui/Spinner";
import { ServerContext } from "@/hooks/useServer";
import { AuthContext, SERVER_KEY, useAuth, useAuthState } from "@/lib/auth";
import { invalidateServerScopedQueries, queryClient } from "@/lib/queryClient";
import { Login } from "@/pages/Login";

const AppShell = lazy(() => import("@/components/layout/AppShell").then((m) => ({ default: m.AppShell })));
const Agents = lazy(() => import("@/pages/Agents").then((m) => ({ default: m.Agents })));
const AgentWorkspace = lazy(() => import("@/pages/AgentWorkspace").then((m) => ({ default: m.AgentWorkspace })));
const AgentRunsRedirect = lazy(() => import("@/pages/AgentWorkspace").then((m) => ({ default: m.AgentRunsRedirect })));
const AgentStrategyRedirect = lazy(() => import("@/pages/AgentWorkspace").then((m) => ({ default: m.AgentStrategyRedirect })));
const BotDetail = lazy(() => import("@/pages/BotDetail").then((m) => ({ default: m.BotDetail })));
const Bots = lazy(() => import("@/pages/Bots").then((m) => ({ default: m.Bots })));
const CreateExecutor = lazy(() => import("@/pages/CreateExecutor").then((m) => ({ default: m.CreateExecutor })));
const Dex = lazy(() => import("@/pages/Dex").then((m) => ({ default: m.Dex })));
const DexPool = lazy(() => import("@/pages/DexPool").then((m) => ({ default: m.DexPool })));
const Portfolio = lazy(() => import("@/pages/Portfolio").then((m) => ({ default: m.Portfolio })));
const ResearchFilePage = lazy(() => import("@/pages/research-file-page").then((m) => ({ default: m.ResearchFilePage })));
const Routines = lazy(() => import("@/pages/Routines").then((m) => ({ default: m.Routines })));
const Settings = lazy(() => import("@/pages/Settings").then((m) => ({ default: m.Settings })));

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  const location = useLocation();
  if (!isAuthenticated) return <Navigate to={`/login?redirect=${encodeURIComponent(location.pathname + location.search + location.hash)}`} replace />;
  return <>{children}</>;
}

/**
 * Holds the selected server for the current session.
 *
 * Mounted with a key derived from the logged-in user, so a session change tears
 * this state down and re-reads `localStorage` — which `logout` has just cleared.
 * Without the remount the selection would outlive the session (logging out and
 * back in never reloads the page) and the next user would inherit it.
 */
function ServerProvider({ children }: { children: React.ReactNode }) {
  const [server, setServer] = useState<string | null>(
    () => localStorage.getItem(SERVER_KEY),
  );
  const handleSetServer = useCallback(
    (next: string) => {
      localStorage.setItem(SERVER_KEY, next);
      setServer(next);
      // Only the two servers involved are touched, and nothing is refetched
      // here: `AppShell` renders `<Outlet key={server}>`, so the switch already
      // remounts every page against the new key. See the helper for why the
      // blanket invalidation it replaces hit the server being left.
      if (next !== server) {
        invalidateServerScopedQueries(queryClient, [server, next]);
      }
    },
    [server],
  );

  return (
    <ServerContext value={{ server, setServer: handleSetServer }}>
      {children}
    </ServerContext>
  );
}

export default function App() {
  const auth = useAuthState();

  return (
    <QueryClientProvider client={queryClient}>
      <AuthContext value={auth}>
        <ServerProvider key={auth.user?.id ?? "anon"}>
          <BrowserRouter>
            <ErrorBoundary>
              <Suspense fallback={<SpinnerBlock className="min-h-dvh" label="Loading dashboard" />}>
                <Routes>
                  <Route path="/login" element={<Login />} />
                  <Route
                    element={
                      <ProtectedRoute>
                        <AppShell />
                      </ProtectedRoute>
                    }
                  >
                    <Route path="/" element={<Agents />} />
                    {/* What every agent is doing is the conversation's Execution
                        panel now, read owner first (FEAT-114) — so this address is
                        a redirect into it rather than a page. Kept rather than
                        deleted: it is in bookmarks and in the nav people remember,
                        and `?desk=` is what makes the redirect open the section
                        instead of whatever the browser last had. */}
                    <Route
                      path="/fleet"
                      element={<Navigate to={EXECUTION_PATH} replace />}
                    />
                    {/* What every agent adds up to is the fleet scope of the
                        browser (FEAT-116) — the same records, folded once, split by
                        the level below whichever scope the reader is on. Kept as a
                        redirect rather than deleted: it is in bookmarks and in the
                        nav people remember. The three parameters it spent —
                        `?basis=`, `?from=`, `?range=` — are the ones the browser's
                        own chart reads, so a reader who lands there and sets them
                        again is setting the same three. */}
                    <Route path="/floor" element={<Navigate to="/bots" replace />} />
                    <Route path="/portfolio" element={<Portfolio />} />
                    <Route path="/bots" element={<Bots />} />
                    <Route path="/bots/:id" element={<BotDetail />} />
                    <Route path="/trade" element={<CreateExecutor />} />
                    <Route path="/dex" element={<Dex />} />
                    <Route path="/dex/:network/:address" element={<DexPool />} />
                    {/* Executors are a scope of the browser now, not a page
                        (FEAT-086). The listing this replaces was the whole
                        history, live and archived together — which is two
                        populations now, and a redirect can only choose one.
                        This one lands on the live fleet grouped by type
                        (omitting `?population` is the running default, per
                        `parsePopulation`); the terminated half is one click
                        away in the sidebar. */}
                    <Route
                      path="/executors"
                      element={<Navigate to="/bots?group=type" replace />}
                    />
                    <Route path="/executors/new" element={<Navigate to="/trade" replace />} />
                    <Route path="/executors/new-grid" element={<Navigate to="/trade?type=grid" replace />} />
                    <Route
                      path="/archived"
                      element={<Navigate to="/bots?population=terminated&group=bot" replace />}
                    />
                    <Route path="/routines" element={<Routines />} />
                    <Route path="/reports" element={<Navigate to="/routines?tab=reports" replace />} />
                    <Route path="/research/*" element={<ResearchFilePage />} />
                    {/* `/agents` has pointed at the home since the fleet grid
                        was deleted, and the home is the conversation with every
                        agent one message away — which is what somebody typing this
                        is after. The listing they might mean instead is the
                        Execution panel on that same screen (FEAT-114). */}
                    <Route path="/agents" element={<Navigate to="/" replace />} />
                    {/* One agent, one screen: every section, run and tick is a query
                        parameter on this route (FEAT-103). */}
                    <Route path="/agents/:slug" element={<AgentWorkspace />} />
                    {/* The Lab and the strategy page are views of the workspace
                        now (FEAT-103). Redirects rather than deletions: both are in
                        notification payloads and in bookmarks, and both carry a
                        query string that has to arrive intact. */}
                    <Route path="/agents/:slug/runs" element={<AgentRunsRedirect />} />
                    <Route
                      path="/agents/:slug/strategies/:sslug"
                      element={<AgentStrategyRedirect />}
                    />
                    <Route path="/settings" element={<Settings />} />
                    <Route path="/market" element={<Navigate to="/trade" replace />} />
                  </Route>
                </Routes>
              </Suspense>
            </ErrorBoundary>
          </BrowserRouter>
        </ServerProvider>
      </AuthContext>
    </QueryClientProvider>
  );
}
