/** @vitest-environment jsdom */

import { useQueryClient } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Outlet, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "@/App";
import { useServer } from "@/hooks/useServer";
import { SERVER_KEY, useAuth } from "@/lib/auth";
import { queryClient } from "@/lib/queryClient";

const session = vi.hoisted(() => ({ authenticated: false }));
const loading = vi.hoisted(() => {
  let release!: () => void;
  const ready = new Promise<void>((resolve) => { release = resolve; });
  return { ready, release, shell: vi.fn(), bots: vi.fn() };
});

vi.mock("@/lib/auth", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/auth")>(),
  useAuthState: () => ({
    user: session.authenticated ? { id: 1, username: "owner", first_name: "Owner", role: "admin" } : null,
    token: session.authenticated ? "test-session" : null,
    isAuthenticated: session.authenticated,
    loginWithToken: vi.fn(),
    loginLocal: vi.fn(),
    logout: vi.fn(),
  }),
}));

vi.mock("@/pages/Login", () => ({ Login: () => <h1>Sign in</h1> }));

vi.mock("@/components/layout/AppShell", () => {
  loading.shell();
  return { AppShell: () => <main><Outlet /></main> };
});

vi.mock("@/pages/Bots", async () => {
  loading.bots();
  await loading.ready;
  return {
    Bots: function Bots() {
      const { user } = useAuth();
      const { server } = useServer();
      const location = useLocation();
      const client = useQueryClient();
      return (
        <section>
          <h1>{user?.username}'s bots on {server}</h1>
          <output>{location.pathname + location.search + location.hash}</output>
          <p>{client.getQueryData<string>(["route-test"])}</p>
        </section>
      );
    },
  };
});

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  session.authenticated = false;
  localStorage.setItem(SERVER_KEY, "desk-a");
  queryClient.setQueryData(["route-test"], "Shared cache is available");
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  loading.release();
  act(() => root.unmount());
  queryClient.clear();
  localStorage.clear();
  container.remove();
  window.history.replaceState(null, "", "/");
});

it("defers protected routes until sign-in and preserves their deep link while the page loads", async () => {
  const destination = "/bots?group=type#performance";
  window.history.replaceState(null, "", destination);
  await act(async () => root.render(<App />));

  expect(window.location.pathname).toBe("/login");
  const redirect = new URLSearchParams(window.location.search).get("redirect");
  expect(redirect).toBe(destination);
  expect(container.querySelector("h1")?.textContent).toBe("Sign in");
  expect(loading.shell).not.toHaveBeenCalled();
  expect(loading.bots).not.toHaveBeenCalled();

  session.authenticated = true;
  window.history.replaceState(null, "", redirect);
  await act(async () => root.render(<App />));
  await act(async () => {
    await import("@/components/layout/AppShell");
  });

  expect(loading.shell).toHaveBeenCalledOnce();
  expect(loading.bots).toHaveBeenCalledOnce();
  expect(container.querySelector('[role="status"]')?.getAttribute("aria-label")).toBe("Loading dashboard");
  expect(container.querySelector("output")).toBeNull();

  await act(async () => {
    loading.release();
    await import("@/pages/Bots");
  });

  expect(container.querySelector('[role="status"]')).toBeNull();
  expect(container.querySelector("h1")?.textContent).toBe("owner's bots on desk-a");
  expect(container.querySelector("output")?.textContent).toBe(destination);
  expect(container.textContent).toContain("Shared cache is available");
});
