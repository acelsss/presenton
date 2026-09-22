// Server-owned immutable snapshots use the existing export-core renderer.
import fs from "node:fs/promises";
import path from "node:path";
import puppeteer from "puppeteer";
import { runTask } from "@presenton/export-core";

const escape = value => String(value).replaceAll("&", "&amp;").replaceAll('"', "&quot;")
  .replaceAll("<", "&lt;").replaceAll(">", "&gt;");

export async function renderSnapshot(task, options) {
  const started = Date.now();
  const browser = await puppeteer.launch({headless: true, ...options.browserLaunchOptions,
    args: ["--no-sandbox", "--disable-gpu"]});
  const out = task.outputDirectory;
  await fs.mkdir(out, {recursive: true});
  // Content/asset snapshot is self-contained. A template cannot initiate network IO.
  const secured = new WeakMap();
  const securePage = async page => {
    if (secured.has(page)) return secured.get(page);
    const ready = (async () => {
      await page.setRequestInterception(true);
      page.on("request", request => /^(data:|about:|blob:)/.test(request.url()) ? request.continue() : request.abort());
    })();
    secured.set(page, ready);
    return ready;
  };
  browser.on("targetcreated", async target => {
    const page = await target.page();
    if (page) await securePage(page);
  });
  const opts = {...options, outputDirectory: out, getBrowser: () => browser};
  const phases = [];
  const timed = async (phase, action, slideIndex = null) => {
    const start = Date.now();
    let status = "success";
    try {
      return await action();
    } catch (error) {
      status = "error";
      const failure = {code: error.name === "TimeoutError" ? "snapshot_render_timeout" : "snapshot_render_failed",
        phase, ...(slideIndex === null ? {} : {slideIndex})};
      throw Object.assign(new Error(failure.code), {snapshotFailure: failure});
    } finally {
      phases.push({phase, slideIndex, status, durationMs: Date.now() - start});
    }
  };
  try {
    const doc = task.snapshot;
    const heads = [], parts = [], renderTimes = [];
    for (const slide of doc.slides) {
      const start = Date.now();
      const html = await timed("slide_html", () => runTask({type: "json-to-html", width: 1280, height: 720, ui: slide.ui}, opts), slide.index);
      const page = await browser.newPage();
      await securePage(page);
      await timed("slide_dom", () => page.setContent(html, {waitUntil: "domcontentloaded"}), slide.index);
      const parsed = await page.evaluate(() => ({head: document.head.innerHTML, body: document.body.innerHTML}));
      await page.close();
      heads.push(parsed.head);
      parts.push(`<section class="main-slide" data-speaker-note="${escape(slide.speakerNote ?? "")}">${parsed.body}</section>`);
      renderTimes.push({index: slide.index, ms: Date.now() - start});
    }
    const theme = doc.theme?.data ?? doc.theme ?? {};
    const colorKeys = {primary: "primary-color", background: "background-color", card: "card-color",
      stroke: "stroke", primary_text: "primary-text", background_text: "background-text"};
    // Theme values enter CSS, not HTML or JavaScript.
    const colors = Object.entries(theme.colors ?? {}).filter(([key, value]) => /^[a-z0-9_]+$/i.test(key) && /^(#[a-f0-9]{3,8}|rgba?\([\d\s.,%/]+\)|hsla?\([\d\s.,%/]+\)|[a-z]+)$/i.test(value));
    const variables = colors.map(([key, value]) => `--${colorKeys[key] ?? key.replaceAll("_", "-")}:${value}`).join(";");
    const fonts = Object.values(theme.fonts ?? {}).filter(font => font?.url?.startsWith("data:"));
    for (const [name, url] of Object.entries(doc.fonts ?? {})) {
      if (typeof url === "string" && url.startsWith("data:")) fonts.push({name, url});
    }
    const family = value => String(value ?? "sans-serif").replaceAll(/[^\p{L}\p{N} _-]/gu, "");
    const fontCss = fonts.map(font => `@font-face{font-family:'${family(font.name)}';src:url('${font.url}')}`).join("\n");
    const font = family(theme.fonts?.textFont?.name ?? "sans-serif");
    const html = `<!doctype html><html><head><meta charset="utf-8">${heads.join("\n")}<style>${fontCss}
      :root{${variables};--heading-font-family:'${font}';--body-font-family:'${font}'}
      html,body{margin:0;padding:0;width:1280px;font-family:'${font}'}
      #presentation-slides-wrapper{width:1280px}
      .main-slide{position:relative;width:1280px;height:720px;overflow:hidden;break-after:page}
      @page{size:1280px 720px;margin:0}
      </style></head><body><div id="presentation-slides-wrapper">${parts.join("\n")}</div></body></html>`;
    await fs.writeFile(path.join(out, "presentation.html"), html);
    const page = await browser.newPage();
    await securePage(page);
    await page.setViewport({width:1280, height:720});
    // Offline data URLs do not reliably emit Chromium's network-idle lifecycle
    // event, even after all bytes are decoded. Wait for actual render inputs.
    await timed("preview_dom", () => page.setContent(html, {waitUntil:"domcontentloaded"}));
    await timed("preview_assets", async () => {
      await page.evaluate(() => {
        window.__snapshotAssetsReady = false;
        window.__snapshotAssetsFailed = false;
        Promise.all([document.fonts.ready, ...[...document.images].map(img => img.decode())])
          .then(() => { window.__snapshotAssetsReady = true; })
          .catch(() => { window.__snapshotAssetsFailed = true; });
      });
      await page.waitForFunction(() => window.__snapshotAssetsReady || window.__snapshotAssetsFailed, {timeout: 30000});
      if (await page.evaluate(() => window.__snapshotAssetsFailed)) throw new Error("snapshot_asset_decode_failed");
    });
    const slides = await page.$$("#presentation-slides-wrapper > section");
    const geometry = [];
    for (let i = 0; i < slides.length; i++) {
      await timed("preview_capture", () => slides[i].screenshot({path: path.join(out, `preview-${i + 1}.png`)}), i);
      geometry.push(await slides[i].evaluate(el => {
        const box = el.getBoundingClientRect();
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        let overflows = 0;
        while(walker.nextNode()) {
          const node = walker.currentNode;
          if (!node.textContent.trim() || ["STYLE", "SCRIPT"].includes(node.parentElement.tagName)) continue;
          const range = document.createRange(); range.selectNodeContents(node);
          const rect = range.getBoundingClientRect();
          if (rect.x < box.x - 2 || rect.right > box.right + 2 || rect.y < box.y - 2 || rect.bottom > box.bottom + 2) overflows++;
        }
        return {overflows, brokenImages: [...el.querySelectorAll("img")].filter(img => !img.complete || !img.naturalWidth).length};
      }));
    }
    await page.close();
    await fs.writeFile(path.join(out, "geometry.json"), JSON.stringify(geometry));
    const formats = {}, errors = {};
    for (const format of task.formats) {
      try {
        const result = await timed(`export_${format}`, () => runTask({type:"html-to-any", html, format, title:doc.title}, opts));
        await fs.copyFile(result.filePath, path.join(out, `presentation.${format}`));
        formats[format] = "completed";
      } catch (error) {
        formats[format] = "error";
        errors[format] = error.snapshotFailure ?? {code: "snapshot_render_failed", phase: `export_${format}`};
      }
    }
    await fs.writeFile(path.join(out, "timing.json"), JSON.stringify({renderTimes, phases, totalMs:Date.now() - started, revision:doc.revision}));
    return {formats, errors, phases};
  } catch (error) {
    return {error: error.snapshotFailure ?? {code: "snapshot_render_failed", phase: "snapshot"}, phases};
  } finally {
    await browser.close();
  }
}
