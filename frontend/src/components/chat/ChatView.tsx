import { useCallback, useRef } from "react";
import { useParams } from "react-router-dom";
import { useInfiniteQuery } from "@tanstack/react-query";
import { MessageSquare, Loader2 } from "lucide-react";
import { useWebSocket } from "@/hooks/useWebSocket";
import { getMessages } from "@/lib/api";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";
import { RepoSelector } from "./RepoSelector";
import { ModelSelector } from "./ModelSelector";
import { PlanReviewCard } from "./PlanReviewCard";
import { StepIndicator } from "./StepIndicator";
import { ToolCallBlock } from "./ToolCallBlock";
import type { Message } from "@/types/message";

export function ChatView() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const {
    sendMessage,
    messages: wsMessages,
    isConnected,
    activeToolCalls,
    completedToolCalls,
    statusText,
    streamingContent,
    isProcessing,
    pendingPlan,
  } = useWebSocket(sessionId);

  // Load existing messages with reverse-chronological pagination.
  // Each "page" returns the next batch of older messages (in chronological order).
  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["messages", sessionId],
    queryFn: ({ pageParam }) =>
      getMessages(sessionId!, { limit: 50, lastKey: pageParam ?? undefined }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.last_key,
    enabled: !!sessionId,
  });

  // Pages come in reverse-chronological order (newest page first).
  // Flatten: older pages first, then newer pages, then live WS messages.
  const loadedMessages: Message[] = data
    ? data.pages
        .slice()
        .reverse()
        .flatMap((page) => page.messages)
    : [];

  const allMessages: Message[] = [...loadedMessages, ...wsMessages];

  // Scroll-up handler: load older messages when the user scrolls to the top.
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    if (el.scrollTop < 80 && hasNextPage && !isFetchingNextPage) {
      // Remember scroll height before prepending so we can restore position.
      const prevHeight = el.scrollHeight;
      fetchNextPage().then(() => {
        requestAnimationFrame(() => {
          if (scrollContainerRef.current) {
            scrollContainerRef.current.scrollTop =
              scrollContainerRef.current.scrollHeight - prevHeight;
          }
        });
      });
    }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  if (!sessionId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500 gap-3">
        <MessageSquare className="w-12 h-12 text-gray-600" />
        <p className="text-lg font-medium text-gray-400">Virtual Software Engineer</p>
        <p className="text-sm text-gray-500 max-w-md text-center">
          Create a new session or select an existing one from the sidebar to start working with your AI software engineer.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Chat header */}
      <div className="flex items-center gap-3 px-4 py-2 border-b border-gray-700/50 bg-gray-800/50 flex-shrink-0">
        <RepoSelector sessionId={sessionId} />
        <ModelSelector sessionId={sessionId} />
        <div className="ml-auto flex items-center gap-2">
          <div
            className={`w-2 h-2 rounded-full ${
              isConnected ? "bg-green-400" : "bg-gray-500"
            }`}
          />
          <span className="text-xs text-gray-500">
            {isConnected ? "Connected" : "Disconnected"}
          </span>
        </div>
      </div>

      {/* Messages + live activity */}
      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-y-auto"
        onScroll={handleScroll}
      >
        <div className="max-w-3xl mx-auto px-4 py-4 space-y-3">
          {/* Loading-older indicator */}
          {isFetchingNextPage && (
            <div className="flex justify-center py-2">
              <Loader2 className="w-5 h-5 text-gray-500 animate-spin" />
            </div>
          )}

          {hasNextPage && !isFetchingNextPage && (
            <div className="flex justify-center py-1">
              <span className="text-xs text-gray-600">Scroll up to load older messages</span>
            </div>
          )}

          {/* Rendered messages (including tool calls attached to assistant messages) */}
          <MessageList messages={allMessages} suppressAutoScroll={isFetchingNextPage} />

          {/* Live area — shows while agent is processing */}
          {isProcessing && (
            <div className="space-y-2">
              {/* Streaming text content (reasoning) — shown first */}
              {streamingContent && (
                <div className="flex gap-3">
                  <div className="flex-shrink-0 w-7 h-7 rounded-md flex items-center justify-center bg-gray-700">
                    <span className="text-xs text-gray-300">AI</span>
                  </div>
                  <div className="max-w-[75%] rounded-lg px-3.5 py-2.5 text-sm leading-relaxed bg-gray-800 text-gray-200 border border-gray-700/50">
                    <div className="whitespace-pre-wrap break-words">{streamingContent}</div>
                    <span className="inline-block w-1.5 h-4 bg-blue-400 animate-pulse ml-0.5" />
                  </div>
                </div>
              )}

              {/* Completed tool calls from current response */}
              {completedToolCalls.length > 0 && (
                <div className="ml-10 space-y-1">
                  {completedToolCalls.map((tc) => (
                    <ToolCallBlock key={tc.id} toolCall={tc} />
                  ))}
                </div>
              )}

              {/* Currently running tool calls */}
              {activeToolCalls.length > 0 && (
                <div className="ml-10 space-y-1">
                  {activeToolCalls.map((tc) => (
                    <ToolCallBlock key={tc.id} toolCall={tc} />
                  ))}
                </div>
              )}

              {/* Status text */}
              {statusText && (
                <div className="ml-10">
                  <StepIndicator text={statusText} />
                </div>
              )}
            </div>
          )}

          {/* Plan review card — shown when agent submits a plan for approval */}
          {pendingPlan && (
            <PlanReviewCard
              plan={pendingPlan}
              onApprove={() => sendMessage("[PLAN_APPROVED]")}
              onRequestChanges={() => {
                const input = document.querySelector<HTMLTextAreaElement>(
                  "[data-chat-input]",
                );
                if (input) {
                  input.focus();
                  input.placeholder =
                    "Describe what you'd like changed in the plan...";
                }
              }}
            />
          )}
        </div>
      </div>

      {/* Input */}
      <div className="flex-shrink-0">
        <ChatInput onSend={sendMessage} disabled={!isConnected} />
      </div>
    </div>
  );
}
