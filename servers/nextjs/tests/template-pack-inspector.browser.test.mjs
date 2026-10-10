import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import test from "node:test";

// Opt-in real server acceptance. Supply an existing authenticated cookie file;
// it is never logged or copied into evidence. No API response is mocked.
test("real template pack inventory, dual-mode trials and immutable readback", {
  skip: !process.env.PACK_INSPECTOR_URL,
  timeout: 240000,
}, async t => {
  const require = createRequire(process.env.PACK_PLAYWRIGHT_PACKAGE);
  const { chromium } = require("@playwright/test");
  const account = JSON.parse(await fs.readFile(process.env.PACK_COOKIE_FILE, "utf8"));
  const initial = JSON.parse(await fs.readFile(process.env.PACK_DETAIL_FILE, "utf8"));
  const output = process.env.PACK_EVIDENCE_DIRECTORY;
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const url = new URL(process.env.PACK_INSPECTOR_URL);
  await context.addCookies(Object.entries(account.cookies).map(([name, value]) => ({ name, value, domain: url.hostname, path: "/", httpOnly: true, sameSite: "Lax" })));
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  await page.goto(url.href, { timeout: 120000 });
  await page.getByTestId("native-preview").waitFor({ timeout: 120000 });
  const inspected = {};
  async function category(name) { await page.getByRole("button", { name, exact: false }).filter({ hasText: name }).first().click(); }
  async function select(id, compiledId = id) {
    await page.locator(`[data-entry-id="${id}"]`).click();
    await page.locator(`[data-testid="native-preview"][data-compiled-id="${compiledId}"][data-preview-entry-id="${id}"]`).waitFor();
    await page.locator("canvas").first().waitFor();
    await page.evaluate(() => document.fonts.ready);
  }
  for (const [key, label] of [["layouts", "固定布局"], ["compositions", "组合"], ["pagePatterns", "页面模式"], ["elementStyles", "基本元素样式"]]) {
    await category(label);
    assert.equal(await page.locator("[data-entry-id]").count(), initial.source[key].length);
    for (const entry of initial.source[key]) {
      await select(entry.id, key === "pagePatterns" ? entry.compositionRef : key === "elementStyles" ? "composed-page" : entry.id);
      assert.deepEqual((await page.getByRole("alert").allTextContents()).filter(text => text.trim()), [], `Failed preview: ${entry.id}`);
      if (key === "layouts") await page.getByTestId("native-preview").screenshot({ path: path.join(output, `native-slide-${entry.sourceSlide}.png`) });
    }
    inspected[key] = initial.source[key].length;
  }
  await category("主题");
  assert.equal(await page.locator("[data-entry-id]").count(), initial.source.themes.length);
  inspected.themes = initial.source.themes.length;
  await category("资源");
  assert.equal(await page.locator("[data-entry-id]").count(), initial.source.assets.length);
  for (const asset of initial.source.assets) {
    await page.locator(`[data-entry-id="${asset.id}"]`).click();
    await page.getByRole("link", { name: "下载原资源" }).waitFor();
  }
  inspected.assets = initial.source.assets.length;

  await category("固定布局"); await select("slide_1");
  const field = page.getByRole("textbox", { name: "试验 JSON", exact: true });
  const content = JSON.parse(await field.inputValue());
  const titleKey = Object.keys(content)[0]; content[titleKey] = "模板填充验收";
  await field.fill(JSON.stringify(content, null, 2));
  const compiledResponse = page.waitForResponse(response => response.url().endsWith("/compile") && response.request().method() === "POST");
  await page.getByRole("button", { name: "编译试验", exact: true }).click();
  const trial = await (await compiledResponse).json();
  assert.ok(JSON.stringify(trial.ui).includes("模板填充验收"));
  await page.getByRole("button", { name: new RegExp(`^text · ${titleKey}$`) }).click();
  await page.getByTestId("node-highlight").waitFor();

  await category("组合"); const combination = initial.source.compositions[0]; await select(combination.id);
  const tree = JSON.parse(await field.inputValue());
  const moved = tree.children.find(node => node.type === "text"); moved.position.x += 40;
  await field.fill(JSON.stringify(tree, null, 2));
  const composeResponse = page.waitForResponse(response => response.url().endsWith("/compile") && response.request().method() === "POST");
  await page.getByRole("button", { name: "编译试验", exact: true }).click();
  const composed = await (await composeResponse).json();
  assert.equal(composed.resolvedRoot.children.find(node => node.id === moved.id).position.x, moved.position.x);
  const saveResponse = page.waitForResponse(response => response.url().endsWith("/versions"));
  await page.getByRole("button", { name: "试验另存为新版本" }).click();
  const saved = await (await saveResponse).json();
  assert.notEqual(saved.id, initial.id);
  await page.getByTestId("native-preview").waitFor();
  await page.reload(); await page.getByTestId("native-preview").waitFor();
  await page.getByRole("combobox", { name: "模板版本" }).selectOption(saved.id);
  await category("组合"); await select(combination.id);
  assert.equal(JSON.parse(await field.inputValue()).children.find(node => node.id === moved.id).position.x, moved.position.x);
  await page.getByRole("combobox", { name: "模板版本" }).selectOption(initial.id);
  await page.getByTestId("native-preview").waitFor();
  await page.screenshot({ path: path.join(output, "inspector-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, "inspector-mobile.png"), fullPage: true });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  assert.deepEqual(pageErrors, []);
  await fs.writeFile(path.join(output, "browser-acceptance.json"), JSON.stringify({ sourceVersionId: initial.id, savedVersionId: saved.id, inspected, templateTrial: "pass", compositionMove: "pass", saveReload: "pass", mobileOverflow: "pass", pageErrors }, null, 2));
});
