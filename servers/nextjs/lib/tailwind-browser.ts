export const TAILWIND_BROWSER_VERSION = "4.3.3";
export const TAILWIND_BROWSER_SCRIPT_URL =
  (process.env.NEXT_PUBLIC_HOST_ASSET_PREFIX || "") + `/vendor/tailwindcss-browser-${TAILWIND_BROWSER_VERSION}.js`;

export const TAILWIND_BROWSER_STYLE_CONTENT = `
@import "tailwindcss/theme";

@scope ([data-tailwind-runtime-scope]) {
  @tailwind utilities;
}
`;

const TAILWIND_BROWSER_SCRIPT_ATTRIBUTE = "data-presenton-tailwind-browser";
const TAILWIND_BROWSER_STYLE_ATTRIBUTE =
  "data-presenton-tailwind-browser-style";

export function ensureTailwindBrowserStyle() {
  if (typeof document === "undefined") return null;

  const existingStyle = document.querySelector<HTMLStyleElement>(
    `style[${TAILWIND_BROWSER_STYLE_ATTRIBUTE}="true"]`,
  );
  if (existingStyle) return existingStyle;

  const style = document.createElement("style");
  style.type = "text/tailwindcss";
  style.textContent = TAILWIND_BROWSER_STYLE_CONTENT;
  style.setAttribute(TAILWIND_BROWSER_STYLE_ATTRIBUTE, "true");
  document.head.appendChild(style);
  return style;
}

export function ensureTailwindBrowserScript() {
  if (typeof document === "undefined") return null;

  ensureTailwindBrowserStyle();

  const existingScript = document.querySelector<HTMLScriptElement>(
    `script[${TAILWIND_BROWSER_SCRIPT_ATTRIBUTE}="true"], script[src$="${TAILWIND_BROWSER_SCRIPT_URL}"]`,
  );
  if (existingScript) return existingScript;

  const script = document.createElement("script");
  script.src = TAILWIND_BROWSER_SCRIPT_URL;
  script.async = true;
  script.setAttribute(TAILWIND_BROWSER_SCRIPT_ATTRIBUTE, "true");
  document.head.appendChild(script);
  return script;
}
