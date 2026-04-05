// packages/contracts/src/index.ts
export type MarketStatus = "open" | "closed" | "resolved";
export type RunMode = "replay" | "paper" | "live";
export type RiskDecision = "allow" | "block" | "reduce_only";
export type OrderIntentStatus =
  | "proposed"
  | "approved"
  | "rejected"
  | "submitted"
  | "filled"
  | "partially_filled"
  | "cancelled";

export interface MarketState {
  ticker: string;
  timestamp: string;        // ISO string on the TS side
  yesBid: number | null;
  yesAsk: number | null;
  yesBidSize: number | null;
  yesAskSize: number | null;
  lastPrice: number | null;
  volume24h: number | null;
  openInterest: number | null;
  closeTime: string | null;
  status: MarketStatus;
  source: string;
}

export interface FeatureVector {
  ticker: string;
  timestamp: string;
  midPrice: number | null;
  spreadPct: number | null;
  spreadTicks: number | null;
  bidAskImbalance: number | null;
  timeToCloseHours: number | null;
}