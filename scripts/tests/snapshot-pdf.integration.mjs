// Run with the installed renderer, e.g. inside the built image:
// PRESENTON_RENDER_MODULE=/app/presentation-export/snapshot-export.mjs node --test <this file>
import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import {pathToFileURL} from "node:url";

test("snapshot PDF has one fixed-size page per saved slide", async () => {
  const module = process.env.PRESENTON_RENDER_MODULE;
  assert.ok(module, "Select the installed render module explicitly");
  const {renderSnapshot} = await import(pathToFileURL(module).href);
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "snapshot-pdf-pages-"));
  try {
    const snapshot = {title: "Three native pages", revision: 1, slides: [0, 1, 2].map(index => ({index,
      ui: {id: "slide-" + index, description: "PDF pagination fixture", components: [{id: "content", description: "Title",
        position: {x: 80, y: 80}, elements: [{type: "text", text: "Page " + (index + 1),
          position: {x: 0, y: 0}, size: {width: 1000, height: 100},
          name: "title", min_length: 0, max_length: 100, decorative: false,
          runs: [{text: "Page " + (index + 1), font: {family: "Noto Sans", size: 36, color: "#111827", bold: true}}],
          font: {family: "Noto Sans", size: 36, color: "#111827", bold: true, line_height: 1.25, wrap: "word"},
          alignment: {horizontal: "left", vertical: "top"}}]}]}}))};
    const result = await renderSnapshot({snapshot, outputDirectory: directory, formats: ["pdf"]}, {
      browserLaunchOptions: process.env.PUPPETEER_EXECUTABLE_PATH ? {executablePath: process.env.PUPPETEER_EXECUTABLE_PATH} : {}});
    assert.equal(result.formats?.pdf, "completed", JSON.stringify(result));
    const pdf = await fs.readFile(path.join(directory, "presentation.pdf"));
    assert.ok(pdf.subarray(0, 5).equals(Buffer.from("%PDF-")));
    assert.equal([...pdf.toString("latin1").matchAll(/\/Type\s*\/Page\b/g)].length, 3);
    assert.equal((await fs.readdir(directory)).filter(file => /^preview-\d+\.png$/.test(file)).length, 3);
  } finally { await fs.rm(directory, {recursive: true, force: true}); }
});
