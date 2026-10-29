import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, getToken, setToken } from "../api/client";
import type { TokenResponse, User } from "../api/types";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  acceptToken: (res: TokenResponse) => User;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(Boolean(getToken()));

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      return;
    }
    try {
      setUser(await api<User>("/auth/me"));
    } catch {
      setToken(null);
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
    const onLogout = () => setUser(null);
    window.addEventListener("allergyguard:logout", onLogout);
    return () => window.removeEventListener("allergyguard:logout", onLogout);
  }, [refresh]);

  const acceptToken = useCallback((res: TokenResponse) => {
    setToken(res.access_token);
    setUser(res.user);
    return res.user;
  }, []);

  const login = useCallback(
    async (email: string, password: string) =>
      acceptToken(await api<TokenResponse>("/auth/login", { method: "POST", json: { email, password } })),
    [acceptToken],
  );

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, acceptToken, logout, refresh }),
    [user, loading, login, acceptToken, logout, refresh],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
