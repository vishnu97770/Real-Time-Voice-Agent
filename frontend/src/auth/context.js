import { createContext, useContext } from "react";

export const AuthContext = createContext(null);

// { status, user, backend, signIn(email, password), signOut() }
export function useAuth() {
  const value = useContext(AuthContext);

  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");

  return value;
}

// Whether to offer "Sign up". True unless the server itself said sign-up is closed: a server that
// cannot be reached, or has not answered yet, does not hide the option (the sign-up screen explains
// whatever is wrong), while a reachable server that closed sign-up should not be advertising it.
export function useSignupOpen() {
  const { backend } = useAuth();

  return !(backend.reachable && backend.signupEnabled === false);
}
