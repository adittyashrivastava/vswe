import { useNavigate, useParams } from "react-router-dom";
import { Trash2 } from "lucide-react";
import { useListSessions, useDeleteSession } from "@/hooks/useSessions";

export function SessionList() {
  const { data: sessions, isLoading } = useListSessions();
  const deleteSession = useDeleteSession();
  const navigate = useNavigate();
  const { sessionId: activeId } = useParams();

  if (isLoading) {
    return (
      <div className="px-3 py-4 text-xs text-gray-500">Loading sessions...</div>
    );
  }

  if (!sessions || sessions.length === 0) {
    return (
      <div className="px-3 py-4 text-xs text-gray-500">
        No sessions yet. Create one to get started.
      </div>
    );
  }

  return (
    <div className="space-y-0.5">
      {sessions.map((session) => {
        const isActive = session.session_id === activeId;
        return (
          <div
            key={session.session_id}
            className={`group flex items-center gap-1 px-2 py-1.5 rounded-md cursor-pointer transition-colors ${
              isActive
                ? "bg-gray-700/70 text-gray-100"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-700/40"
            }`}
            onClick={() => navigate(`/chat/${session.session_id}`)}
          >
            <div className="flex-1 min-w-0">
              <p className="text-sm truncate">
                {session.title || "Untitled Session"}
              </p>
              <p className="text-xs text-gray-500 truncate">
                {session.repo_url
                  ? session.repo_url.split("/").slice(-1)[0]
                  : "No repo"}
              </p>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation();
                deleteSession.mutate(session.session_id);
              }}
              className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-gray-600/50 transition-all text-gray-500 hover:text-red-400"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
