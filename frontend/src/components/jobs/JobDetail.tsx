import { ArrowLeft, Check, Loader2, AlertCircle, Clock } from "lucide-react";
import type { Job, Checkpoint } from "@/types/job";

interface Props {
  job: Job;
  onBack: () => void;
}

const checkpointStatusIcon: Record<string, React.ReactNode> = {
  pending: <Clock className="w-4 h-4 text-gray-500" />,
  running: <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />,
  completed: <Check className="w-4 h-4 text-green-400" />,
  failed: <AlertCircle className="w-4 h-4 text-red-400" />,
};

export function JobDetail({ job, onBack }: Props) {
  return (
    <div className="p-6 space-y-6">
      {/* Back button + title */}
      <div className="flex items-center gap-3">
        <button
          onClick={onBack}
          className="p-1.5 rounded-md hover:bg-gray-700/50 text-gray-400 hover:text-gray-200 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div>
          <h2 className="text-lg font-semibold text-gray-100 font-mono">
            Job {job.id.slice(0, 8)}
          </h2>
          <p className="text-xs text-gray-500">{job.repo_url}</p>
        </div>
      </div>

      {/* Metadata cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: "Status", value: job.status },
          { label: "Instance", value: job.instance_type },
          { label: "Branch", value: job.branch },
          { label: "Cost", value: `$${job.cost.toFixed(4)}` },
        ].map((item) => (
          <div
            key={item.label}
            className="bg-gray-800 border border-gray-700/50 rounded-lg px-3 py-2.5"
          >
            <p className="text-[10px] uppercase tracking-wider text-gray-500">{item.label}</p>
            <p className="text-sm text-gray-200 mt-0.5 font-mono">{item.value}</p>
          </div>
        ))}
      </div>

      {/* Command */}
      {job.command && (
        <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4">
          <p className="text-xs text-gray-500 uppercase tracking-wider mb-2">Command</p>
          <pre className="text-sm text-gray-300 font-mono whitespace-pre-wrap bg-gray-900/50 rounded p-3">
            {job.command}
          </pre>
        </div>
      )}

      {/* Checkpoints */}
      <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4">
        <p className="text-xs text-gray-500 uppercase tracking-wider mb-3">Checkpoints</p>

        {job.checkpoints.length === 0 ? (
          <p className="text-sm text-gray-500">No checkpoints recorded.</p>
        ) : (
          <div className="space-y-0">
            {job.checkpoints.map((cp, i) => (
              <CheckpointRow
                key={cp.id}
                checkpoint={cp}
                isLast={i === job.checkpoints.length - 1}
              />
            ))}
          </div>
        )}
      </div>

      {/* Logs link */}
      {job.logs_url && (
        <a
          href={job.logs_url}
          target="_blank"
          rel="noreferrer"
          className="inline-block text-sm text-blue-400 hover:text-blue-300 underline underline-offset-2"
        >
          View full logs
        </a>
      )}
    </div>
  );
}

function CheckpointRow({
  checkpoint,
  isLast,
}: {
  checkpoint: Checkpoint;
  isLast: boolean;
}) {
  return (
    <div className="flex gap-3">
      {/* Timeline line + icon */}
      <div className="flex flex-col items-center">
        <div className="flex-shrink-0 mt-0.5">
          {checkpointStatusIcon[checkpoint.status] || checkpointStatusIcon.pending}
        </div>
        {!isLast && <div className="w-px flex-1 bg-gray-700 my-1" />}
      </div>

      {/* Content */}
      <div className={`pb-4 ${isLast ? "" : ""}`}>
        <p className="text-sm text-gray-200">{checkpoint.label}</p>
        <p className="text-xs text-gray-500">Step {checkpoint.step}</p>
        {checkpoint.output && (
          <pre className="mt-1 text-xs text-gray-400 font-mono bg-gray-900/50 rounded p-2 max-h-32 overflow-y-auto whitespace-pre-wrap">
            {checkpoint.output}
          </pre>
        )}
      </div>
    </div>
  );
}
