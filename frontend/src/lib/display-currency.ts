import { DISPLAY_CURRENCY_KEY } from "@/lib/sessionState";

export type DisplayCurrency = "USDT" | "BTC" | "BRL" | "EUR";

export const CURRENCY_OPTIONS: DisplayCurrency[] = ["USDT", "BTC", "BRL", "EUR"];

export const CURRENCY_SYMBOLS: Record<DisplayCurrency, string> = {
  USDT: "$",
  BTC: "\u20BF",
  BRL: "R$",
  EUR: "\u20AC",
};

function getStoredCurrency(): DisplayCurrency {
  const stored = localStorage.getItem(DISPLAY_CURRENCY_KEY);
  if (stored && CURRENCY_OPTIONS.includes(stored as DisplayCurrency)) {
    return stored as DisplayCurrency;
  }
  return "USDT";
}

let currentCurrency: DisplayCurrency = getStoredCurrency();
const listeners = new Set<() => void>();

export function subscribeDisplayCurrency(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The same display currency for React consumers and send-time page context. */
export function getDisplayCurrency(): DisplayCurrency {
  return currentCurrency;
}

export function setDisplayCurrency(currency: DisplayCurrency) {
  currentCurrency = currency;
  localStorage.setItem(DISPLAY_CURRENCY_KEY, currency);
  listeners.forEach((listener) => listener());
}
