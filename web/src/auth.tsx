// Session state for the frontend.
//
// The session cookie is HttpOnly, so JavaScript cannot read it — which is the
// point. The client therefore never inspects a token; it asks the server who
// it is (`GET /api/auth/me`) and caches that answer in React Query. Signing
// in or out invalidates that cache rather than mutating local state, so there
// is exactly one source of truth and no way for the two to disagree.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";

export interface User {
  id: number;
  email: string;
}

async function fetchMe(): Promise<User | null> {
  const resp = await fetch("/api/auth/me", { credentials: "include" });
  if (resp.status === 401) return null; // signed out is a normal state, not an error
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function post(path: string, body?: unknown): Promise<Response> {
  const resp = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp
      .json()
      .then((d) => d.detail)
      .catch(() => null);
    throw new Error(detail ?? `HTTP ${resp.status}`);
  }
  return resp;
}

interface AuthValue {
  user: User | null;
  isLoading: boolean;
  signup: (email: string, password: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["me"],
    queryFn: fetchMe,
    retry: false,
    staleTime: 60_000,
  });

  // Every auth transition clears the whole cache, not just ["me"]. Collections
  // are user-scoped, so a stale list from the previous account would otherwise
  // survive a sign-out and be shown to the next person at this browser.
  const reset = async () => {
    await qc.invalidateQueries();
  };

  const signupM = useMutation({
    mutationFn: (v: { email: string; password: string }) => post("/api/auth/signup", v),
    onSuccess: reset,
  });
  const loginM = useMutation({
    mutationFn: (v: { email: string; password: string }) => post("/api/auth/login", v),
    onSuccess: reset,
  });
  const logoutM = useMutation({
    mutationFn: () => post("/api/auth/logout"),
    onSuccess: async () => {
      qc.clear();
      await qc.invalidateQueries();
    },
  });

  return (
    <AuthContext.Provider
      value={{
        user: data ?? null,
        isLoading,
        signup: async (email, password) => void (await signupM.mutateAsync({ email, password })),
        login: async (email, password) => void (await loginM.mutateAsync({ email, password })),
        logout: async () => void (await logoutM.mutateAsync()),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
