import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { getMe } from "@/lib/api";

export function AuthCallback() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = searchParams.get("token");
    if (!token) {
      setError("No token received from GitHub. Please try again.");
      return;
    }

    // Store token first so API calls include it
    localStorage.setItem("vswe_token", token);

    // Fetch full user info from backend
    getMe()
      .then((user) => {
        login(token, {
          user_id: user.user_id,
          github_login: user.github_login,
          name: user.name,
          avatar_url: user.avatar_url,
          email: user.email,
          orgs: user.orgs,
        });
        navigate("/chat", { replace: true });
      })
      .catch((err) => {
        setError(`Failed to fetch user info: ${err.message}`);
        localStorage.removeItem("vswe_token");
      });
  }, [searchParams, login, navigate]);

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-900">
        <div className="max-w-md rounded-lg bg-gray-800 p-8 text-center">
          <p className="text-red-400">{error}</p>
          <a
            href="/login"
            className="mt-4 inline-block text-blue-400 hover:text-blue-300"
          >
            Try again
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-900">
      <div className="text-center">
        <div className="mb-4 h-8 w-8 animate-spin rounded-full border-2 border-gray-600 border-t-blue-400 mx-auto" />
        <p className="text-gray-400">Signing you in...</p>
      </div>
    </div>
  );
}
