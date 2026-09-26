import { createContext, useContext } from "react";

export const ConsoleContext = createContext(null);

// The live voice console (the browser call): one session for the whole application, so a call keeps
// running while the operator looks at another page.
export function useConsole() {
  const value = useContext(ConsoleContext);

  if (!value) throw new Error("useConsole must be used inside <ConsoleProvider>");

  return value;
}
