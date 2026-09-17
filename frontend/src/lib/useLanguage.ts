import { useSyncExternalStore } from "react";
import { getLanguage, subscribeLanguage } from "./i18n";
export const useLanguage = () => useSyncExternalStore(subscribeLanguage, getLanguage, () => "ko" as const);
