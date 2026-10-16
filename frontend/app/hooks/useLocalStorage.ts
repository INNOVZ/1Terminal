"use client";

import { useSyncExternalStore, useCallback } from "react";

function subscribe(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener("local-storage-update", callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener("local-storage-update", callback);
  };
}

/**
 * Hook to synchronize state with localStorage without cascading renders or SSR hydration mismatch.
 * Uses React 18/19 `useSyncExternalStore` for external store subscription.
 */
export function useLocalStorage<T>(
  key: string,
  defaultValue: T,
  deserializer: (raw: string) => T = (v) => v as unknown as T
): [T, (value: T | ((prev: T) => T)) => void] {
  const getSnapshot = useCallback(() => {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }, [key]);

  const getServerSnapshot = useCallback(() => null, []);

  const raw = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const value = raw !== null ? deserializer(raw) : defaultValue;

  const setValue = useCallback(
    (action: T | ((prev: T) => T)) => {
      try {
        const currentRaw = localStorage.getItem(key);
        const current =
          currentRaw !== null ? deserializer(currentRaw) : defaultValue;
        const nextValue =
          typeof action === "function"
            ? (action as (prev: T) => T)(current)
            : action;
        localStorage.setItem(key, String(nextValue));
        window.dispatchEvent(new Event("local-storage-update"));
      } catch (e) {
        console.error("Failed to save to localStorage:", e);
      }
    },
    [key, defaultValue, deserializer]
  );

  return [value, setValue];
}
