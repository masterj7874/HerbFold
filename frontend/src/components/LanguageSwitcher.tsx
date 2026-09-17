import { Languages } from "lucide-react";
import { setLanguage } from "../lib/i18n";
import { useLanguage } from "../lib/useLanguage";
import "./language-switcher.css";

export default function LanguageSwitcher() {
  const language = useLanguage();
  return <div className="language-switcher" role="group" aria-label={language === "en" ? "Interface language" : "화면 언어"} data-testid="language-switcher">
    <Languages size={15} aria-hidden="true" />
    <button type="button" lang="en" aria-label="Switch to English" aria-pressed={language === "en"} onClick={() => setLanguage("en")} data-testid="language-en">EN</button>
    <button type="button" lang="ko" aria-label="한국어로 전환" aria-pressed={language === "ko"} onClick={() => setLanguage("ko")} data-testid="language-ko" title="한국어 · Korean">KO</button>
  </div>;
}
