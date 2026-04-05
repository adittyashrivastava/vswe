import type { WebSocketEvent } from "@/types/message";

type EventCallback = (event: WebSocketEvent) => void;

export class WebSocketManager {
  private ws: WebSocket | null = null;
  private callbacks: Set<EventCallback> = new Set();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private sessionId: string | null = null;
  private _isConnected = false;

  get isConnected(): boolean {
    return this._isConnected;
  }

  connect(sessionId: string): void {
    this.disconnect();
    this.sessionId = sessionId;
    this.reconnectAttempts = 0;
    this.openConnection();
  }

  private openConnection(): void {
    if (!this.sessionId) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = import.meta.env.VITE_WS_URL || `${protocol}//${window.location.host}`;
    const token = localStorage.getItem("vswe_token");
    const url = `${host}/ws/sessions/${this.sessionId}${token ? `?token=${token}` : ""}`;

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this._isConnected = true;
      this.reconnectAttempts = 0;
    };

    this.ws.onmessage = (event) => {
      try {
        const raw = JSON.parse(event.data);

        // Normalize: backend sends flat events, frontend expects { type, data, timestamp }
        const normalized: WebSocketEvent = {
          type: raw.type,
          session_id: raw.session_id || "",
          timestamp: raw.timestamp || new Date().toISOString(),
          data: {},
        };

        // Map backend fields into the data structure the hooks expect
        switch (raw.type) {
          case "token":
            normalized.data.content = raw.content;
            break;
          case "status":
            normalized.data.status = raw.message || raw.status;
            break;
          case "tool_call":
            normalized.data.tool_call = {
              id: raw.tool_use_id || raw.tool_call_id || "",
              name: raw.name || "",
              input: raw.arguments || {},
              status: "running",
            };
            break;
          case "tool_result":
            normalized.data.tool_result = {
              id: raw.tool_use_id || raw.tool_call_id || "",
              output: raw.result || "",
              error: raw.error,
            };
            break;
          case "done":
            normalized.data.content = raw.content;
            normalized.data.message = {
              id: raw.message_id || crypto.randomUUID(),
              session_id: this.sessionId || "",
              role: "assistant",
              content: raw.content || "",
              created_at: raw.timestamp || new Date().toISOString(),
            };
            break;
          case "error":
            normalized.data.error = raw.detail || raw.message || "Unknown error";
            break;
        }

        this.callbacks.forEach((cb) => cb(normalized));
      } catch {
        console.error("Failed to parse WebSocket message:", event.data);
      }
    };

    this.ws.onclose = () => {
      this._isConnected = false;
      this.attemptReconnect();
    };

    this.ws.onerror = () => {
      this._isConnected = false;
    };
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return;
    if (!this.sessionId) return;

    this.reconnectAttempts++;
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30000);

    this.reconnectTimer = setTimeout(() => {
      this.openConnection();
    }, delay);
  }

  disconnect(): void {
    this.sessionId = null;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this._isConnected = false;
  }

  send(message: string, model?: string): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "message", content: message, model }));
    }
  }

  onEvent(callback: EventCallback): () => void {
    this.callbacks.add(callback);
    return () => {
      this.callbacks.delete(callback);
    };
  }
}

export const wsManager = new WebSocketManager();
