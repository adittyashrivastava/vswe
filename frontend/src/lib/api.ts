import type { Session, SessionCreate } from "@/types/session";
import type { Message } from "@/types/message";
import type { Job } from "@/types/job";
import type { CostSummary, SessionCostBreakdown, SessionDetailedCost } from "@/types/cost";

const BASE_URL = import.meta.env.VITE_API_URL || "/api";

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem("vswe_token");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      ...getAuthHeaders(),
      ...options.headers,
    },
  });

  if (res.status === 401) {
    // Token expired or invalid — clear auth and redirect to login
    localStorage.removeItem("vswe_token");
    localStorage.removeItem("vswe_user");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

// Auth
export function getMe(): Promise<{
  user_id: string;
  github_login: string;
  name: string | null;
  avatar_url: string | null;
  email: string | null;
  orgs: string[];
}> {
  return request("/auth/me");
}

export function getAccessibleRepos(): Promise<{
  full_name: string;
  private: boolean;
  permissions: Record<string, boolean>;
}[]> {
  return request("/auth/github/repos");
}

// Sessions
export function listSessions(userId?: string): Promise<{ sessions: Session[]; count: number }> {
  if (!userId) throw new Error("User ID required to list sessions");
  const params = `?user_id=${userId}`;
  return request(`/sessions/${params}`);
}

export function createSession(data: SessionCreate): Promise<Session> {
  return request("/sessions/", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function getSession(id: string): Promise<Session> {
  return request(`/sessions/${id}`);
}

export function deleteSession(id: string): Promise<void> {
  return request(`/sessions/${id}`, { method: "DELETE" });
}

// Messages
export interface MessagePage {
  messages: Message[];
  count: number;
  last_key: string | null;
}

export function getMessages(
  sessionId: string,
  opts?: { limit?: number; lastKey?: string },
): Promise<MessagePage> {
  const limit = opts?.limit ?? 50;
  const params = new URLSearchParams({ limit: String(limit), newest_first: "true" });
  if (opts?.lastKey) params.set("last_key", opts.lastKey);
  return request(`/sessions/${sessionId}/messages?${params}`);
}

// Jobs
export function listJobs(): Promise<{ jobs: Job[]; count: number }> {
  return request("/jobs/");
}

export function getJob(id: string): Promise<Job> {
  return request(`/jobs/${id}`);
}

// Costs
export function getCostSummary(from: string, to: string): Promise<CostSummary> {
  return request(`/costs/summary?from_date=${from}&to_date=${to}`);
}

export function getSessionCosts(sessionId: string): Promise<SessionCostBreakdown> {
  return request(`/costs/sessions/${sessionId}`);
}

export function getSessionDetailedCosts(sessionId: string): Promise<SessionDetailedCost> {
  return request(`/costs/sessions/${sessionId}/detailed`);
}

// Config
export function getConfig(scope: string): Promise<Record<string, unknown>> {
  return request(`/config/${scope}`);
}

export function updateConfig(scope: string, data: Record<string, unknown>): Promise<Record<string, unknown>> {
  return request(`/config/${scope}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}
