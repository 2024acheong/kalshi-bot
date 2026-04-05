export type MarketStatus = "open" | "closed" | "resolved";

export interface MarketState {
  ticker: string;
  yesBid: number | null;
  yesAsk: number | null;
  volume: number | null;
  status: MarketStatus;
}
