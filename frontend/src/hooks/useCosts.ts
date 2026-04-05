import { useQuery } from "@tanstack/react-query";
import { getCostSummary, getSessionCosts, getSessionDetailedCosts } from "@/lib/api";

export function useCostSummary(from?: string, to?: string) {
  return useQuery({
    queryKey: ["costs", "summary", from, to],
    queryFn: () => getCostSummary(from, to),
  });
}

export function useSessionCosts(sessionId: string | undefined) {
  return useQuery({
    queryKey: ["costs", "session", sessionId],
    queryFn: () => getSessionCosts(sessionId!),
    enabled: !!sessionId,
  });
}

export function useSessionDetailedCosts(sessionId: string | undefined) {
  return useQuery({
    queryKey: ["costs", "session-detailed", sessionId],
    queryFn: () => getSessionDetailedCosts(sessionId!),
    enabled: !!sessionId,
  });
}
