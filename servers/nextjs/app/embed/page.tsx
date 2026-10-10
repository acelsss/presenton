import { Suspense } from "react";
import HostEditor from "./HostEditor";

// This shell contains no manuscript data or credentials. All data requests go
// through the host's authenticated, document-scoped editor session.
export default async function EmbedPage({ searchParams }: {
  searchParams: Promise<{ id?: string; hostBase?: string }>;
}) {
  const params = await searchParams;
  if (!params.id || !/^[a-f0-9-]{36}$/.test(params.id) || !params.hostBase) return <p>Invalid editor session</p>;
  return <Suspense fallback={<p>正在打开演示文稿…</p>}><HostEditor id={params.id} base={params.hostBase} /></Suspense>;
}
