import { DollarSign, TrendingUp, PiggyBank } from "lucide-react";
import type { CostSummary } from "@/types/cost";

interface Props {
  summary: CostSummary;
}

export function CostOverview({ summary }: Props) {
  const percentColor =
    summary.percentage_used > 80
      ? "text-red-400"
      : summary.percentage_used > 50
        ? "text-yellow-400"
        : "text-green-400";

  const cards = [
    {
      label: "Total Spend",
      value: `$${summary.total_cost.toFixed(2)}`,
      icon: DollarSign,
      iconColor: "text-blue-400",
      bgColor: "bg-blue-500/10",
    },
    {
      label: "Budget Remaining",
      value: `$${summary.budget_remaining.toFixed(2)}`,
      sublabel: `of $${summary.budget_limit.toFixed(2)}`,
      icon: PiggyBank,
      iconColor: "text-green-400",
      bgColor: "bg-green-500/10",
    },
    {
      label: "Budget Used",
      value: `${summary.percentage_used.toFixed(1)}%`,
      icon: TrendingUp,
      iconColor: percentColor,
      bgColor:
        summary.percentage_used > 80
          ? "bg-red-500/10"
          : summary.percentage_used > 50
            ? "bg-yellow-500/10"
            : "bg-green-500/10",
    },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
      {cards.map((card) => (
        <div
          key={card.label}
          className="bg-gray-800 border border-gray-700/50 rounded-lg p-4"
        >
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${card.bgColor}`}>
              <card.icon className={`w-5 h-5 ${card.iconColor}`} />
            </div>
            <div>
              <p className="text-xs text-gray-500">{card.label}</p>
              <p className="text-xl font-semibold text-gray-100">{card.value}</p>
              {card.sublabel && (
                <p className="text-xs text-gray-500">{card.sublabel}</p>
              )}
            </div>
          </div>

          {/* Budget bar for Budget Used card */}
          {card.label === "Budget Used" && (
            <div className="mt-3 h-1.5 bg-gray-700 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  summary.percentage_used > 80
                    ? "bg-red-500"
                    : summary.percentage_used > 50
                      ? "bg-yellow-500"
                      : "bg-green-500"
                }`}
                style={{ width: `${Math.min(summary.percentage_used, 100)}%` }}
              />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
