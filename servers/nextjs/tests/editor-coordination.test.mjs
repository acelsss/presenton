import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

let directory, acknowledgeEditorPage, editorDocumentArguments, editorMetadataChanges;
test.before(async () => {
  directory = await mkdtemp(path.join(tmpdir(), "presenton-editor-"));
  const output = path.join(directory, "editor.mjs");
  await build({
    entryPoints: ["app/(presentation-generator)/presentation/utils/editorCoordination.ts"],
    outfile: output, bundle: true, platform: "node", format: "esm", logLevel: "silent",
  });
  ({ acknowledgeEditorPage, editorDocumentArguments, editorMetadataChanges } = await import(pathToFileURL(output).href));
});
test.after(async () => { if (directory) await rm(directory, { recursive: true, force: true }); });

test("ordinary decks keep the existing request contract", () => {
  assert.deepEqual(editorDocumentArguments(null), {});
});
test("renaming never writes a render-normalized theme back to the manuscript", () => {
  const renderedTheme = { fonts: { textFont: { name: "Fallback" } } };
  const before = JSON.stringify({ title: "Before", theme: renderedTheme });
  assert.deepEqual(editorMetadataChanges({ title: "After", theme: renderedTheme }, before), { title: "After" });
  assert.deepEqual(editorMetadataChanges({ title: "Before", theme: null }, before), { theme: null });
});
test("own saves advance the document and only the acknowledged page", () => {
  const before = { revision: 6, pageRevisions: { p1: 1, p2: 3 }, writable: true };
  const after = acknowledgeEditorPage(before, { revision: 7, previousRevision: 6, pageRevisions: { p1: 2 } });
  assert.deepEqual(after, { revision: 7, pageRevisions: { p1: 2, p2: 3 }, writable: true });
  assert.deepEqual(before.pageRevisions, { p1: 1, p2: 3 });
});
test("saving a separate page never authorizes overwriting an unseen concurrent deck", () => {
  const before = { revision: 6, pageRevisions: { p1: 1, p2: 3 }, writable: true };
  const after = acknowledgeEditorPage(before, { revision: 8, previousRevision: 7, pageRevisions: { p1: 2 } });
  assert.deepEqual(editorDocumentArguments(after), {
    expectedRevision: 6, expectedPageRevisions: { p1: 2, p2: 3 },
  });
});
test("an uncertain save can be acknowledged again without advancing twice", () => {
  const before = { revision: 6, pageRevisions: { p1: 1 }, writable: true };
  const receipt = { previousRevision: 6, revision: 7, pageRevisions: { p1: 2 } };
  const saved = acknowledgeEditorPage(before, receipt);
  assert.deepEqual(acknowledgeEditorPage(saved, receipt), saved);
});
