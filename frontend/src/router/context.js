import { createContext, useContext } from "react";

export const RouterContext = createContext(null);

// { route, params, search, navigate(to, { replace, transition }) }
export function useRouter() {
  const value = useContext(RouterContext);

  if (!value) throw new Error("useRouter must be used inside <RouterProvider>");

  return value;
}
