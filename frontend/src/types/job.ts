export interface Job {
  job_id: string;
  session_id: string;
  status: string;
  instance_type: string | null;
  spot_price: number | null;
  script_path: string | null;
  profile: Record<string, unknown> | null;
  batch_job_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  total_cost_usd: number;
}
