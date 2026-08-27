export type PremiereThemeClass = "theme-dark" | "theme-light";

interface ThemeListenerCollection {
  addListener(listener: (theme: string) => void): void;
  removeListener(listener: (theme: string) => void): void;
}

interface PremiereThemeApi {
  getCurrent(): string;
  onUpdated: ThemeListenerCollection;
}

interface ThemeDocument {
  body: {
    classList: {
      add(...tokens: string[]): void;
      remove(...tokens: string[]): void;
    };
  };
  theme?: PremiereThemeApi;
}

export function premiereThemeClass(theme: string): PremiereThemeClass {
  return theme.toLowerCase().includes("light") ? "theme-light" : "theme-dark";
}

export function installPremiereTheme(target: ThemeDocument): () => void {
  const applyTheme = (theme: string) => {
    target.body.classList.remove("theme-dark", "theme-light");
    target.body.classList.add(premiereThemeClass(theme));
  };

  const theme = target.theme;
  if (!theme) {
    applyTheme("dark");
    return () => undefined;
  }

  applyTheme(theme.getCurrent());
  theme.onUpdated.addListener(applyTheme);
  return () => theme.onUpdated.removeListener(applyTheme);
}
