export type HoldingItem = {
  fund_code: string;
  fund_name: string;
  shares: number;
  cost_basis: number;
  market_value: number;
  pnl: number;
  pnl_pct: number;
  is_stale: boolean;
  as_of_date: string | null;
};

export type HoldingsResponse = {
  items: HoldingItem[];
};

export type ValueHistoryPoint = {
  date: string;
  value: number;
};

export type Transaction = {
  id: number;
  fund_code: string;
  action: "buy" | "sell";
  amount: number | null;
  shares: number;
  proceeds: number | null;
  nav_at_trade: number;
  fee: number;
  traded_at: string;
};

export type TransactionList = {
  total: number;
  items: Transaction[];
};

export type CurrentValuation = {
  index_code: string;
  pe: number;
  pb: number;
  pe_percentile: number | null;
  pb_percentile: number | null;
  as_of_date: string;
};

export type HistoricalValuation = {
  date: string;
  pe: number;
  pb: number;
  pe_percentile: number | null;
  pb_percentile: number | null;
  effective_window: number;
};

export type NewsItem = {
  id: number;
  title: string;
  url: string;
  published_at: string;
  summary: string | null;
  summary_status: string;
  event_type: string | null;
};

export type NewsGroup = {
  fund_code: string;
  items: NewsItem[];
};

export type NewsFeedResponse = {
  groups: NewsGroup[];
};

export type NotificationSettings = {
  recipient_email: string;
  reminder_day: number;
  reference_index_code: string | null;
  base_monthly_amount: number;
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_username: string | null;
  smtp_from: string | null;
};

export type JobRun = {
  id: number;
  job_name: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
  details: Record<string, unknown>;
};
