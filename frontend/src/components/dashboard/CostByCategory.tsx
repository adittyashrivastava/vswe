import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from "recharts";
import type { CostByCategory as CostByCategoryType } from "@/types/cost";

const COLORS = ["#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444", "#06b6d4"];

interface Props {
  categories: CostByCategoryType[];
}

export function CostByCategory({ categories }: Props) {
  const data = categories.map((c, i) => ({
    ...c,
    color: c.color || COLORS[i % COLORS.length],
  }));

  return (
    <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-300 mb-4">Cost by Category</h3>

      {data.length === 0 ? (
        <div className="flex items-center justify-center h-48 text-sm text-gray-500">
          No category data
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
              nameKey="category"
            >
              {data.map((entry, i) => (
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
