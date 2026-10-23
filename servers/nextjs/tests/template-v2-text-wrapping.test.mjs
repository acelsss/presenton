import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { build } from "esbuild";

let textLayout;
let temporaryDirectory;

test.before(async () => {
  temporaryDirectory = await mkdtemp(
    path.join(tmpdir(), "presenton-text-wrapping-"),
  );
  const outputFile = path.join(temporaryDirectory, "template-v2-text.mjs");
  await build({
    entryPoints: [
      path.resolve("components/slide-editor/text/template-v2-text.ts"),
    ],
    outfile: outputFile,
    bundle: true,
    platform: "node",
    format: "esm",
    tsconfig: path.resolve("tsconfig.json"),
    logLevel: "silent",
  });

  globalThis.document = {
    compatMode: "CSS1Compat",
    createElement() {
      return {
        getContext() {
          return {
            font: "",
            measureText(text) {
              return { width: [...text].length * 8.6 };
            },
          };
        },
      };
    },
  };

  textLayout = await import(
    `${pathToFileURL(outputFile).href}?cache=${Date.now()}`
  );
});

test.after(async () => {
  delete globalThis.document;
  if (temporaryDirectory) {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
});

test("keeps CJK punctuation as a break opportunity", () => {
  assert.deepEqual(
    textLayout.splitTextForLayout(
      "PPTAgent、SlideGen、SlideLab、AutoPresent",
    ),
    ["PPTAgent、", "SlideGen、", "SlideLab、", "AutoPresent"],
  );
});

test("does not split a Latin word when the word fits the next line", () => {
  const font = {
    family: "Arial",
    size: 16,
    color: "#111827",
    bold: false,
    italic: false,
    underline: false,
    lineHeight: 1.35,
    letterSpacing: 0,
    opacity: 1,
  };
  const lines = textLayout.layoutRenderTextRuns(
    [{ text: "PPTAgent、SlideGen、SlideLab、AutoPresent", font }],
    280,
    "word",
  );
  assert.deepEqual(
    lines.map((line) => line.map((segment) => segment.text).join("")),
    ["PPTAgent、SlideGen、SlideLab、", "AutoPresent"],
  );
});
