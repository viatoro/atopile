import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";

/**
 * A boolean flag that auto-resets after a timeout, optionally released
 * early when any value in `releaseDeps` changes while the flag is active.
 *
 * @param timeoutMs  Time in ms before the flag resets to `false`.
 * @param releaseDeps  Optional dependency list — any change while active
 *                     immediately resets the flag.
 * @returns `[active, raise]` — `raise()` sets the flag to `true`.
 */
export function useWaitFlag(
  releaseDeps: DependencyList,
  timeoutMs: number = 5000,
): [boolean, () => void] {
  const [active, setActive] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const raise = useCallback(() => {
    setActive(true);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setActive(false), timeoutMs);
  }, [timeoutMs]);

  // Clean up on unmount
  useEffect(() => () => clearTimeout(timerRef.current), []);

  // Release when deps change while active
  useEffect(() => {
    if (active) {
      setActive(false);
      clearTimeout(timerRef.current);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, releaseDeps);

  return [active, raise];
}
