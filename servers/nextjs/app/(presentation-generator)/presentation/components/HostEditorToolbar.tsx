import { Undo2, Redo2 } from "lucide-react";
import { usePresentationUndoRedo } from "../hooks/PresentationUndoRedo";

export default function HostEditorToolbar() {
  const { onUndo, onRedo, canUndo, canRedo } = usePresentationUndoRedo();
  return <div className="flex h-10 shrink-0 items-center gap-2 border-b bg-white px-3" aria-label="编辑工具">
    <button type="button" aria-label="撤销" disabled={!canUndo} onClick={onUndo} className="p-1 disabled:opacity-30"><Undo2 size={16} /></button>
    <button type="button" aria-label="重做" disabled={!canRedo} onClick={onRedo} className="p-1 disabled:opacity-30"><Redo2 size={16} /></button>
    <span className="ml-2 text-xs text-gray-500">双击文字编辑 · 拖动元素调整位置</span>
  </div>;
}
