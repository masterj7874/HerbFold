import { useSyncExternalStore } from "react";

const query = "(prefers-reduced-motion: reduce)";
const snapshot = () => typeof window !== "undefined" && window.matchMedia(query).matches;
const serverSnapshot = () => false;
const subscribe = (notify: () => void) => {
  const preference = window.matchMedia(query);
  preference.addEventListener("change", notify);
  return () => preference.removeEventListener("change", notify);
};

/** Keep an open studio in sync when the system motion preference changes. */
export function useMotionPreference() {
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot);
}
