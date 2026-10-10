import { Suspense } from "react";
import TemplateInspector from "./TemplateInspector";

export default function TemplateInspectorPage() {
  return <Suspense fallback={<p className="p-8">加载模板检验工作台…</p>}><TemplateInspector /></Suspense>;
}
