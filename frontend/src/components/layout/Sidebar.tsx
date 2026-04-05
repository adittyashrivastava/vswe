import { NavLink } from "react-router-dom";
import {
  MessageSquare,
  BarChart3,
  Settings,
  Cpu,
  Plus,
  LogOut,
} from "lucide-react";
import { SessionList } from "./SessionList";
import { useCreateSession } from "@/hooks/useSessions";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";

const navItems = [
  { to: "/chat", icon: MessageSquare, label: "Chat" },
  { to: "/jobs", icon: Cpu, label: "Jobs" },
  { to: "/costs", icon: BarChart3, label: "Costs" },
  { to: "/config", icon: Settings, label: "Config" },
];

export function Sidebar() {
  const createSession = useCreateSession();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  const handleNewSession = () => {
    createSession.mutate(
      { repo_url: "", title: "New Session" },
      {
        onSuccess: (session) => {
          navigate(`/chat/${session.session_id}`);
        },
      },
    );
  };

  return (
    <div className="flex flex-col h-full bg-gray-800 border-r border-gray-700/50">
      {/* Logo */}
      <div className="flex items-center gap-2 px-4 h-10 border-b border-gray-700/50 flex-shrink-0">
        <div className="w-5 h-5 rounded bg-blue-500 flex items-center justify-center">
          <span className="text-xs font-bold text-white">V</span>
        </div>
        <span className="text-sm font-semibold text-gray-100 tracking-tight">VSWE</span>
      </div>

      {/* New Session button */}
      <div className="px-3 pt-3 pb-1">
        <button
          onClick={handleNewSession}
          disabled={createSession.isPending}
          className="flex items-center gap-2 w-full px-3 py-2 text-sm rounded-md
                     bg-blue-600 hover:bg-blue-500 text-white transition-colors
                     disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Plus className="w-4 h-4" />
          New Session
        </button>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        <SessionList />
      </div>

      {/* Navigation */}
      <nav className="border-t border-gray-700/50 px-2 py-2 space-y-0.5">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-3 py-1.5 text-sm rounded-md transition-colors ${
                isActive
                  ? "bg-gray-700/70 text-gray-100"
                  : "text-gray-400 hover:text-gray-200 hover:bg-gray-700/40"
              }`
            }
          >
            <item.icon className="w-4 h-4" />
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* User profile */}
      {user && (
        <div className="border-t border-gray-700/50 px-3 py-3 flex items-center gap-2">
          {user.avatar_url && (
            <img
              src={user.avatar_url}
              alt={user.github_login}
              className="w-7 h-7 rounded-full"
            />
          )}
          <span className="flex-1 text-sm text-gray-300 truncate">
            {user.github_login}
          </span>
          <button
            onClick={() => {
              logout();
              navigate("/login");
            }}
            className="text-gray-500 hover:text-gray-300 transition-colors"
            title="Logout"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}
