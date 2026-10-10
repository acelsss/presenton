export type HostEditorConfig = { documentId: string; sessionId: string; nativeId: string; base: string };
let host: HostEditorConfig | null = null;
export function configureHostEditor(nativeId: string, base: string) {
  const match = /^\/api\/presentations\/(ppt_[a-zA-Z0-9]+)\/editor\/([a-f0-9-]{36})\/native$/.exec(base);
  if (!match) throw new Error("Invalid host editor scope");
  host = { documentId: match[1], sessionId: match[2], nativeId, base };
}
export function hostEditorConfig() { return host; }
export function hostAsset(path: string) {
  return host && (path.startsWith("/vendor/") || path.startsWith("/figma/presentation-actions/")) ? `/api/presentation-editor-assets${path}` : path;
}
