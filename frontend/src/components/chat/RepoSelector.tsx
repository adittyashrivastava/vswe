import { useState } from "react";
import { GitBranch } from "lucide-react";

interface Props {
  sessionId: string;
}

export function RepoSelector({ sessionId: _sessionId }: Props) {
  const [repoUrl, setRepoUrl] = useState("");

  return (
    <div className="flex items-center gap-1.5 flex-1 max-w-sm">
      <GitBranch className="w-4 h-4 text-gray-500 flex-shrink-0" />
      <input
        type="text"
        value={repoUrl}
        onChange={(e) => setRepoUrl(e.target.value)}
        placeholder="github.com/org/repo"
        className="flex-1 bg-transparent border-none text-sm text-gray-300
                   placeholder-gray-600 focus:outline-none min-w-0"
      />
    </div>
  );
}
