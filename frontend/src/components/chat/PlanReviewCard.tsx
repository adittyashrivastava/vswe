import { ClipboardList, Check, MessageSquare } from "lucide-react";

interface Props {
  plan: string;
  onApprove: () => void;
  onRequestChanges: () => void;
}

export function PlanReviewCard({ plan, onApprove, onRequestChanges }: Props) {
  return (
    <div className="flex gap-3">
      <div className="flex-shrink-0 w-7 h-7 rounded-md flex items-center justify-center bg-blue-600/20">
        <ClipboardList className="w-4 h-4 text-blue-400" />
      </div>
      <div className="flex-1 max-w-[85%] rounded-lg border border-blue-500/30 bg-blue-950/30 overflow-hidden">
        {/* Header */}
        <div className="px-4 py-2.5 border-b border-blue-500/20 bg-blue-950/40">
          <h3 className="text-sm font-medium text-blue-300">
            Proposed Plan of Action
          </h3>
          <p className="text-xs text-gray-400 mt-0.5">
            Review the plan below and approve to proceed, or request changes.
          </p>
        </div>

        {/* Plan content */}
        <div className="px-4 py-3">
          <div className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap break-words">
            {plan}
          </div>
        </div>

        {/* Actions */}
        <div className="px-4 py-3 border-t border-blue-500/20 bg-blue-950/20 flex gap-2">
          <button
            onClick={onApprove}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-green-600 hover:bg-green-500 text-white transition-colors"
          >
            <Check className="w-3.5 h-3.5" />
            Approve
          </button>
          <button
            onClick={onRequestChanges}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
          >
            <MessageSquare className="w-3.5 h-3.5" />
            Request Changes
          </button>
        </div>
      </div>
    </div>
  );
}
