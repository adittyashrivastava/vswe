import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Cpu, ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { listJobs, getJobLogs } from "@/lib/api";
import type { Job } from "@/types/job";

const statusStyles: Record<string, string> = {
  profiling: "bg-gray-600/20 text-gray-400",
  queued: "bg-yellow-600/20 text-yellow-400",
  running: "bg-blue-600/20 text-blue-400",
  completed: "bg-green-600/20 text-green-400",
  failed: "bg-red-600/20 text-red-400",
};

function JobRow({ job }: { job: Job }) {
  const [expanded, setExpanded] = useState(false);
  const { data: logsData, isLoading: logsLoading } = useQuery({
    queryKey: ["job-logs", job.job_id],
    queryFn: () => getJobLogs(job.job_id),
    enabled: expanded,
  });

  return (
    <>
      <tr
        onClick={() => setExpanded(!expanded)}
        className="hover:bg-gray-700/30 cursor-pointer transition-colors"
      >
        <td className="px-4 py-3">
          <p className="text-gray-200 font-mono text-xs">{job.job_id}</p>
          <p className="text-gray-500 text-xs truncate max-w-xs">
            {job.script_path || "N/A"}
          </p>
        </td>
        <td className="px-4 py-3">
          <span
            className={`inline-flex px-2 py-0.5 text-xs rounded-full font-medium ${
              statusStyles[job.status] || statusStyles.profiling
            }`}
          >
            {job.status}
          </span>
        </td>
        <td className="px-4 py-3 text-gray-400 text-xs font-mono">
          {job.instance_type || "—"}
        </td>
        <td className="px-4 py-3 text-right text-gray-300 text-xs">
          ${job.total_cost_usd.toFixed(4)}
        </td>
        <td className="px-2 py-3">
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-gray-400" />
          ) : (
            <ChevronRight className="w-4 h-4 text-gray-600" />
          )}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={5} className="px-4 py-3 bg-gray-900/50">
            {logsLoading ? (
              <div className="flex items-center gap-2 text-xs text-gray-500 py-2">
                <Loader2 className="w-3 h-3 animate-spin" />
                Loading logs...
              </div>
            ) : (
              <pre className="text-xs text-gray-400 font-mono whitespace-pre-wrap max-h-64 overflow-y-auto leading-relaxed">
                {logsData?.logs.join("\n") || "No logs available."}
              </pre>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export function JobsPanel() {
  const { data, isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
    refetchInterval: 10000,
  });

  const jobs = data?.jobs ?? [];

  return (
    <div className="h-full overflow-y-auto p-6 space-y-4">
      <div className="flex items-center gap-2">
        <Cpu className="w-5 h-5 text-purple-400" />
        <h1 className="text-lg font-semibold text-gray-100">Jobs</h1>
      </div>

      {isLoading && (
        <div className="text-sm text-gray-500">Loading jobs...</div>
      )}

      {!isLoading && jobs.length === 0 && (
        <div className="text-sm text-gray-500">No jobs found.</div>
      )}

      {jobs.length > 0 && (
        <div className="bg-gray-800 border border-gray-700/50 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-700/50">
                <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Job
                </th>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Instance
                </th>
                <th className="text-right px-4 py-2.5 text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Cost
                </th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700/30">
              {jobs.map((job: Job) => (
                <JobRow key={job.job_id} job={job} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
