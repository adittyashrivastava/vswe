export interface Job {
  id: string;
  session_id: string;
  status: "queued" | "provisioning" | "running" | "completed" | "failed" | "cancelled";
  instance_type: string;
  repo_url: string;
  branch: string;
  command: string;
  cost: number;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  checkpoints: Checkpoint[];
  logs_url?: string;
}

export interface Checkpoint {
  id: string;
  job_id: string;
  step: number;
  label: string;
  status: "pending" | "running" | "completed" | "failed";
  output?: string;
  created_at: string;
}
