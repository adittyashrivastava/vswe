import { useState } from "react";
import { DollarSign, ChevronDown, ChevronRight } from "lucide-react";
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip,
  Legend,
} from "recharts";
import { useCostSummary, useSessionDetailedCosts } from "@/hooks/useCosts";
import { CostOverview } from "./CostOverview";
import { CostByCategory } from "./CostByCategory";
import { CostTimeline } from "./CostTimeline";
import { SessionCostView } from "./SessionCostView";

function CostByModel({ models }: { models: { category: string; amount: number; percentage: number; color: string }[] }) {

  const COLORS = ["#8b5cf6", "#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#06b6d4"];

  function modelShortName(model: string): string {
    if (model.includes("opus")) return "Opus";
    if (model.includes("sonnet")) return "Sonnet";
    if (model.includes("haiku")) return "Haiku";
    if (model.includes("gpt-4-turbo")) return "GPT-4T";
    if (model.includes("gpt-4")) return "GPT-4";
    return model;
  }

  const data = models.map((m, i) => ({
    name: modelShortName(m.category),
    amount: m.amount,
    color: m.color || COLORS[i % COLORS.length],
  }));

  return (
    <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-300 mb-4">Cost by Model</h3>

      {data.length === 0 ? (
        <div className="flex items-center justify-center h-48 text-sm text-gray-500">
          No model data
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <PieChart>
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius={55}
              outerRadius={85}
              paddingAngle={3}
              dataKey="amount"
              nameKey="name"
            >
              {data.map((entry: { color: string }, i: number) => (
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
      )}
    </div>
  );
}

interface SessionRowData {
  session_id: string;
  total_cost: number;
  turn_count: number;
  category: string;
}

function SessionsSection({ entries, totalCost }: { entries: { session_id?: string | null; amount_usd?: number; category?: string; details?: Record<string, unknown> | null }[]; totalCost: number }) {
  const [expandedSession, setExpandedSession] = useState<string | null>(null);

  // Aggregate entries by session_id
  const sessionsMap = new Map<string, SessionRowData>();
  let attributedCost = 0;

  for (const entry of entries) {
    const sid = entry.session_id;
    const cost = entry.amount_usd ?? 0;
    if (!sid) continue;
    attributedCost += cost;
    const existing = sessionsMap.get(sid);
    if (existing) {
      existing.total_cost += cost;
      existing.turn_count += 1;
    } else {
      sessionsMap.set(sid, {
        session_id: sid,
        total_cost: cost,
        turn_count: 1,
        category: entry.category ?? "other",
      });
    }
  }

  const sessions = Array.from(sessionsMap.values()).sort(
    (a, b) => b.total_cost - a.total_cost,
  );

  // Calculate unattributed cost (entries with no session_id)
  const unattributedCost = totalCost - attributedCost;

  if (sessions.length === 0) {
    return null;
  }

  return (
    <div className="bg-gray-800 border border-gray-700/50 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-700/50">
        <h3 className="text-sm font-medium text-gray-300">Sessions</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-700/50 text-xs text-gray-500 uppercase">
              <th className="px-4 py-2 text-left">Session ID</th>
              <th className="px-4 py-2 text-right">Total Cost</th>
              <th className="px-4 py-2 text-center">Entries</th>
              <th className="px-4 py-2 text-left">Category</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((session) => (
              <SessionTableRow
                key={session.session_id}
                session={session}
                isExpanded={expandedSession === session.session_id}
                onToggle={() =>
                  setExpandedSession(
                    expandedSession === session.session_id
                      ? null
                      : session.session_id,
                  )
                }
              />
            ))}
            {unattributedCost > 0.0001 && (
              <tr className="border-b border-gray-700/50 text-gray-500">
                <td className="px-4 py-2 text-xs italic">
                  Unattributed (no session ID)
                </td>
                <td className="px-4 py-2 text-xs text-right font-mono">
                  ${unattributedCost.toFixed(4)}
                </td>
                <td className="px-4 py-2 text-xs text-center">—</td>
                <td className="px-4 py-2 text-xs">mixed</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SessionTableRow({
  session,
  isExpanded,
  onToggle,
}: {
  session: SessionRowData;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const { data: detail, isLoading } = useSessionDetailedCosts(
    isExpanded ? session.session_id : undefined,
  );

  return (
    <>
      <tr
        className="border-b border-gray-700/50 hover:bg-gray-700/30 cursor-pointer transition-colors"
        onClick={onToggle}
      >
        <td className="px-4 py-2 text-xs text-gray-300">
          <span className="flex items-center gap-1">
            {isExpanded ? (
              <ChevronDown className="w-3 h-3 text-gray-500" />
            ) : (
              <ChevronRight className="w-3 h-3 text-gray-500" />
            )}
            <span className="font-mono">{session.session_id.slice(0, 16)}...</span>
          </span>
        </td>
        <td className="px-4 py-2 text-xs text-gray-200 text-right font-mono">
          ${session.total_cost.toFixed(4)}
        </td>
        <td className="px-4 py-2 text-xs text-gray-300 text-center">
          {session.turn_count}
        </td>
        <td className="px-4 py-2 text-xs text-gray-400">
          {session.category}
        </td>
      </tr>
      {isExpanded && (
        <tr className="bg-gray-900/30">
          <td colSpan={4} className="p-4">
            {isLoading ? (
              <div className="text-sm text-gray-500 py-4 text-center">
                Loading session details...
              </div>
            ) : detail ? (
              <SessionCostView detail={detail} />
            ) : (
              <div className="text-sm text-gray-500 py-4 text-center">
                No detailed data available for this session.
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export function CostDashboard() {
  const [dateRange, setDateRange] = useState<{ from: string; to: string }>({
    from: getDefaultFrom(),
    to: new Date().toISOString().split("T")[0],
  });

  const { data: summary, isLoading, error } = useCostSummary(dateRange.from, dateRange.to);

  return (
    <div className="h-full overflow-y-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <DollarSign className="w-5 h-5 text-green-400" />
          <h1 className="text-lg font-semibold text-gray-100">Cost Dashboard</h1>
        </div>

        <div className="flex items-center gap-2 text-sm">
          <input
            type="date"
            value={dateRange.from}
            onChange={(e) => setDateRange((r) => ({ ...r, from: e.target.value }))}
            className="bg-gray-800 border border-gray-700 rounded-md px-2 py-1 text-gray-300 text-xs
                       focus:outline-none focus:ring-1 focus:ring-blue-500/50"
          />
          <span className="text-gray-500">to</span>
          <input
            type="date"
            value={dateRange.to}
            onChange={(e) => setDateRange((r) => ({ ...r, to: e.target.value }))}
            className="bg-gray-800 border border-gray-700 rounded-md px-2 py-1 text-gray-300 text-xs
                       focus:outline-none focus:ring-1 focus:ring-blue-500/50"
          />
        </div>
      </div>

      {isLoading && (
        <div className="text-sm text-gray-500">Loading cost data...</div>
      )}

      {error && (
        <div className="text-sm text-red-400">
          Failed to load cost data. Make sure the API is running.
        </div>
      )}

      {summary && (
        <>
          <CostOverview summary={summary} />
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <CostByCategory categories={summary.by_category} />
            <CostByModel models={summary.by_model} />
          </div>
          <div className="grid grid-cols-1 gap-6">
            <CostTimeline dailyCosts={summary.daily_costs} />
          </div>
          <SessionsSection entries={summary.entries} totalCost={summary.total_cost} />
        </>
      )}

      {!summary && !isLoading && !error && (
        <div className="text-sm text-gray-500">No cost data available for the selected period.</div>
      )}
    </div>
  );
}

function getDefaultFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 30);
  return d.toISOString().split("T")[0];
}
