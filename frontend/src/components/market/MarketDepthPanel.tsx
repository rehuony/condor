import { OrderBook } from "./OrderBook";
import { RecentTrades } from "./RecentTrades";

interface MarketDepthPanelProps {
  server: string;
  connector: string;
  pair: string;
}

export function MarketDepthPanel({ server, connector, pair }: MarketDepthPanelProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="min-h-0 flex-[3] overflow-hidden">
        <OrderBook server={server} connector={connector} pair={pair} />
      </div>
      <div className="min-h-0 flex-[2] overflow-hidden border-t border-[var(--color-border)]">
        <RecentTrades server={server} connector={connector} pair={pair} />
      </div>
    </div>
  );
}
