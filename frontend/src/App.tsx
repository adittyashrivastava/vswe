import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { ChatView } from "@/components/chat/ChatView";
import { CostDashboard } from "@/components/dashboard/CostDashboard";
import { ConfigPanel } from "@/components/config/ConfigPanel";
import { JobsPanel } from "@/components/jobs/JobsPanel";
import { LoginPage } from "@/components/auth/LoginPage";
import { AuthCallback } from "@/components/auth/AuthCallback";
import { useAuthStore } from "@/stores/authStore";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      {/* Public routes */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/auth/callback" element={<AuthCallback />} />
      <Route path="/auth/success" element={<AuthCallback />} />

      {/* Protected routes */}
      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatView />} />
        <Route path="/chat/:sessionId" element={<ChatView />} />
        <Route path="/costs" element={<CostDashboard />} />
        <Route path="/config" element={<ConfigPanel />} />
        <Route path="/jobs" element={<JobsPanel />} />
      </Route>
    </Routes>
  );
}
