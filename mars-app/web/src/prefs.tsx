import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { EN } from './en';

/** Per-browser preferences: colour theme and interface language, kept in localStorage. */
export type Theme = 'system' | 'light' | 'dark';
export type Lang = 'zh' | 'en';
type Params = Record<string, string | number | null | undefined>;

function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = window.localStorage.getItem(key) as T | null;
    return value && allowed.includes(value) ? value : fallback;
  } catch {
    return fallback; // storage blocked (private window)
  }
}

function save(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* preference just won't survive a reload */
  }
}

function fill(text: string, params?: Params): string {
  return params ? text.replace(/\{(\w+)\}/g, (_match, key: string) => (params[key] ?? '—').toString()) : text;
}

interface Prefs {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  lang: Lang;
  setLang: (lang: Lang) => void;
}

const PrefsContext = createContext<Prefs | null>(null);

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => stored('mars.theme', ['system', 'light', 'dark'], 'system'));
  const [lang, setLangState] = useState<Lang>(() => stored('mars.lang', ['zh', 'en'], 'zh'));

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'system') delete root.dataset.theme;
    else root.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    document.documentElement.lang = lang === 'en' ? 'en' : 'zh-CN';
    document.title = lang === 'en' ? 'MARS Research Workbench' : 'MARS 研究工作台';
  }, [lang]);

  const value = useMemo<Prefs>(() => ({
    theme,
    setTheme: (next) => (setThemeState(next), save('mars.theme', next)),
    lang,
    setLang: (next) => (setLangState(next), save('mars.lang', next)),
  }), [theme, lang]);
  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}

export function usePrefs(): Prefs {
  const prefs = useContext(PrefsContext);
  if (!prefs) throw new Error('usePrefs outside PrefsProvider');
  return prefs;
}

const missing = new Set<string>();

/**
 * Interface strings are written in Chinese and looked up in the English table by that same text, so
 * `t('共 {n} 轮', { n })` reads naturally at the call site. Unknown text falls back to the original: model output,
 * file names and core error messages pass through unchanged.
 */
export function useT(): (zh: string, params?: Params) => string {
  const { lang } = usePrefs();
  return useCallback((zh: string, params?: Params) => {
    if (lang === 'zh') return fill(zh, params);
    const en = EN[zh];
    if (en === undefined && import.meta.env.DEV && !missing.has(zh)) {
      missing.add(zh);
      console.debug('[i18n] missing English for:', zh);
    }
    return fill(en ?? zh, params);
  }, [lang]);
}
