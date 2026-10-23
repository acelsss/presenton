"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";
import { getApiUrl, getApiErrorMessage } from "@/utils/api";

const NativeSlide = dynamic(() => import("@/components/slide-editor/surface/TemplateV2KonvaSlide").then(module => module.TemplateV2KonvaSlide), { ssr: false });

type Json = Record<string, unknown>;
type PackNode = { id: string; type: string; position?: { x: number; y: number }; size?: { width: number; height: number }; content?: Json; props?: Json; children?: PackNode[]; styleRef?: string };
type Entry = { id: string; name?: string; type?: string; root?: PackNode; bindings?: Record<string, { nodeId: string; property: string }>; compositionRef?: string; tokens?: Record<string, Json>; mimeType?: string; role?: string; sha256?: string };
type Category = "themes" | "elementStyles" | "compositions" | "pagePatterns" | "layouts" | "assets";
type Pack = { name: string; version: string; defaultThemeRef: string; canvas: { width: number; height: number }; provenance: Json } & Record<Category, Entry[]>;
type Finding = { severity: string; code: string; message?: string; nodeId?: string; sourceSlide?: number; assetId?: string };
type Report = { counts: Record<string, number>; checks: Record<string, string>; findings: Finding[]; sourceHash: string; status: string };
type Detail = { id: string; templateId: string; name: string; version: string; sourceHash: string; source: Pack; report: Report; assetUrls: Record<string, string> };
type Summary = Omit<Detail, "source" | "report" | "assetUrls">;
type Compiled = { ui: TemplateV2Layout; resolvedRoot: PackNode; nodePaths: Record<string, string>; contentSchema?: Json; sourceHash: string; findings?: Finding[]; previewEntryId?: string };
const categories: [Category, string][] = [["themes", "主题"], ["elementStyles", "基本元素样式"], ["compositions", "组合"], ["pagePatterns", "页面模式"], ["layouts", "固定布局"], ["assets", "资源"]];
const button = "rounded border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50 disabled:opacity-40";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(getApiUrl(`/api/v1/ppt/template-packs${path}`), { credentials: "include", ...init });
  if (!response.ok) throw new Error(await getApiErrorMessage(response, `请求失败 (${response.status})`));
  return response.json();
}
function post(value: unknown): RequestInit { return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) }; }
function format(value: unknown) { return JSON.stringify(value, null, 2); }
function nodeEntries(root?: PackNode, offset = { x: 0, y: 0 }): { node: PackNode; x: number; y: number }[] {
  if (!root) return [];
  const x = offset.x + (root.position?.x ?? 0), y = offset.y + (root.position?.y ?? 0);
  return [{ node: root, x, y }, ...(root.children ?? []).flatMap(child => nodeEntries(child, { x, y }))];
}

