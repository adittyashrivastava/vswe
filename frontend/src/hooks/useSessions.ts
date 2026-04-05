import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listSessions, createSession, deleteSession } from "@/lib/api";
import type { SessionCreate } from "@/types/session";
import { useSessionStore } from "@/stores/sessionStore";
import { useAuthStore } from "@/stores/authStore";

export function useListSessions() {
  const setSessions = useSessionStore((s) => s.setSessions);
  const user = useAuthStore((s) => s.user);
  const userId = user?.user_id;

  return useQuery({
    queryKey: ["sessions", userId],
    queryFn: async () => {
      const data = await listSessions(userId!);
      setSessions(data.sessions);
      return data.sessions;
    },
    enabled: !!userId,
  });
}

export function useCreateSession() {
  const queryClient = useQueryClient();
  const addSession = useSessionStore((s) => s.addSession);

  return useMutation({
    mutationFn: (data: SessionCreate) => createSession(data),
    onSuccess: (session) => {
      addSession(session);
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
}

export function useDeleteSession() {
  const queryClient = useQueryClient();
  const removeSession = useSessionStore((s) => s.removeSession);

  return useMutation({
    mutationFn: (id: string) => deleteSession(id),
    onSuccess: (_, id) => {
      removeSession(id);
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
}
