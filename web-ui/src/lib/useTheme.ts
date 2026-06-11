import { useState } from "react";

/**
 * Light/dark theme toggle. The initial paint theme is applied by the inline
 * script in index.html (light by default); this hook keeps React state in sync
 * with the `dark` class on <html> and persists the user's choice.
 */
export function useTheme() {
  const [dark, setDark] = useState(
    () =>
      typeof document !== "undefined" &&
      document.documentElement.classList.contains("dark"),
  );

  const toggle = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("da.theme", next ? "dark" : "light");
    } catch {
      /* ignore */
    }
    setDark(next);
  };

  return { dark, toggle };
}