export default function TemplateInspector() {
  const params = useSearchParams();
  const [versions, setVersions] = useState<Summary[]>([]);
  const [versionTotal, setVersionTotal] = useState(0);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [category, setCategory] = useState<Category>("layouts");
  const [selectedId, setSelectedId] = useState("");
  const [theme, setTheme] = useState("");
  const [compiled, setCompiled] = useState<Compiled | null>(null);
  const [input, setInput] = useState("");
  const [rawDraft, setRawDraft] = useState("");
  const [jsonTab, setJsonTab] = useState("定义");
  const [selectedNode, setSelectedNode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [scale, setScale] = useState(.5);
  const previewRef = useRef<HTMLDivElement>(null);
  const requestSequence = useRef(0);
  const diagnosticSelection = useRef<{ layoutId: string; nodeId: string } | null>(null);
  const pack = detail?.source;
  const entry = pack?.[category].find(item => item.id === selectedId);
  const allNodes = nodeEntries(compiled?.resolvedRoot);
  const highlight = allNodes.find(item => item.node.id === selectedNode);
  const editable = category === "layouts" || category === "compositions" || category === "pagePatterns";

  async function loadList() { const response = await api<{ items: Summary[]; total: number }>(""); setVersions(response.items); setVersionTotal(response.total); return response.items; }
  function adopt(value: Detail) {
    requestSequence.current++;
    setDetail(value); setTheme(value.source.defaultThemeRef); setCategory("layouts"); setSelectedId(value.source.layouts[0]?.id ?? "");
    setRawDraft(format(value.source)); setCompiled(null); setInput(""); setError("");
    const url = new URL(window.location.href); url.searchParams.set("version", value.id); window.history.replaceState(null, "", url);
  }
  async function run(operation: () => Promise<void>) {
    setBusy(true); setError(""); try { await operation(); } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); } finally { setBusy(false); }
  }
  useEffect(() => { let disposed = false; void (async () => { try { const response = await api<{ items: Summary[]; total: number }>(""); if (disposed) return; setVersions(response.items); setVersionTotal(response.total); const id = params.get("version") ?? response.items[0]?.id; if (id) { const value = await api<Detail>(`/${id}`); if (!disposed) adopt(value); } } catch (failure) { if (!disposed) setError(String(failure)); } })(); return () => { disposed = true; }; }, []); // initial version only; subsequent selection is explicit
  useEffect(() => {
    if (!previewRef.current || !pack) return;
    const observer = new ResizeObserver(entries => setScale(Math.min(1, entries[0].contentRect.width / pack.canvas.width)));
    observer.observe(previewRef.current); return () => observer.disconnect();
  }, [pack, category]);

  function previewRequest() {
    if (!entry || !pack) return null;
    const base = { themeRef: theme };
    if (category === "layouts") return { ...base, generationMode: "template", ref: entry.id };
    if (category === "compositions" || category === "pagePatterns") return { ...base, generationMode: "composition", ref: entry.compositionRef ?? entry.id };
    if (category === "elementStyles") {
      const node: PackNode = { id: "style_example", type: entry.type ?? "text", styleRef: entry.id, position: { x: 60, y: 70 }, size: { width: 700, height: 180 } };
      if (node.type === "text") { node.content = { text: "理解大语言模型 · Aa 0123456789" }; node.props = { runs: [] }; }
      else if (node.type === "vector") node.props = { points: [{ x: 0, y: 0 }, { x: 420, y: 0 }, { x: 420, y: 180 }, { x: 0, y: 180 }], closed: true };
      else return null;
      return { ...base, generationMode: "composition", root: { id: "style_sheet", type: "group", children: [node] } };
    }
    return null;
  }
  useEffect(() => {
    setCompiled(null); setSelectedNode(""); setError(""); setInput("");
    const request = previewRequest(); const sequence = ++requestSequence.current;
    if (!request || !detail) return;
    void api<Compiled>(`/${detail.id}/compile`, post(request)).then(value => {
      if (sequence !== requestSequence.current) return;
      setCompiled({ ...value, previewEntryId: selectedId });
      if (diagnosticSelection.current?.layoutId === selectedId) {
        setSelectedNode(diagnosticSelection.current.nodeId);
        diagnosticSelection.current = null;
      }
      if (category === "layouts") {
        const properties = value.contentSchema?.properties as Record<string, { default: unknown }>;
        setInput(format(Object.fromEntries(Object.entries(properties ?? {}).map(([key, prop]) => [key, prop.default]))));
      } else setInput(format(value.resolvedRoot));
    }).catch(failure => { if (sequence === requestSequence.current) setError(String(failure)); });
  }, [detail?.id, category, selectedId, theme]);

  async function compileInput() {
    if (!detail) return;
    const request = previewRequest(); if (!request) return;
    const sequence = ++requestSequence.current;
    const payload = category === "layouts" ? { ...request, content: JSON.parse(input) } : { ...request, ref: undefined, root: JSON.parse(input) };
    const result = await api<Compiled>(`/${detail.id}/compile`, post(payload));
    if (sequence === requestSequence.current) setCompiled({ ...result, previewEntryId: selectedId });
  }
  async function saveVersion(source: Pack) {
    if (!detail) return;
    const value = await api<Detail>(`/${detail.id}/versions`, post({ expectedSourceHash: detail.sourceHash, source }));
    adopt(value); await loadList();
  }
  const selectedJson = jsonTab === "完整包" ? pack : jsonTab === "解析树" ? compiled?.resolvedRoot : jsonTab === "Native UI" ? compiled?.ui : jsonTab === "Schema / 绑定" ? { schema: compiled?.contentSchema, bindings: entry?.bindings, nodePaths: compiled?.nodePaths } : entry;

  return <main className="min-h-screen bg-slate-100 text-slate-900">
    <header className="border-b bg-white px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><a className="text-xs text-slate-500 underline" href="/dashboard">返回模板管理</a><h1 className="mt-1 text-xl font-semibold">三层模板检验工作台</h1></div>
        <div className="flex flex-wrap gap-2"><select aria-label="模板版本" className={button} value={detail?.id ?? ""} disabled={busy} onChange={event => void run(async () => adopt(await api<Detail>(`/${event.target.value}`)))}><option value="">选择版本</option>{versions.map(item => <option key={item.id} value={item.id}>{item.name} · {item.version.slice(0, 8)}</option>)}</select>
          <label className={button}>导入 PPTX<input aria-label="导入 PPTX" type="file" accept=".pptx" className="hidden" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (file) void run(async () => { const form = new FormData(); form.append("pptx_file", file); adopt(await api<Detail>("/import", { method: "POST", body: form })); await loadList(); }); event.target.value = ""; }} /></label>
          <button className={button} disabled={!detail || busy} onClick={() => void run(async () => { if (detail) { const report = await api<Report>(`/${detail.id}/validate`, { method: "POST" }); setDetail({ ...detail, report }); } })}>重新校验</button>
          {versions.length < versionTotal && <button className={button} disabled={busy} onClick={() => void run(async () => { const response = await api<{ items: Summary[] }>(`?page=${Math.floor(versions.length / 100) + 1}`); setVersions([...versions, ...response.items]); })}>更多版本 ({versions.length}/{versionTotal})</button>}
        </div></div>
      {detail && <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-600"><span>草稿 · 版本 {detail.version}</span><span className="break-all">SHA-256 {detail.sourceHash}</span><select aria-label="预览主题" className="rounded border p-1" value={theme} onChange={event => setTheme(event.target.value)}>{pack?.themes.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><span>视觉 / 编辑保存 / PPTX / PDF：未验收</span></div>}
    </header>
    {busy && <p role="status" className="px-6 py-2">正在处理…</p>}
    {error && <p role="alert" className="break-words border-b border-red-200 bg-red-50 px-6 py-3 text-sm text-red-800">{error}</p>}
    {!pack ? <p className="p-8 text-slate-600">导入 PPTX 后逐项检查主题、组合、布局、元素样式和资源。解析失败的条目也保留诊断。</p> : <>
      <div className="grid gap-4 p-4 xl:grid-cols-[250px_minmax(0,1fr)_390px]">
        <aside className="rounded border bg-white p-3"><h2 className="mb-3 font-medium">完整目录</h2>{categories.map(([key, label]) => <section key={key} className="mb-3"><button className="flex w-full justify-between py-2 text-sm font-medium" onClick={() => { setCategory(key); setSelectedId(pack[key][0]?.id ?? ""); }}>{label}<span className="text-slate-500">{pack[key].length}</span></button>{category === key && <div className="max-h-[520px] overflow-auto">{pack[key].map(item => <button key={item.id} data-entry-id={item.id} className={`mb-1 block w-full rounded px-2 py-2 text-left text-xs ${selectedId === item.id ? "bg-slate-900 text-white" : "hover:bg-slate-100"}`} onClick={() => setSelectedId(item.id)}><span className="block">{item.name ?? item.id}</span><span className="block break-all opacity-60">{item.id}</span></button>)}</div>}</section>)}</aside>
        <section className="min-w-0 space-y-4">
          {(compiled?.findings?.length ?? 0) > 0 && <div className="rounded border border-amber-200 bg-amber-50 p-3 text-xs">{compiled?.findings?.map((finding, index) => <p key={index}>{finding.code} · {finding.nodeId} · {finding.message}</p>)}</div>}
          <div className="rounded border bg-white p-4"><h2 className="mb-3 font-medium">{entry?.name ?? entry?.id} <span className="text-xs font-normal text-slate-500">{category === "layouts" ? "模板填充模式" : "元素组合模式"}</span></h2>
            <div ref={previewRef} className="w-full overflow-hidden" style={{ minHeight: 80 }}>
              {compiled && <div data-testid="native-preview" data-compiled-id={compiled.ui.id} data-preview-entry-id={compiled.previewEntryId} className="relative" style={{ width: pack.canvas.width * scale, height: pack.canvas.height * scale }}><div style={{ width: pack.canvas.width, height: pack.canvas.height, transform: `scale(${scale})`, transformOrigin: "top left" }}><NativeSlide key={`${detail?.id}/${category}/${selectedId}/${theme}`} layout={compiled.ui} isEditMode={false} slideId={null} slideIndex={0} enableViewportCulling={false} />
                {highlight?.node.size && <div data-testid="node-highlight" className="pointer-events-none absolute border-2 border-blue-500 bg-blue-500/10" style={{ left: highlight.x, top: highlight.y, width: highlight.node.size.width, height: highlight.node.size.height }} />}</div></div>}
              {category === "themes" && <div className="space-y-4">{Object.entries(entry?.tokens ?? {}).map(([key, tokens]) => <div key={key}><h3 className="mb-2 text-sm font-medium">{key}</h3><div className="flex flex-wrap gap-3">{Object.entries(tokens).map(([name, value]) => <div key={name} className="rounded border p-2 text-xs">{key === "colors" && <div className="mb-2 h-10 w-16 rounded border" style={{ background: String(value) }} />}<p>{name}</p><p className="text-slate-500">{String(value)}</p></div>)}</div>{Object.keys(tokens).length === 0 && <p className="text-xs text-slate-500">未提取该类设计语义</p>}</div>)}</div>}
              {category === "assets" && entry && <div className="space-y-3 text-sm">{entry.mimeType?.startsWith("image/") && <img alt={entry.id} src={getApiUrl(detail!.assetUrls[entry.id])} className="max-h-96 max-w-full" />}{entry.mimeType?.startsWith("video/") && <video controls src={getApiUrl(detail!.assetUrls[entry.id])} className="max-h-96 max-w-full" />}<p>{entry.mimeType} · {entry.role}</p><p className="break-all text-xs">SHA-256 {entry.sha256}</p><a className="underline" href={getApiUrl(detail!.assetUrls[entry.id])}>下载原资源</a></div>}
              {!compiled && category !== "themes" && category !== "assets" && <p className="py-5 text-sm text-slate-500">{error ? "该条目无法预览，查看错误与定义。" : "正在编译预览…"}</p>}
            </div>
          </div>
          {editable && <div className="rounded border bg-white p-4"><h2 className="font-medium">{category === "layouts" ? "试填内容 JSON" : "元素树试验：修改内容、位置、组合或样式引用"}</h2><p className="my-2 text-xs text-slate-500">试验不会覆盖当前版本；可保存为新的草稿版本。</p><textarea aria-label="试验 JSON" className="h-60 w-full rounded border p-3 font-mono text-xs" value={input} onChange={event => setInput(event.target.value)} /><div className="mt-2 flex flex-wrap gap-2"><button className={button} disabled={busy || !compiled} onClick={() => void run(compileInput)}>编译试验</button><button className={button} disabled={busy || !compiled} onClick={() => void run(async () => { await compileInput(); const source = structuredClone(pack); if (category === "layouts") { const layout = source.layouts.find(item => item.id === selectedId)!; const content = JSON.parse(input); for (const [key, binding] of Object.entries(layout.bindings ?? {})) { const node = nodeEntries(layout.root).find(item => item.node.id === binding.nodeId)?.node; const [section, field] = binding.property.split("."); if (node && key in content) { const record = node as unknown as Record<string, Json>; record[section][field] = content[key]; } } } else { const ref = entry?.compositionRef ?? selectedId; source.compositions.find(item => item.id === ref)!.root = JSON.parse(input); } await saveVersion(source); })}>试验另存为新版本</button></div></div>}
          {allNodes.length > 0 && <div className="rounded border bg-white p-4"><h2 className="mb-2 font-medium">解析元素 · {allNodes.length}</h2><div className="max-h-48 overflow-auto">{allNodes.map(({ node }) => <button key={node.id} className={`mb-1 mr-1 rounded border px-2 py-1 text-xs ${selectedNode === node.id ? "bg-blue-100" : ""}`} onClick={() => setSelectedNode(node.id)}>{node.type} · {node.id}</button>)}</div><p className="mt-2 break-all font-mono text-xs">{selectedNode && `${selectedNode} → ${compiled?.nodePaths[selectedNode]}`}</p></div>}
        </section>
        <aside className="min-w-0 rounded border bg-white p-3"><h2 className="mb-3 font-medium">定义与解析结果</h2><div className="mb-3 flex flex-wrap gap-1">{["定义", "解析树", "Native UI", "Schema / 绑定", "完整包"].map(label => <button key={label} className={`rounded border px-2 py-1 text-xs ${jsonTab === label ? "bg-slate-900 text-white" : ""}`} onClick={() => setJsonTab(label)}>{label}</button>)}</div><pre data-testid="definition-json" className="max-h-[850px] overflow-auto whitespace-pre-wrap break-all bg-slate-50 p-3 text-[11px]">{format(selectedJson ?? {})}</pre><details className="mt-4"><summary className="cursor-pointer text-sm">编辑完整包并另存草稿</summary><textarea aria-label="完整包草稿" className="mt-3 h-60 w-full rounded border p-2 font-mono text-xs" value={rawDraft} onChange={event => setRawDraft(event.target.value)} /><button className={button} disabled={busy} onClick={() => void run(() => saveVersion(JSON.parse(rawDraft)))}>保存新版本</button></details></aside>
      </div>
      <section className="mx-4 mb-6 rounded border bg-white p-4"><h2 className="font-medium">检验报告</h2><div className="my-3 flex flex-wrap gap-2">{Object.entries(detail!.report.checks).map(([key, value]) => <span key={key} className="rounded border px-3 py-1 text-xs">{key}: {value}</span>)}</div><p className="mb-3 text-xs text-slate-500">{String(pack.provenance.filename)} · {String(pack.provenance.slideCount)} 页 · {String(pack.provenance.shapeCount)} 个源形状。报告绑定当前版本与资源哈希。</p><div className="max-h-72 overflow-auto">{detail!.report.findings.length === 0 ? <p className="text-sm">结构检查无诊断；视觉质量仍需逐项检查。</p> : detail!.report.findings.map((finding, index) => <button key={index} className="block w-full border-b py-2 text-left text-xs" onClick={() => { if (finding.sourceSlide) { diagnosticSelection.current = { layoutId: `slide_${finding.sourceSlide}`, nodeId: finding.nodeId ?? "" }; setCategory("layouts"); setSelectedId(`slide_${finding.sourceSlide}`); } setSelectedNode(finding.nodeId ?? ""); }}><span className={finding.severity === "error" ? "text-red-700" : "text-amber-700"}>{finding.severity} · {finding.code}</span> · {finding.nodeId ?? finding.assetId} <span className="text-slate-500">{finding.message}</span></button>)}</div></section>
    </>}
  </main>;
}
