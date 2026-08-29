export type PremiereThemeClass = "theme-dark" | "theme-light";

type PremierePalette = Record<`--uxp-host-${string}`, string>;

interface ThemeListenerCollection {
  addListener(listener: (theme: string) => void): void;
  removeListener(listener: (theme: string) => void): void;
}

interface PremiereThemeApi {
  getCurrent(): string;
  onUpdated: ThemeListenerCollection;
}

interface ThemeDocument {
  documentElement?: {
    dataset: Record<string, string | undefined>;
    style?: { setProperty(name: string, value: string): void };
  };
  body: {
    classList: {
      add(...tokens: string[]): void;
      remove(...tokens: string[]): void;
    };
  };
  theme?: PremiereThemeApi;
}

const premierePalettes = {
  darkest: {
    "--uxp-host-background-color": "#1D1D1D",
    "--uxp-host-text-color": "#D0D0D0",
    "--uxp-host-border-color": "#303030",
    "--uxp-host-link-text-color": "#0098FA",
    "--uxp-host-link-hover-text-color": "#3DACFE",
    "--uxp-host-label-text-color": "#B0B0B0",
    "--uxp-host-widget-hover-background-color": "#000000",
    "--uxp-host-widget-hover-text-color": "#D0D0D0",
    "--uxp-host-widget-hover-border-color": "#4B4B4B",
    "--uxp-host-text-color-secondary": "#B0B0B0",
  },
  dark: {
    "--uxp-host-background-color": "#323232",
    "--uxp-host-text-color": "#D1D1D1",
    "--uxp-host-border-color": "#3F3F3F",
    "--uxp-host-link-text-color": "#2DA5FD",
    "--uxp-host-link-hover-text-color": "#57AFF0",
    "--uxp-host-label-text-color": "#B2B2B2",
    "--uxp-host-widget-hover-background-color": "#1D1D1D",
    "--uxp-host-widget-hover-text-color": "#D1D1D1",
    "--uxp-host-widget-hover-border-color": "#545454",
    "--uxp-host-text-color-secondary": "#B2B2B2",
  },
  light: {
    "--uxp-host-background-color": "#F8F8F8",
    "--uxp-host-text-color": "#464646",
    "--uxp-host-border-color": "#E6E6E6",
    "--uxp-host-link-text-color": "#0067E4",
    "--uxp-host-link-hover-text-color": "#0056BD",
    "--uxp-host-label-text-color": "#6D6D6D",
    "--uxp-host-widget-hover-background-color": "#FFFFFF",
    "--uxp-host-widget-hover-text-color": "#464646",
    "--uxp-host-widget-hover-border-color": "#D5D5D5",
    "--uxp-host-text-color-secondary": "#6D6D6D",
  },
} satisfies Record<string, PremierePalette>;

const premiereMacLinkColors = {
  darkest: ["#4096F3", "#5EAAF7"],
  dark: ["#54A3F6", "#72B7F9"],
  light: ["#147AF3", "#0265DC"],
} as const;

function normalizedTheme(theme: string): keyof typeof premierePalettes {
  const value = theme.toLowerCase();
  if (value.includes("light")) return "light";
  return value.includes("darkest") ? "darkest" : "dark";
}

export function premiereThemeClass(theme: string): PremiereThemeClass {
  return normalizedTheme(theme) === "light" ? "theme-light" : "theme-dark";
}

export function premiereThemePalette(
  theme: string,
  platform: string,
): PremierePalette {
  const name = normalizedTheme(theme);
  const palette = { ...premierePalettes[name] };
  if (platform === "darwin") {
    const [link, linkHover] = premiereMacLinkColors[name];
    palette["--uxp-host-link-text-color"] = link;
    palette["--uxp-host-link-hover-text-color"] = linkHover;
  }
  return palette;
}

export function installPremiereTheme(
  target: ThemeDocument,
  platform = "unknown",
): () => void {
  const applyTheme = (theme: string) => {
    target.body.classList.remove("theme-dark", "theme-light");
    target.body.classList.add(premiereThemeClass(theme));
    const root = target.documentElement;
    if (root) {
      root.dataset.theme = theme.toLowerCase();
      root.dataset.platform = platform;
      for (const [name, value] of Object.entries(
        premiereThemePalette(theme, platform),
      )) {
        root.style?.setProperty(name, value);
      }
    }
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
