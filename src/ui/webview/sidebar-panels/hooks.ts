import { useCallback, useState } from "react";

/**
 * Manages a Set<string> toggle state — useful for expand/collapse
 * of tree groups where multiple items can be independently toggled.
 */
export function useToggleSet(initial?: Iterable<string>) {
  const [set, setSet] = useState<Set<string>>(() => new Set(initial));

  const toggle = useCallback((key: string) => {
    setSet((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  return [set, toggle] as const;
}
