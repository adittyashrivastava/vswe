import { useState } from "react";
import {
  ChevronRight,
  ChevronDown,
  FileText,
  Search,
  FolderOpen,
  Terminal,
  GitBranch,
  GitPullRequest,
  Edit3,
  FilePlus,
  Download,
  Check,
  Loader2,
  AlertCircle,
} from "lucide-react";
import type { ToolCall } from "@/types/message";

interface Props {
  toolCall: ToolCall;
}

const TOOL_ICONS: Record<string, typeof FileText> = {
  read_file: FileText,
  edit_file: Edit3,
  write_file: FilePlus,
  search_code: Search,
  list_files: FolderOpen,
  run_command: Terminal,
  clone_repo: Download,
  create_branch: GitBranch,
  commit_and_push: GitBranch,
  create_pull_request: GitPullRequest,
};

function truncateOutput(output: string, maxLen = 120): string {
  if (output.length <= maxLen) return output;
  return output.slice(0, maxLen) + "…";
}

function formatToolSummary(toolCall: ToolCall): string {
  const { name, input } = toolCall;
  switch (name) {
    case "read_file":
      return `Read ${input.path || "file"}`;
    case "edit_file":
      return `Edit ${input.path || "file"}`;
    case "write_file":
      return `Write ${input.path || "file"}`;
    case "search_code":
      return `Search: ${input.pattern || input.query || ""}`;
    case "list_files":
      return `List ${input.path || "."}${input.recursive ? " (recursive)" : ""}`;
    case "run_command":
      return `$ ${input.command || ""}`;
    case "clone_repo":
      return `Clone ${input.repo_url || "repo"}`;
    case "create_branch":
      return `Branch: ${input.branch_name || ""}`;
    case "commit_and_push":
      return `Commit: ${input.message || ""}`;
    case "create_pull_request":
      return `PR: ${input.title || ""}`;
    default:
      return name;
  }
}

export function ToolCallBlock({ toolCall }: Props) {
  const [expanded, setExpanded] = useState(false);

  const Icon = TOOL_ICONS[toolCall.name] || Terminal;

  const isRunning = toolCall.status === "running" || toolCall.status === "pending";
  const isError = toolCall.status === "error";
  const isDone = toolCall.status === "completed";

  const statusIndicator = isRunning ? (
    <Loader2 className="w-3 h-3 text-blue-400 animate-spin" />
  ) : isError ? (
    <AlertCircle className="w-3 h-3 text-red-400" />
  ) : isDone ? (
    <Check className="w-3 h-3 text-green-400" />
  ) : null;

  const summary = formatToolSummary(toolCall);
  const truncatedOutput = toolCall.output ? truncateOutput(toolCall.output) : null;

  return (
    <div
      className={`rounded-md border text-xs font-mono ${
        isError
          ? "border-red-800/50 bg-red-950/20"
          : "border-gray-700/50 bg-gray-800/50"
      }`}
    >
      {/* Compact header — always visible */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-2.5 py-1.5 hover:bg-gray-700/20 transition-colors text-left"
      >
        {expanded ? (
          <ChevronDown className="w-3 h-3 text-gray-500 flex-shrink-0" />
        ) : (
          <ChevronRight className="w-3 h-3 text-gray-500 flex-shrink-0" />
        )}
        <Icon className="w-3 h-3 text-gray-500 flex-shrink-0" />
        <span className="text-gray-300 truncate flex-1">{summary}</span>
        <span className="flex items-center gap-1.5 flex-shrink-0">
          {toolCall.duration_ms !== undefined && (
            <span className="text-gray-600">{toolCall.duration_ms}ms</span>
          )}
          {statusIndicator}
        </span>
      </button>

      {/* Truncated result preview — shown when collapsed and has output */}
      {!expanded && truncatedOutput && (
        <div className="px-2.5 pb-1.5 -mt-0.5">
          <span className="text-gray-500">{truncatedOutput}</span>
        </div>
      )}

      {/* Expanded details */}
      {expanded && (
        <div className="border-t border-gray-700/30">
          {/* Input */}
          <div className="px-2.5 py-2">
            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">
              Input
            </div>
            <pre className="text-gray-400 whitespace-pre-wrap break-all bg-gray-900/50 rounded p-2 max-h-32 overflow-y-auto">
              {JSON.stringify(toolCall.input, null, 2)}
            </pre>
          </div>

          {/* Output */}
          {toolCall.output && (
            <div className="px-2.5 py-2 border-t border-gray-700/20">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">
                Output
              </div>
              <pre className="text-gray-400 whitespace-pre-wrap break-all bg-gray-900/50 rounded p-2 max-h-60 overflow-y-auto">
                {toolCall.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
