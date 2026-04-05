import { useState } from "react";
import { ChevronDown, ChevronRight, Cpu, Wrench } from "lucide-react";
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip,
  Legend,
} from "recharts";
import type { SessionDetailedCost, TurnCost } from "@/types/cost";

const COLORS = ["#8b5cf6", "#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#06b6d4"];

interface Props {
  detail: SessionDetailedCost;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function cacheHitPercent(turn: TurnCost): number {
  const total = turn.input_tokens + turn.cache_read_tokens + turn.cache_creation_tokens;
  if (total === 0) return 0;
  return Math.round((turn.cache_read_tokens / total) * 100);
}

function modelShortName(model: string): string {
  if (model.includes("opus")) return "Opus";
  if (model.includes("sonnet")) return "Sonnet";
  if (model.includes("haiku")) return "Haiku";
  if (model.includes("gpt-4-turbo")) return "GPT-4T";
  if (model.includes("gpt-4")) return "GPT-4";
  return model;
}

function TurnRow({ turn }: { turn: TurnCost }) {
  const [expanded, setExpanded] = useState(false);
  const cacheHit = cacheHitPercent(turn);

  return (
    <>
      <tr
        className="border-b border-gray-700/50 hover:bg-gray-700/30 cursor-pointer transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <td className="px-3 py-2 text-xs text-gray-400">
          <span className="flex items-center gap-1">
            {expanded ? (
              <ChevronDown className="w-3 h-3" />
            ) : (
              <ChevronRight className="w-3 h-3" />
            )}
            <span className="font-mono">{turn.turn_id.slice(0, 12)}</span>
          </span>
        </td>
        <td className="px-3 py-2 text-xs text-gray-200 text-right font-mono">
          ${turn.total_cost.toFixed(4)}
        </td>
        <td className="px-3 py-2 text-xs text-gray-300 text-center">
          {turn.iterations}
        </td>
        <td className="px-3 py-2 text-xs text-gray-300 text-right font-mono">
          {formatTokens(turn.input_tokens)}
        </td>
        <td className="px-3 py-2 text-xs text-gray-300 text-right font-mono">
          {formatTokens(turn.output_tokens)}
        </td>
        <td className="px-3 py-2 text-xs text-center">
          <span
            className={
              cacheHit > 60
                ? "text-green-400"
                : cacheHit > 30
                  ? "text-yellow-400"
                  : "text-gray-400"
            }
          >
            {cacheHit}%
          </span>
        </td>
        <td className="px-3 py-2 text-xs text-gray-300">
          <div className="flex flex-wrap gap-1">
            {Object.keys(turn.models_used).map((m) => (
              <span
                key={m}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-700 text-gray-300"
              >
                <Cpu className="w-3 h-3 text-purple-400" />
                {modelShortName(m)}
              </span>
            ))}
          </div>
        </td>
        <td className="px-3 py-2 text-xs text-gray-300">
          <div className="flex flex-wrap gap-1">
            {turn.tools_used.map((t) => (
              <span
                key={t}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-700 text-gray-300"
              >
                <Wrench className="w-3 h-3 text-blue-400" />
                {t}
              </span>
            ))}
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-gray-800/50">
          <td colSpan={8} className="px-6 py-3">
            <div className="text-xs text-gray-400 space-y-2">
              <p className="font-medium text-gray-300">Model breakdown:</p>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {Object.entries(turn.models_used).map(([model, cost]) => (
                  <div
                    key={model}
                    className="flex items-center justify-between bg-gray-700/50 rounded px-3 py-1.5"
                  >
                    <span className="text-gray-300">{modelShortName(model)}</span>
                    <span className="font-mono text-gray-200">${cost.toFixed(4)}</span>
                  </div>
                ))}
              </div>
              <div className="flex gap-4 text-gray-500 mt-1">
                <span>Cache read: {formatTokens(turn.cache_read_tokens)} tokens</span>
                <span>Cache creation: {formatTokens(turn.cache_creation_tokens)} tokens</span>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function SessionCostView({ detail }: Props) {
  const modelData = detail.by_model.map((m, i) => ({
    name: modelShortName(m.category),
    value: m.amount,
    color: m.color || COLORS[i % COLORS.length],
  }));

  return (
    <div className="space-y-6">
      {/* Header stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4">
          <p className="text-xs text-gray-500">Session Total</p>
          <p className="text-xl font-semibold text-gray-100">
            ${detail.total_cost.toFixed(4)}
          </p>
          <p className="text-xs text-gray-500 mt-1 font-mono">
            {detail.session_id.slice(0, 16)}...
          </p>
        </div>

        <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4">
          <p className="text-xs text-gray-500">Turns</p>
          <p className="text-xl font-semibold text-gray-100">{detail.turns.length}</p>
        </div>

        <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4">
          <p className="text-xs text-gray-500">Cache Efficiency</p>
          <p
            className={`text-xl font-semibold ${
              detail.cache_efficiency > 60
                ? "text-green-400"
                : detail.cache_efficiency > 30
                  ? "text-yellow-400"
                  : "text-gray-100"
            }`}
          >
            {detail.cache_efficiency.toFixed(1)}%
          </p>
          <div className="mt-2 h-1.5 bg-gray-700 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                detail.cache_efficiency > 60
                  ? "bg-green-500"
                  : detail.cache_efficiency > 30
                    ? "bg-yellow-500"
                    : "bg-gray-500"
              }`}
              style={{ width: `${Math.min(detail.cache_efficiency, 100)}%` }}
            />
          </div>
        </div>
      </div>

      {/* Model pie chart */}
      {modelData.length > 0 && (
        <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-300 mb-4">Cost by Model</h3>
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie
                data={modelData}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={80}
                paddingAngle={3}
                dataKey="value"
                nameKey="name"
              >
                {modelData.map((entry, i) => (
                  <Cell key={i} fill={entry.color} stroke="transparent" />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  backgroundColor: "#1f2937",
                  border: "1px solid #374151",
                  borderRadius: "8px",
                  fontSize: "12px",
                  color: "#e5e7eb",
                }}
                formatter={(value: number) => [`$${value.toFixed(4)}`, "Cost"]}
              />
              <Legend
                formatter={(value: string) => (
                  <span className="text-xs text-gray-400">{value}</span>
                )}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Per-turn table */}
      <div className="bg-gray-800 border border-gray-700/50 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-700/50">
          <h3 className="text-sm font-medium text-gray-300">Per-Turn Breakdown</h3>
        </div>
        {detail.turns.length === 0 ? (
          <div className="flex items-center justify-center h-24 text-sm text-gray-500">
            No turn data available
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700/50 text-xs text-gray-500 uppercase">
                  <th className="px-3 py-2 text-left">Turn</th>
                  <th className="px-3 py-2 text-right">Cost</th>
                  <th className="px-3 py-2 text-center">Iters</th>
                  <th className="px-3 py-2 text-right">Input</th>
                  <th className="px-3 py-2 text-right">Output</th>
                  <th className="px-3 py-2 text-center">Cache %</th>
                  <th className="px-3 py-2 text-left">Models</th>
                  <th className="px-3 py-2 text-left">Tools</th>
                </tr>
              </thead>
              <tbody>
                {detail.turns.map((turn) => (
                  <TurnRow key={turn.turn_id} turn={turn} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
