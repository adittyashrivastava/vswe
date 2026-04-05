const API_BASE = import.meta.env.VITE_API_URL || "/api";

export function LoginPage() {
  const handleGitHubLogin = () => {
    // Redirect to backend which will redirect to GitHub OAuth
    window.location.href = `${API_BASE}/auth/github/login`;
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-900">
      <div className="w-full max-w-md rounded-lg bg-gray-800 p-8 shadow-xl">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold text-gray-100">VSWE</h1>
          <p className="mt-2 text-gray-400">Virtual Software Engineer</p>
        </div>

        <button
          onClick={handleGitHubLogin}
          className="flex w-full items-center justify-center gap-3 rounded-lg bg-gray-700 px-4 py-3 text-gray-100 transition-colors hover:bg-gray-600"
        >
          <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 24 24">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
          </svg>
          Sign in with GitHub
        </button>

        <p className="mt-6 text-center text-sm text-gray-500">
          Sign in with your GitHub account to get started.
          The agent will have access to repos where the GitHub App is installed.
        </p>
      </div>
    </div>
  );
}
