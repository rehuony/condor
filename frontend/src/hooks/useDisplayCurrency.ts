import { useSyncExternalStore } from "react";

import {
  CURRENCY_SYMBOLS,
  getDisplayCurrency,
  setDisplayCurrency,
  subscribeDisplayCurrency,
} from "@/lib/display-currency";

export function useDisplayCurrency() {
  const currency = useSyncExternalStore(subscribeDisplayCurrency, getDisplayCurrency);

  return {
    currency,
    setCurrency: setDisplayCurrency,
    currencySymbol: CURRENCY_SYMBOLS[currency],
  };
}
