import { useQueryClient } from "@tanstack/react-query";
import { Suspense, useEffect, useRef, useState } from "react";
import { Eye, Moon, Sun } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { ChatBubble } from "@/components/chat/ChatBubble";
import { ConnectKeysOverlay } from "@/components/ConnectKeysOverlay";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { RelaunchBanner } from "@/components/RelaunchBanner";
import { SpinnerBlock } from "@/components/ui/Spinner";
import { ChatProvider } from "@/hooks/useChat";
import { useCredentials } from "@/hooks/useCredentials";
import { usePrefetchData } from "@/hooks/usePrefetchData";
import { useServer } from "@/hooks/useServer";
import { useTheme } from "@/hooks/useTheme";
import { NAV_ITEMS, FULL_BLEED_ROUTES } from "@/lib/nav";
import { routeFacts } from "@/lib/pageFacts";
import { useViewFacts } from "@/lib/viewFacts";
import { CurrencySelector } from "./CurrencySelector";
import { NotificationBell } from "./NotificationBell";
import { ServerSelector } from "./ServerSelector";

/**
 * Full-bleed routes that carry a parameter, so an exact match cannot find them.
 *
 * The Lab (`/agents/:slug/runs`, FEAT-099) was the first: a rail, a tick spine
 * and a body, all screen-tall, under a slug the shell cannot enumerate. The
 * agent workspace absorbed it (FEAT-103) and is the same shape in every view —
 * a header, a loop bar, a spine and a body, each scrolling on its own — so the
 * Lab's pattern is replaced by an exact match on the one route. Kept as a
 * separate pattern list rather than turned into a `startsWith` over the array
 * above, because `/` is in that array and prefixes everything.
 */
const FULL_BLEED_PATTERNS = [/^\/agents\/[^/]+$/];

/**
 * The shell owns the chat state.
 *
 * There used to be two surfaces rendering a conversation — an overlay panel
 * docked to the right of every page, and the workspace at `/` — which meant
 * two doors to one thing. The panel is gone; the provider stays here so the
 * socket outlives navigation between pages and the workspace.
 */
export function AppShell() {
  return (
    <ChatProvider>
      <AppShellBody />
    </ChatProvider>
  );
}

