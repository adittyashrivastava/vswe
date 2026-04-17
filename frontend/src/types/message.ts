export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  tool_calls?: ToolCall[];
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  tool_output?: string;
  cost?: number;
  cost_usd?: number;
  model?: string;
  message_id?: string;
  input_tokens?: number;
  output_tokens?: number;
  created_at: string;
}

export interface ToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
  output?: string;
  status: "pending" | "running" | "completed" | "error";
  duration_ms?: number;
}

export type WebSocketEventType =
  | "status"
  | "tool_call"
  | "tool_result"
  | "token"
  | "assistant_message"
  | "plan_review"
  | "done"
  | "error";

export interface WebSocketEvent {
  type: WebSocketEventType;
  session_id: string;
  data: {
    content?: string;
    status?: string;
    tool_call?: ToolCall;
    tool_result?: { id: string; output: string; error?: string };
    message?: Message;
    plan?: string;
    error?: string;
  };
  timestamp: string;
}
