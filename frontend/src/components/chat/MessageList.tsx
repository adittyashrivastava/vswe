import { useEffect, useRef } from "react";
import { Message as MessageComponent } from "./Message";
import type { Message, ToolCall } from "@/types/message";

interface Props {
  messages: Message[];
  /** When true, skip auto-scroll to bottom (e.g. while loading older pages). */
  suppressAutoScroll?: boolean;
}

interface GroupedMessage extends Message {
  attached_tool_calls?: ToolCall[];
}

/**
 * Groups tool messages with the PRECEDING assistant message.
 * The backend stores: assistant (reasoning + tool requests), tool, tool, ...
 * Tool results become compact collapsible blocks under the assistant's text.
 */
function groupMessages(messages: Message[]): GroupedMessage[] {
  const grouped: GroupedMessage[] = [];

  for (const msg of messages) {
    if (msg.role === "tool") {
      // Attach to the last assistant message in the grouped list
      const last = grouped[grouped.length - 1];
      if (last && last.role === "assistant") {
        if (!last.attached_tool_calls) {
          last.attached_tool_calls = [];
        }
        last.attached_tool_calls.push({
          id: msg.id || msg.message_id || crypto.randomUUID(),
          name: msg.tool_name || "tool",
          input: msg.tool_input || {},
          output: msg.tool_output || msg.content,
          status: "completed",
        });
      } else {
        // Orphan tool message with no preceding assistant — render as
        // a synthetic assistant message with just tool calls
        grouped.push({
          id: `synthetic-${msg.id}`,
          session_id: msg.session_id,
          role: "assistant",
          content: "",
          created_at: msg.created_at,
          attached_tool_calls: [
            {
              id: msg.id || msg.message_id || crypto.randomUUID(),
              name: msg.tool_name || "tool",
              input: msg.tool_input || {},
              output: msg.tool_output || msg.content,
              status: "completed",
            },
          ],
        });
      }
      continue;
    }

    grouped.push({ ...msg });
  }

  return grouped;
}

export function MessageList({ messages, suppressAutoScroll }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const prevLastIdRef = useRef<string | null>(null);

  // Auto-scroll only when the LAST message changes (new message at the bottom),
  // not when older messages are prepended at the top.
  useEffect(() => {
    const lastMsg = messages[messages.length - 1];
    const lastId = lastMsg?.id || lastMsg?.message_id || null;
    if (suppressAutoScroll) {
      prevLastIdRef.current = lastId;
      return;
    }
    if (lastId !== prevLastIdRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
    prevLastIdRef.current = lastId;
  }, [messages, suppressAutoScroll]);

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
