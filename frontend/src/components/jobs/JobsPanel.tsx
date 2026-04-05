import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Cpu, ChevronRight } from "lucide-react";
import { listJobs } from "@/lib/api";
import { JobDetail } from "./JobDetail";
import type { Job } from "@/types/job";

const statusStyles: Record<string, string> = {
  queued: "bg-gray-600/20 text-gray-400",
  provisioning: "bg-yellow-600/20 text-yellow-400",
  running: "bg-blue-600/20 text-blue-400",
  completed: "bg-green-600/20 text-green-400",
  failed: "bg-red-600/20 text-red-400",
  cancelled: "bg-gray-600/20 text-gray-500",
};

export function JobsPanel() {
  const { data: jobs, isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
  });
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);

  if (selectedJob) {
    return (
      <div className="h-full overflow-y-auto">
        <JobDetail job={selectedJob} onBack={() => setSelectedJob(null)} />
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Cpu className="w-5 h-5 text-purple-400" />
        <h1 className="text-lg font-semibold text-gray-100">Jobs</h1>
      </div>

      {isLoading && (
        <div className="text-sm text-gray-500">Loading jobs...</div>
      )}

      {jobs && jobs.length === 0 && (
        <div className="text-sm text-gray-500">No jobs found.</div>
      )}

      {/* Jobs table */}
      {jobs && jobs.length > 0 && (
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
              {jobs.map((job) => (
                <tr
                  key={job.id}
                  onClick={() => setSelectedJob(job)}
                  className="hover:bg-gray-700/30 cursor-pointer transition-colors"
                >
                  <td className="px-4 py-3">
                    <p className="text-gray-200 font-mono text-xs">{job.id.slice(0, 8)}</p>
                    <p className="text-gray-500 text-xs truncate max-w-xs">
                      {job.repo_url ? job.repo_url.split("/").slice(-2).join("/") : "N/A"}
                    </p>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex px-2 py-0.5 text-xs rounded-full font-medium ${
                        statusStyles[job.status] || statusStyles.queued
                      }`}
                    >
                      {job.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-400 text-xs font-mono">
                    {job.instance_type}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-300 text-xs">
                    ${job.cost.toFixed(4)}
                  </td>
                  <td className="px-2 py-3">
                    <ChevronRight className="w-4 h-4 text-gray-600" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
