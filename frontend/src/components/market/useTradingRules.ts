import { useQuery } from "@tanstack/react-query";

import { marketApi } from "@/lib/api/market";

/**
 * A venue's trading rules — the pair list and the price/amount increments.
 *
 * Both the trade page and the market browser ask through this hook, so one
 * cached request per venue serves both.
 *
 * @param enabled Pass false for venues with no rules endpoint, where the
 *   request would only 502.
 */
export function useTradingRules(server: string, connector: string, enabled = true) {
  const { data } = useQuery({
    queryKey: ["trading-rules", server, connector],
    queryFn: () => marketApi.getTradingRules(server, connector),
    enabled: !!server && !!connector && enabled,
    staleTime: 5 * 60 * 1000,
  });
  return data;
}
