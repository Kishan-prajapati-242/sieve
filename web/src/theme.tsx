// Light / dark, with the three states a theme control actually has:
// explicit light, explicit dark, and "follow the system" — which is the
// default and the one most implementations drop.
//
// The stored value is the USER'S CHOICE, not the resolved theme. Storing
// "dark" when they picked "system" on a dark laptop would silently freeze
// them in dark when their OS later switched.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemeChoice = "light" | "dark" | "system";
const KEY = "sieve-theme";

function systemTheme(): "light" | "dark" {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function apply(choice: ThemeChoice) {
  const resolved = choice === "system" ? systemTheme() : choice;
  document.documentElement.dataset.theme = resolved;
  return resolved;
}

interface ThemeValue {
  choice: ThemeChoice;
  resolved: "light" | "dark";
  setChoice: (c: ThemeChoice) => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(
    () => (localStorage.getItem(KEY) as ThemeChoice | null) ?? "system",
  );
  const [resolved, setResolved] = useState<"light" | "dark">(() => apply(choice));

  useEffect(() => {
    setResolved(apply(choice));
    localStorage.setItem(KEY, choice);
    if (choice !== "system") return;
    // Only while following the system: track OS changes live, so a laptop
    // switching to light at sunset takes the app with it.
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setResolved(apply("system"));
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [choice]);

  return (
    <ThemeContext.Provider value={{ choice, resolved, setChoice: setChoiceState }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeValue {
  const ctx = useContext(ThemeContext);
  // Falls back rather than throwing: a test rendering one component in
  // isolation should not need the provider to render a button.
  return ctx ?? { choice: "dark", resolved: "dark", setChoice: () => {} };
}
