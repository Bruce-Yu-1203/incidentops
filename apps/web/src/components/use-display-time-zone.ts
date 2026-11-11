"use client";

import { useSyncExternalStore } from "react";

const subscribe = () => () => {};
const browserTimeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
const serverTimeZone = () => "UTC";

/** Hydrate with the same UTC snapshot, then adopt the browser's local timezone. */
export function useDisplayTimeZone(): string {
  return useSyncExternalStore(subscribe, browserTimeZone, serverTimeZone);
}
