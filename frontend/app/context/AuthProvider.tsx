"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import {
  confirmSignup as apiConfirmSignup,
  login as apiLogin,
  logout as apiLogout,
  me as apiMe,
  signup as apiSignup,
  type AuthUser,
} from "../lib/auth";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, orgSlug: string) => Promise<void>;
  confirmSignup: (email: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const authUser = await apiLogin(email, password);
    setUser(authUser);
  }, []);

  const signup = useCallback(async (email: string, password: string, orgSlug: string) => {
    await apiSignup(email, password, orgSlug);
  }, []);

  const confirmSignup = useCallback(async (email: string, code: string) => {
    await apiConfirmSignup(email, code);
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, signup, confirmSignup, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within an AuthProvider");
  return context;
}
