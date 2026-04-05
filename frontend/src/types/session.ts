export interface Session {
  session_id: string;
  user_id: string;
  repo_url: string | null;
  model: string;
  state: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionCreate {
  repo_url?: string;
  model?: string;
  title?: string;
}
