import { useEffect, useRef } from "react";
import { hostEditorConfig } from "@/utils/host-editor";

export type EditorSaveStatus = { dirty: boolean; saving: boolean; conflict: boolean; revision: number; ready: boolean };
export function useHostEditorBridge(options: {
  enabled: boolean; selectedSlide: number; slideId?: string; readOnly: boolean;
  getStatus: () => EditorSaveStatus; flush: () => Promise<void>;
  reload: (options: { clearHistory: boolean; shouldApply: () => boolean }) => Promise<unknown>;
}) {
  const latest = useRef(options); latest.current = options;
  useEffect(() => {
    const config = hostEditorConfig();
    if (!options.enabled || !config) return;
    let refreshing = false;
    let flushRequestId: string | undefined;
    const publish = () => {
      const value = latest.current;
      window.parent.postMessage({ type: "presenton:state", documentId: config.documentId, sessionId: config.sessionId,
        ...value.getStatus(), slideId: value.slideId ?? null, slideIndex: value.selectedSlide, flushRequestId }, window.location.origin);
    };
    const receive = async (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== window.parent
        || event.data?.documentId !== config.documentId || event.data?.sessionId !== config.sessionId) return;
      const current = latest.current;
      if (event.data.type === "presenton:flush") {
        if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        await current.flush();
        flushRequestId = typeof event.data.requestId === "string" ? event.data.requestId : undefined;
        publish();
      }
      if (event.data.type === "presenton:refresh" && !refreshing) {
        const status = current.getStatus();
        const modeChanged = typeof event.data.readOnly === "boolean" && event.data.readOnly !== current.readOnly;
        if (!status.ready || status.dirty || status.saving || status.conflict || !modeChanged && event.data.revision <= status.revision) return;
        refreshing = true;
        try { await current.reload({ clearHistory: true, shouldApply: () => {
          const state = latest.current.getStatus();
          return !state.dirty && !state.saving && !state.conflict;
        } }); } finally { refreshing = false; publish(); }
      }
    };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (latest.current.getStatus().dirty) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("message", receive);
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("input", publish, true);
    document.addEventListener("focusin", publish, true);
    const timer = window.setInterval(publish, 500);
    publish();
    return () => { clearInterval(timer); window.removeEventListener("message", receive); window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("input", publish, true); document.removeEventListener("focusin", publish, true); };
  }, [options.enabled]);
}
