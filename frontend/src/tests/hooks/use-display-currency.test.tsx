/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { DISPLAY_CURRENCY_KEY } from "@/lib/sessionState";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.resetModules();
  localStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  localStorage.clear();
});

it.each([
  ["EUR", "EUR"],
  ["unsupported", "USDT"],
])("restores %s as %s for the hook and plain readers", async (stored, expected) => {
  localStorage.setItem(DISPLAY_CURRENCY_KEY, stored);
  const { useDisplayCurrency } = await import("@/hooks/useDisplayCurrency");
  const { getDisplayCurrency } = await import("@/lib/display-currency");
  function Reader() {
    return <span>{useDisplayCurrency().currency}</span>;
  }

  act(() => root.render(<Reader />));

  expect(container.textContent).toBe(expected);
  expect(getDisplayCurrency()).toBe(expected);
});

it("shares currency changes across React consumers, plain readers, and storage", async () => {
  const { useDisplayCurrency } = await import("@/hooks/useDisplayCurrency");
  const { getDisplayCurrency, setDisplayCurrency } = await import("@/lib/display-currency");
  function Reader() {
    const { currency, currencySymbol, setCurrency } = useDisplayCurrency();
    return <button onClick={() => setCurrency("EUR")}>{currency}:{currencySymbol}</button>;
  }

  act(() => root.render(<><Reader /><Reader /></>));
  act(() => container.querySelector("button")!.click());

  expect([...container.querySelectorAll("button")].map((button) => button.textContent))
    .toEqual(["EUR:€", "EUR:€"]);
  expect(getDisplayCurrency()).toBe("EUR");
  expect(localStorage.getItem(DISPLAY_CURRENCY_KEY)).toBe("EUR");

  act(() => setDisplayCurrency("BTC"));
  expect([...container.querySelectorAll("button")].map((button) => button.textContent))
    .toEqual(["BTC:₿", "BTC:₿"]);
  expect(getDisplayCurrency()).toBe("BTC");
  expect(localStorage.getItem(DISPLAY_CURRENCY_KEY)).toBe("BTC");
});
