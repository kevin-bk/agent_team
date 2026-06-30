import { useEffect, useState } from "react";

/**
 * Reactively track whether the `dark` class is on `<html>` so editor surfaces
 * (Monaco) can flip their theme on a runtime theme toggle without a remount.
 */
export function useIsDark(): boolean {
  const [dark, setDark] = useState(
    () =>
      typeof document !== "undefined" &&
      document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    const el = document.documentElement;
    const obs = new MutationObserver(() =>
      setDark(el.classList.contains("dark")),
    );
    obs.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}
