import { useEffect, useState, useCallback, useRef } from "react";
import { wsManager } from "@/lib/websocket";
import type { Message, WebSocketEvent, ToolCall } from "@/types/message";

interface UseWebSocketReturn {
  sendMessage: (content: string) => void;
  messages: Message[];
  isConnected: boolean;
  events: WebSocketEvent[];
  activeToolCalls: ToolCall[];
  completedToolCalls: ToolCall[];
  statusText: string | null;
  streamingContent: string;
  isProcessing: boolean;
  pendingPlan: string | null;
}

export function useWebSocket(sessionId: string | undefined): UseWebSocketReturn {
  const [messages, setMessages] = useState<Message[]>([]);
  const [events, setEvents] = useState<WebSocketEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [activeToolCalls, setActiveToolCalls] = useState<ToolCall[]>([]);
  const [completedToolCalls, setCompletedToolCalls] = useState<ToolCall[]>([]);
  const [statusText, setStatusText] = useState<string | null>(null);
  const [streamingContent, setStreamingContent] = useState("");
  const [isProcessing, setIsProcessing] = useState(false);
  const [pendingPlan, setPendingPlan] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!sessionId) return;

    wsManager.connect(sessionId);

    intervalRef.current = setInterval(() => {
      setIsConnected(wsManager.isConnected);
    }, 500);

    const unsubscribe = wsManager.onEvent((event) => {
      setEvents((prev) => [...prev, event]);

      switch (event.type) {
        case "token":
          if (event.data.content) {
            setStreamingContent((prev) => prev + event.data.content);
          }
          break;

        case "status":
          setStatusText(event.data.status || event.data.content || null);
          setIsProcessing(true);
          break;

        case "tool_call":
          if (event.data.tool_call) {
            setActiveToolCalls((prev) => [...prev, event.data.tool_call!]);
          }
          break;

        case "tool_result":
          if (event.data.tool_result) {
            const result = event.data.tool_result;
            // Move from active to completed with result data
            setActiveToolCalls((prev) => {
              const updated = prev.map((tc) =>
                tc.id === result.id
                  ? { ...tc, output: result.output, status: (result.error ? "error" : "completed") as ToolCall["status"] }
                  : tc,
              );
              // Move completed ones to the completed list
              const done = updated.filter((tc) => tc.status === "completed" || tc.status === "error");
              const still_active = updated.filter((tc) => tc.status !== "completed" && tc.status !== "error");
              setCompletedToolCalls((prev) => [...prev, ...done]);
              return still_active;
            });
          }
          break;

        case "assistant_message":
          // Intermediate assistant message — commit it (with accumulated
          // tool calls) into the message list and reset streaming state
          // so the next iteration starts clean.
          if (event.data.message) {
            setCompletedToolCalls((prevCompleted) => {
              setActiveToolCalls((prevActive) => {
                const allToolCalls = [...prevCompleted, ...prevActive];
                const msg = event.data.message!;
                if (allToolCalls.length > 0) {
                  msg.tool_calls = allToolCalls;
                }
                setMessages((prev) => [...prev, msg]);
                return [];
              });
              return [];
            });
          }
          setStreamingContent("");
          break;

        case "plan_review":
          if (event.data.plan) {
            setPendingPlan(event.data.plan);
          }
          setStreamingContent("");
          setStatusText(null);
          setIsProcessing(false);
          break;

        case "done":
          if (event.data.message) {
            // Attach any remaining tool calls to the final message
            setCompletedToolCalls((prevCompleted) => {
              setActiveToolCalls((prevActive) => {
                const allToolCalls = [...prevCompleted, ...prevActive];
                const msg = event.data.message!;
                if (allToolCalls.length > 0) {
                  msg.tool_calls = allToolCalls;
                }
                setMessages((prev) => [...prev, msg]);
                return [];
              });
              return [];
            });
          } else {
            setMessages((prev) => prev); // no-op, just to trigger re-render
            setActiveToolCalls([]);
            setCompletedToolCalls([]);
          }
          setStreamingContent("");
          setStatusText(null);
          setIsProcessing(false);
          break;

        case "error":
          setStatusText(null);
          setStreamingContent("");
          setIsProcessing(false);
          break;
      }
    });

    return () => {
      unsubscribe();
      wsManager.disconnect();
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [sessionId]);

  const sendMessage = useCallback(
    (content: string) => {
      const userMessage: Message = {
        id: crypto.randomUUID(),
        session_id: sessionId || "",
        role: "user",
        content,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMessage]);
      setCompletedToolCalls([]);
      setActiveToolCalls([]);
      setPendingPlan(null);
      wsManager.send(content);
    },
    [sessionId],
  );

  return {
    sendMessage,
    messages,
    isConnected,
    events,
    activeToolCalls,
    completedToolCalls,
    statusText,
    streamingContent,
    isProcessing,
    pendingPlan,
  };
}
