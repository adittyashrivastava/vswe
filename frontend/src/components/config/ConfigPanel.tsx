import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Settings, Save, Loader2 } from "lucide-react";
import { getConfig, updateConfig } from "@/lib/api";
import type { ConfigData } from "@/lib/api";
import { AVAILABLE_MODELS } from "@/lib/models";

export function ConfigPanel() {
  const queryClient = useQueryClient();
  const { data: config, isLoading } = useQuery({
    queryKey: ["config"],
    queryFn: getConfig,
  });

  const mutation = useMutation({
    mutationFn: updateConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["config"] });
    },
  });

  const [form, setForm] = useState<Partial<ConfigData>>({});

  useEffect(() => {
    if (config) {
      setForm(config);
    }
  }, [config]);

  const handleSave = () => {
    mutation.mutate(form);
  };

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-2xl space-y-6">
        {/* Header */}
        <div className="flex items-center gap-2">
          <Settings className="w-5 h-5 text-gray-400" />
          <h1 className="text-lg font-semibold text-gray-100">Configuration</h1>
        </div>

        {isLoading && (
          <div className="text-sm text-gray-500">Loading configuration...</div>
        )}

        {!isLoading && (
          <div className="space-y-6">
            {/* Enabled toggle */}
            <div className="flex items-center justify-between bg-gray-800 border border-gray-700/50 rounded-lg p-4">
              <div>
                <p className="text-sm font-medium text-gray-200">Enable VSWE</p>
                <p className="text-xs text-gray-500 mt-0.5">
                  Allow the virtual software engineer to process requests
                </p>
              </div>
              <button
                onClick={() => setForm((f) => ({ ...f, enabled: !f.enabled }))}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  form.enabled ? "bg-blue-600" : "bg-gray-600"
                }`}
              >
                <span
                  className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                    form.enabled ? "translate-x-5" : "translate-x-0"
                  }`}
                />
              </button>
            </div>

            {/* Default model */}
            <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4 space-y-2">
              <label className="text-sm font-medium text-gray-200">Default Model</label>
              <select
                value={form.default_model || ""}
                onChange={(e) => setForm((f) => ({ ...f, default_model: e.target.value }))}
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

            {/* Allowed repos */}
            <div className="bg-gray-800 border border-gray-700/50 rounded-lg p-4 space-y-2">
              <label className="text-sm font-medium text-gray-200">Allowed Repositories</label>
              <p className="text-xs text-gray-500">
                One repository URL per line. Leave empty to allow all.
              </p>
              <textarea
                value={(form.allowed_repos || []).join("\n")}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    allowed_repos: e.target.value
                      .split("\n")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  }))
                }
                rows={4}
                className="w-full bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-300
                           font-mono resize-none
                           focus:outline-none focus:ring-1 focus:ring-blue-500/50"
                placeholder="https://github.com/org/repo"
              />
            </div>

            {/* Save button */}
            <button
              onClick={handleSave}
              disabled={mutation.isPending}
              className="flex items-center gap-2 px-4 py-2 rounded-md bg-blue-600 hover:bg-blue-500
                         text-white text-sm font-medium transition-colors
                         disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {mutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              Save Configuration
            </button>

            {mutation.isSuccess && (
              <p className="text-xs text-green-400">Configuration saved successfully.</p>
            )}
            {mutation.isError && (
              <p className="text-xs text-red-400">Failed to save configuration.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
