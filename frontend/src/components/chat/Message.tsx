import { User, Bot } from "lucide-react";
import { ToolCallBlock } from "./ToolCallBlock";
import type { Message as MessageType, ToolCall } from "@/types/message";

interface Props {
  message: MessageType;
  attachedToolCalls?: ToolCall[];
}

export function Message({ message, attachedToolCalls }: Props) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  // Don't render tool messages standalone — they're grouped into assistant messages
  if (message.role === "tool") return null;

  if (isSystem) {
    return (
      <div className="flex justify-center">
        <div className="px-3 py-1.5 text-xs text-gray-500 bg-gray-800/50 rounded-full">
          {message.content}
        </div>
      </div>
    );
  }

  // Merge tool_calls from both sources (live WebSocket + history grouping),
  // deduplicating by ID to prevent double-rendering during live sessions.
  const seenIds = new Set<string>();
  const allToolCalls: ToolCall[] = [];
  for (const tc of [...(message.tool_calls || []), ...(attachedToolCalls || [])]) {
    if (!seenIds.has(tc.id)) {
      seenIds.add(tc.id);
      allToolCalls.push(tc);
    }
  }

  const hasContent = message.content && message.content.trim();
  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      {/* Avatar */}
      <div
        className={`flex-shrink-0 w-7 h-7 rounded-md flex items-center justify-center ${
          isUser ? "bg-blue-600" : "bg-gray-700"
        }`}
      >
        {isUser ? (
          <User className="w-4 h-4 text-white" />
        ) : (
          <Bot className="w-4 h-4 text-gray-300" />
        )}
      </div>

      {/* Content */}
      <div className={`max-w-[75%] space-y-2 ${isUser ? "items-end" : "items-start"}`}>
        {/* Message text (reasoning) — shown before tool calls */}
        {hasContent && (
          <div
            className={`rounded-lg px-3.5 py-2.5 text-sm leading-relaxed ${
              isUser
                ? "bg-blue-600 text-white"
                : "bg-gray-800 text-gray-200 border border-gray-700/50"
            }`}
          >
            <MessageContent content={message.content} isUser={isUser} />
          </div>
        )}

        {/* Tool calls — compact collapsible blocks below the reasoning */}
        {!isUser && allToolCalls.length > 0 && (
          <div className="space-y-1">
            {allToolCalls.map((tc) => (
              <ToolCallBlock key={tc.id} toolCall={tc} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MessageContent({ content, isUser }: { content: string; isUser: boolean }) {
  const parts = content.split(/(```[\s\S]*?```|`[^`]+`)/g);

  return (
    <div className="whitespace-pre-wrap break-words">
      {parts.map((part, i) => {
        if (part.startsWith("```") && part.endsWith("```")) {
          const lines = part.slice(3, -3).split("\n");
          const lang = lines[0]?.trim();
          const code = lang ? lines.slice(1).join("\n") : lines.join("\n");
          return (
            <pre
              key={i}
              className={`my-2 p-3 rounded-md text-xs font-mono overflow-x-auto ${
                isUser ? "bg-blue-700/50" : "bg-gray-900 border border-gray-700/50"
              }`}
            >
              {lang && (
                <div className="text-[10px] text-gray-500 mb-1 uppercase tracking-wider">
                  {lang}
                </div>
              )}
              <code>{code}</code>
            </pre>
          );
        }

        if (part.startsWith("`") && part.endsWith("`")) {
          return (
            <code
              key={i}
              className={`px-1 py-0.5 rounded text-xs font-mono ${
                isUser ? "bg-blue-700/50" : "bg-gray-700/50"
              }`}
            >
              {part.slice(1, -1)}
            </code>
          );
        }

        return (
          <span key={i}>
            {part.split(/(\*\*[^*]+\*\*)/g).map((seg, j) => {
              if (seg.startsWith("**") && seg.endsWith("**")) {
                return <strong key={j}>{seg.slice(2, -2)}</strong>;
              }
              return seg;
            })}
          </span>
        );
      })}
    </div>
  );
}
