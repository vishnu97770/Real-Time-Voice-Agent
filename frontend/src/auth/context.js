import { createContext, useContext } from "react";

export const AuthContext = createContext(null);

// { status, user, backend, signIn(email, password), signOut() }
export function useAuth() {
  const value = useContext(AuthContext);

  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");

  return value;
}
