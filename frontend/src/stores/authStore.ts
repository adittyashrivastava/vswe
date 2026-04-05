import { create } from "zustand";

export interface User {
  user_id: string;
  github_login: string;
  name: string | null;
  avatar_url: string | null;
  email: string | null;
  orgs: string[];
}

// Hydrate synchronously from localStorage on store creation
function getInitialState() {
  const token = localStorage.getItem("vswe_token");
  const userStr = localStorage.getItem("vswe_user");
  if (token && userStr) {
    try {
      const user = JSON.parse(userStr);
      return { token, user, isAuthenticated: true };
    } catch {
      localStorage.removeItem("vswe_token");
      localStorage.removeItem("vswe_user");
    }
  }
  return { token: null, user: null, isAuthenticated: false };
}

interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  login: (token: string, user: User) => void;
  logout: () => void;
}

const initial = getInitialState();

export const useAuthStore = create<AuthState>((set) => ({
  user: initial.user,
  token: initial.token,
  isAuthenticated: initial.isAuthenticated,

  login: (token, user) => {
    localStorage.setItem("vswe_token", token);
    localStorage.setItem("vswe_user", JSON.stringify(user));
    set({ token, user, isAuthenticated: true });
  },

  logout: () => {
    localStorage.removeItem("vswe_token");
    localStorage.removeItem("vswe_user");
    set({ token: null, user: null, isAuthenticated: false });
  },
}));
