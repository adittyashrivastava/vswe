import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Settings,
  Save,
  Loader2,
  GitBranch,
  Lock,
  Globe,
  Check,
  X,
} from "lucide-react";
import {
  getConfig,
  updateConfig,
  getReposStatus,
  enableRepo,
  disableRepo,
} from "@/lib/api";
import type { RepoConfigStatus } from "@/lib/api";
import { AVAILABLE_MODELS } from "@/lib/models";

interface ConfigData {
  enabled?: boolean;
  default_model?: string;
  max_cost_per_session?: number;
  allowed_repos?: string[];
}

export function ConfigPanel() {
  const queryClient = useQueryClient();

  // Global config
  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ["config", "global"],
    queryFn: () => getConfig("global") as Promise<ConfigData>,
  });

  const configMutation = useMutation({
    mutationFn: (data: Partial<ConfigData>) => updateConfig("global", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["config", "global"] });
    },
  });

  // Repo statuses
  const {
    data: repos,
    isLoading: reposLoading,
    isError: reposError,
  } = useQuery({
    queryKey: ["config", "repos-status"],
    queryFn: getReposStatus,
  });

  const [form, setForm] = useState<Partial<ConfigData>>({});

  useEffect(() => {
    if (config) {
      setForm(config);
    }
  }, [config]);

  const handleSave = () => {
    configMutation.mutate(form);
  };

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-2xl space-y-6">
        {/* Header */}
        <div className="flex items-center gap-2">
          <Settings className="w-5 h-5 text-gray-400" />
          <h1 className="text-lg font-semibold text-gray-100">Configuration</h1>
        </div>

        {/* Repositories Section */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <GitBranch className="w-4 h-4 text-gray-400" />
            <h2 className="text-sm font-semibold text-gray-200">
              Repository Agent
            </h2>
          </div>
          <p className="text-xs text-gray-500">
            Enable or disable the agent for repositories where the GitHub App is
            installed. When enabled, the agent will respond to new issues
            automatically.
          </p>

          {reposLoading && (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-4">
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading repositories...
            </div>
          )}

          {reposError && (
            <div className="text-sm text-red-400 bg-red-950/30 border border-red-500/20 rounded-lg px-4 py-3">
              Failed to load repositories. Make sure you have a GitHub App
              installed on at least one repository.
            </div>
          )}

          {repos && repos.length === 0 && !reposLoading && (
            <div className="text-sm text-gray-500 bg-gray-800 border border-gray-700/50 rounded-lg px-4 py-3">
              No repositories found. Install the GitHub App on a repository
              first.
            </div>
          )}

          {repos && repos.length > 0 && (
            <div className="bg-gray-800 border border-gray-700/50 rounded-lg overflow-hidden divide-y divide-gray-700/50">
              {repos.map((repo) => (
                <RepoRow key={repo.full_name} repo={repo} />
              ))}
            </div>
          )}
        </div>

        {/* Global Settings Section */}
        <div className="space-y-3 pt-2">
          <h2 className="text-sm font-semibold text-gray-200">
            Global Settings
          </h2>

          {configLoading && (
            <div className="text-sm text-gray-500">
              Loading configuration...
            </div>
          )}

          {!configLoading && (
            <div className="space-y-4">
              {/* Default model */}
              <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4 space-y-2">
                <label className="text-sm font-medium text-gray-200">
                  Default Model
                </label>
                <select
                  value={form.default_model || ""}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, default_model: e.target.value }))
                  }
                  className="w-full bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-300
                             focus:outline-none focus:ring-1 focus:ring-blue-500/50"
                >
                  {AVAILABLE_MODELS.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.provider} - {m.name}
                    </option>
                  ))}
                </select>
              </div>

              {/* Max cost per session */}
              <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4 space-y-2">
                <label className="text-sm font-medium text-gray-200">
                  Max Cost per Session ($)
                </label>
                <p className="text-xs text-gray-500">
                  Maximum spend allowed per individual session
                </p>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={form.max_cost_per_session ?? ""}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      max_cost_per_session: parseFloat(e.target.value) || 0,
                    }))
                  }
                  className="w-full bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-300
                             focus:outline-none focus:ring-1 focus:ring-blue-500/50"
                />
              </div>

              {/* Save button */}
              <button
                onClick={handleSave}
                disabled={configMutation.isPending}
                className="flex items-center gap-2 px-4 py-2 rounded-md bg-blue-600 hover:bg-blue-500
                           text-white text-sm font-medium transition-colors
                           disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {configMutation.isPending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Save className="w-4 h-4" />
                )}
                Save Settings
              </button>

              {configMutation.isSuccess && (
                <p className="text-xs text-green-400">
                  Settings saved successfully.
                </p>
              )}
              {configMutation.isError && (
                <p className="text-xs text-red-400">
                  Failed to save settings.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RepoRow — individual repository with enable/disable toggle
// ---------------------------------------------------------------------------

function RepoRow({ repo }: { repo: RepoConfigStatus }) {
  const queryClient = useQueryClient();

  const toggleMutation = useMutation({
    mutationFn: () =>
      repo.enabled ? disableRepo(repo.full_name) : enableRepo(repo.full_name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["config", "repos-status"] });
    },
  });

  return (
    <div className="flex items-center justify-between px-4 py-3">
      <div className="flex items-center gap-3 min-w-0">
        {repo.private ? (
          <Lock className="w-4 h-4 text-yellow-500 flex-shrink-0" />
        ) : (
          <Globe className="w-4 h-4 text-gray-500 flex-shrink-0" />
        )}
        <div className="min-w-0">
          <p className="text-sm text-gray-200 truncate">{repo.full_name}</p>
          <p className="text-xs text-gray-500">
            {repo.private ? "Private" : "Public"}
          </p>
        </div>
      </div>

      <button
        onClick={() => toggleMutation.mutate()}
        disabled={toggleMutation.isPending}
        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors
          disabled:opacity-50 disabled:cursor-not-allowed
          ${
            repo.enabled
              ? "bg-green-600/20 text-green-400 border border-green-500/30 hover:bg-red-600/20 hover:text-red-400 hover:border-red-500/30"
              : "bg-gray-700 text-gray-300 hover:bg-blue-600/20 hover:text-blue-400 hover:border-blue-500/30 border border-gray-600"
          }`}
      >
        {toggleMutation.isPending ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : repo.enabled ? (
          <Check className="w-3.5 h-3.5" />
        ) : (
          <X className="w-3.5 h-3.5" />
        )}
        {repo.enabled ? "Enabled" : "Enable"}
      </button>
    </div>
  );
}
