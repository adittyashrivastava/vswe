import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { AVAILABLE_MODELS } from "@/lib/models";

interface Props {
  sessionId: string;
}

export function ModelSelector({ sessionId: _sessionId }: Props) {
  const [selectedModel, setSelectedModel] = useState(AVAILABLE_MODELS[0].id);
  const [open, setOpen] = useState(false);

  const current = AVAILABLE_MODELS.find((m) => m.id === selectedModel);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs
                   text-gray-400 hover:text-gray-200 hover:bg-gray-700/50 transition-colors"
      >
        <span className="text-gray-500">{current?.provider}</span>
        <span className="text-gray-300">{current?.name}</span>
        <ChevronDown className="w-3 h-3" />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute top-full left-0 mt-1 z-20 w-56 bg-gray-800 border border-gray-700 rounded-lg shadow-xl overflow-hidden">
            {AVAILABLE_MODELS.map((model) => (
              <button
                key={model.id}
                onClick={() => {
                  setSelectedModel(model.id);
                  setOpen(false);
                }}
                className={`flex items-center gap-2 w-full px-3 py-2 text-sm text-left transition-colors ${
                  model.id === selectedModel
                    ? "bg-blue-600/20 text-blue-300"
                    : "text-gray-300 hover:bg-gray-700/50"
                }`}
              >
                <span className="text-xs text-gray-500 w-16">{model.provider}</span>
                <span>{model.name}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
