import { useEffect, useRef } from "react";
import { Message as MessageComponent } from "./Message";
import type { Message, ToolCall } from "@/types/message";

interface Props {
  messages: Message[];
}

interface GroupedMessage extends Message {
  attached_tool_calls?: ToolCall[];
}

/**
 * Groups consecutive tool messages into the next assistant message.
 * The backend stores: tool, tool, tool, ..., assistant (final response).
 * Tool messages become compact collapsible blocks above the assistant's text.
 */
function groupMessages(messages: Message[]): GroupedMessage[] {
  const grouped: GroupedMessage[] = [];
  let pendingTools: ToolCall[] = [];

  for (const msg of messages) {
    if (msg.role === "tool") {
      // Buffer tool messages — they'll attach to the next assistant message
      pendingTools.push({
        id: msg.id || msg.message_id || crypto.randomUUID(),
        name: msg.tool_name || "tool",
        input: msg.tool_input || {},
        output: msg.tool_output || msg.content,
        status: "completed",
      });
      continue;
    }

    if (msg.role === "assistant" && pendingTools.length > 0) {
      // Attach buffered tool calls to this assistant message
      grouped.push({
        ...msg,
        attached_tool_calls: [...pendingTools],
      });
      pendingTools = [];
      continue;
    }

    grouped.push({ ...msg });
  }

  // If there are trailing tool messages with no following assistant message,
  // render them as a synthetic assistant message with just tool calls
  if (pendingTools.length > 0) {
    grouped.push({
      id: "pending-tools",
      session_id: "",
      role: "assistant",
      content: "",
      created_at: new Date().toISOString(),
      attached_tool_calls: pendingTools,
    });
  }

  return grouped;
}

export function MessageList({ messages }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, messages[messages.length - 1]?.content]);

  const grouped = groupMessages(messages);

  if (grouped.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm">
        Send a message to start the conversation.
      </div>
    );
  }

  return (
    <div ref={containerRef} className="space-y-4">
      {grouped.map((msg) => (
        <MessageComponent
          key={msg.id || msg.message_id}
          message={msg}
          attachedToolCalls={msg.attached_tool_calls}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
