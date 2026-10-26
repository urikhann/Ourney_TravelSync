import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Features } from "./types";

/** Which optional parts the backend has switched on (e.g. the availability checker). Cached for the session. */
export function useFeatures(): Features {
  const q = useQuery({ queryKey: ["features"], queryFn: () => api.get<Features>("/meta/features"), staleTime: Infinity });
  return q.data ?? { availability: false };
}
