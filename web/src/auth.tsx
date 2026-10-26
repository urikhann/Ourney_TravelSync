import { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, getToken } from "./api";
import type { User } from "./types";
import { Loading } from "./ui";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/me"), enabled: !!getToken(), staleTime: 60_000 });
}

/** Wrap screens that need login. Sends people to /login and brings them back afterwards. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const loc = useLocation();
  const me = useMe();
  if (!getToken()) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname)}`} replace />;
  if (me.isLoading) return <Loading />;
  return <>{children}</>;
}
