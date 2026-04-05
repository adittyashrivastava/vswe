import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { DailyCost } from "@/types/cost";

interface Props {
  dailyCosts: DailyCost[];
}

export function CostTimeline({ dailyCosts }: Props) {
  const data = dailyCosts.map((d) => ({
    date: d.date.slice(5), // MM-DD
    amount: d.amount,
  }));

  return (
    <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-300 mb-4">Daily Costs</h3>

      {data.length === 0 ? (
        <div className="flex items-center justify-center h-48 text-sm text-gray-500">
          No timeline data
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: "#6b7280" }}
              axisLine={{ stroke: "#374151" }}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "#6b7280" }}
              axisLine={{ stroke: "#374151" }}
              tickLine={false}
              tickFormatter={(v) => `$${v}`}
            />
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
            <Line
              type="monotone"
              dataKey="amount"
              stroke="#3b82f6"
              strokeWidth={2}
              dot={{ r: 3, fill: "#3b82f6" }}
              activeDot={{ r: 5 }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