function AppShellBody() {
  const { server } = useServer();
  const { pathname, search } = useLocation();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const { hasKeys, isLoading: keysLoading } = useCredentials();
  const navRef = useRef<HTMLElement>(null);
  const [navEdges, setNavEdges] = useState({ left: false, right: false });
  const updateNavEdges = () => {
    const nav = navRef.current;
    if (nav) setNavEdges({ left: nav.scrollLeft > 1, right: nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 1 });
  };
  useEffect(() => {
    const nav = navRef.current;
    const active = nav?.querySelector<HTMLElement>('[aria-current="page"]');
    if (nav && active) {
      const item = active.getBoundingClientRect(), bounds = nav.getBoundingClientRect();
      if (item.left < bounds.left) nav.scrollLeft -= bounds.left - item.left;
      else if (item.right > bounds.right) nav.scrollLeft += item.right - bounds.right;
    }
    updateNavEdges();
    const observer = new ResizeObserver(updateNavEdges);
    if (nav) observer.observe(nav);
    return () => observer.disconnect();
  }, [pathname]);

  // A pathname is enough: every full-bleed screen is its own route.
  // `/agents/:slug` is an ordinary padded page, deliberately not matched here.
  const isFullBleed =
    FULL_BLEED_ROUTES.includes(pathname) ||
    FULL_BLEED_PATTERNS.some((re) => re.test(pathname));

  // The home is exempt because it is the chat: the one surface that can talk a
  // new user through connecting keys, and so the last one that should be
  // hidden behind a block telling them to. `/fleet` is exempt for the weaker
  // but sufficient reason that it needs no keys either — it reads agents,
  // loops and journals, none of which is an exchange — so blocking it would
  // teach nobody anything. Matched exactly rather than by prefix, because `/`
  // prefixes every route in the app.
  const exemptRoutes = ["/routines", "/settings", "/research"];
  const showKeysOverlay =
    server && !keysLoading && hasKeys === false && pathname !== "/" &&
    !exemptRoutes.some((r) => pathname.startsWith(r));

  // ⌘K used to toggle the overlay panel. It now goes to the chat, so the
  // reflex still lands somewhere sensible instead of silently doing nothing.
  // The keystroke means a *conversation*, and the home is one again.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        navigate("/");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate]);

  // Prefetch core data (executors, bots) and subscribe to WS channels early
  usePrefetchData();

  // The route baseline for the chat's page context (FEAT-059): every page
  // gets a label and a URL-derived subject through the same seam richer
  // contributors use, so the chat never has to know about the router.
  // The client is handed over so the table can also read what each page is
  // rendering (FEAT-060) — at send time, out of the cache the page fetched
  // through, rather than from eight components each pushing their state here.
  const queryClient = useQueryClient();
  useViewFacts(() => routeFacts(pathname, search, queryClient));

  return (
    <div className="flex h-dvh min-w-0 flex-col">
      {/* Top bar: always one row. The nav is a size container: when its own
          width cannot hold every label it keeps only the current page's,
          showing the rest as icons (label in the tooltip and for screen
          readers); narrower still, the current page is an icon too, and if
          even that does not fit it scrolls under the edge fades. A phone
          gives the nav the room instead of the logo, which only repeats the
          Agents link, and the server and currency selectors. */}
      <header className="flex h-12 shrink-0 items-center gap-x-2 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-2 xl:gap-x-4 xl:px-4">
        <NavLink to="/" className="flex shrink-0 items-center gap-2 font-bold tracking-tight max-sm:hidden">
          <img src="/condor_old.jpeg" alt="Condor" className="h-6 w-6 rounded-full" />
          <span className="hidden text-sm lg:inline">Condor</span>
        </NavLink>

        <nav ref={navRef} onScroll={updateNavEdges} aria-label="Main navigation" data-overflow-left={navEdges.left} data-overflow-right={navEdges.right} className="main-navigation @container flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              title={label}
              className={({ isActive }) =>
                `flex h-10 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-3 text-sm transition-colors @max-[24rem]:px-2.5 ${
                  isActive
                    ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  <span className={isActive ? "@max-[20rem]:sr-only" : "@max-[42rem]:sr-only"}>{label}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Right: server selector + controls */}
        <div className="ml-auto flex shrink-0 items-center gap-1 xl:gap-3">
          <div className="flex items-center gap-1 max-sm:hidden xl:gap-3">
            <ServerSelector />
            <CurrencySelector />
          </div>

          <div className="flex items-center gap-1">
            {/* Inside the ChatProvider on purpose: the bell reads the same
                react-query cache the chat socket pushes live notifications
                into, and the provider opens that socket on every route — not
                only on the chat workspace — which is what makes the bell
                update without a reload wherever the user is (FEAT-048). */}
            <NotificationBell />

            <button
              onClick={toggleTheme}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-accent)]"
              title={
                theme === "dark" ? "Switch to light mode" :
                theme === "light" ? "Switch to color-blind mode" :
                "Switch to dark mode"
              }
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> :
               theme === "light" ? <Eye className="h-4 w-4" /> :
               <Moon className="h-4 w-4" />}
            </button>
          </div>
        </div>
      </header>

      {/* An update landed but this process is still the old one, so the bundle
          in the browser is ahead of the API answering it. Above `main` because
          every page needs it, and the chat
          workspace owns its own scrolling. Renders nothing the rest of the
          time, which is nearly always. */}
      <RelaunchBanner />

      {/* Main content */}
      <main
        className={`relative min-h-0 min-w-0 flex-1 ${
          isFullBleed ? "overflow-hidden" : "overflow-auto [scrollbar-gutter:stable] p-3 sm:p-6 max-md:pb-[calc(4.5rem+env(safe-area-inset-bottom))]"
        }`}
      >
        <ErrorBoundary resetKey={pathname + server}>
          <Suspense fallback={<SpinnerBlock className="h-full min-h-32" label="Loading page" />}>
            <Outlet key={server} />
          </Suspense>
        </ErrorBoundary>
        {showKeysOverlay && <ConnectKeysOverlay />}
      </main>

      {/* The quick chat on every page but `/` (FEAT-059). Inside the
          provider, outside `main`, so it neither scrolls with the page nor
          unmounts on navigation. */}
      <ChatBubble />
    </div>
  );
}
