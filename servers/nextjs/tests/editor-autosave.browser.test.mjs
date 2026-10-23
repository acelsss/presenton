import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { build } from 'esbuild';

// Uses the browser runtime installed with presentation-export; ordinary node
// tests do not require Chromium. Run with PRESENTON_TEST_BROWSER=1.
test('editor coordination in Chromium', { skip: process.env.PRESENTON_TEST_BROWSER !== '1' }, async t => {
  const { default: puppeteer } = await import('../../../presentation-export/node_modules/puppeteer/lib/puppeteer/puppeteer.js');
  const mocks = {
    'next/navigation': 'export const useRouter=()=>({push(){}}), usePathname=()=>"/presentation";',
    '@/utils/mixpanel': 'export const trackEvent=()=>{}, trackEventImmediately=async()=>{}, MixpanelEvent={};',
    '@/components/ui/sonner': 'export const notify={error:(...args)=>window.editor.notify(...args)};',
    '../../services/api/presentation-generation': 'export const PresentationGenerationApi={updatePresentationSlide:(...args)=>window.editor.savePage(...args),updatePresentationContent:(...args)=>window.editor.saveDocument(...args)};',
    '@/components/MarkDownRender': 'export default ({content})=>content;',
    './NewSlide': 'export default ()=>null;',
    './SlideThumbnailCard': 'export const SlideThumbnailCard=()=>null;',
    './SortableSlide': 'import React from "react"; export const SortableSlide=()=>React.createElement("div",{"data-slide-sortable":true});',
    '../../components/PresentationRender': 'import React from "react"; export default ({isEditMode})=>React.createElement("div",{"data-canvas-editable":String(isEditMode)});',
  };
  const bundle = await build({
    entryPoints: ['tests/fixtures/editor-autosave.tsx'], bundle: true, write: false,
    format: 'iife', platform: 'browser', jsx: 'automatic', logLevel: 'silent',
    define: { 'process.env.NODE_ENV': '"test"', 'process.env': '{}' },
    plugins: [{ name: 'editor-boundaries', setup(build) {
      build.onResolve({ filter: /.*/ }, args => args.path in mocks ? { path: args.path, namespace: 'mock' } : undefined);
      build.onLoad({ filter: /.*/, namespace: 'mock' }, args => ({ contents: mocks[args.path], resolveDir: path.resolve('.') }));
    } }],
  });
  const server = createServer((req, res) => {
    res.setHeader('content-type', 'text/html');
    res.end('<!doctype html><html><body><div id="root"></div></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await puppeteer.launch({ headless: true, executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined, args: ['--no-sandbox'] });
  t.after(() => browser.close());
  async function open(writable = true, controls = false) {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    assert.deepEqual(errors, []);
    await page.evaluate((writable, controls) => window.editor.mount(writable, controls), writable, controls);
    return page;
  }
  async function saved(page, count) {
    await page.waitForFunction(count => window.editor.calls.length === count &&
      !document.querySelector('[data-saving="true"]'), {}, count);
  }

  await t.test('page and document receipts survive a failed-chat pause', async () => {
    const page = await open();
    await page.evaluate(() => window.editor.editPage('first'));
    await saved(page, 1);
    await page.evaluate(() => window.editor.pause(false));
    await page.evaluate(() => window.editor.pause(true));
    await page.evaluate(() => window.editor.editPage('second'));
    await saved(page, 2);
    await page.evaluate(() => window.editor.editTitle('renamed'));
    await saved(page, 3);
    await page.evaluate(() => window.editor.pause(false));
    await page.evaluate(() => window.editor.pause(true));
    await page.evaluate(() => window.editor.editTitle('renamed again'));
    await saved(page, 4);
    const result = await page.evaluate(() => ({ calls: window.editor.calls, notices: window.editor.notices }));
    assert.deepEqual(result.calls.map(call => [call.kind, call.expected, call.actual]), [
      ['page', 1, 1], ['page', 2, 2], ['document', 3, 3], ['document', 4, 4],
    ]);
    assert.equal(result.notices.length, 0);
    await page.close();
  });

  await t.test('server hydration updates versions and a mere pause cannot clear conflicts', async () => {
    const page = await open();
    await page.evaluate(() => { window.editor.server.pageRevision = 3; });
    await page.evaluate(() => window.editor.editPage('stale'));
    await saved(page, 1);
    await page.evaluate(() => window.editor.pause(false));
    await page.evaluate(() => window.editor.pause(true));
    await page.evaluate(() => window.editor.editPage('still stale'));
    await new Promise(resolve => setTimeout(resolve, 80));
    assert.equal(await page.evaluate(() => window.editor.calls.length), 1);
    await page.evaluate(() => window.editor.pause(false));
    await page.evaluate(() => window.editor.hydrate(7, 5));
    await page.evaluate(() => window.editor.pause(true));
    await page.evaluate(() => window.editor.editPage('fresh'));
    await saved(page, 2);
    assert.deepEqual(await page.evaluate(() => window.editor.calls[1]), { kind: 'page', expected: 5, actual: 5 });
    await page.close();
  });

  await t.test('read-only managed decks disable canvas, title, slide actions and keyboard undo', async () => {
    const page = await open(false, true);
    const before = await page.evaluate(() => ({
      canvas: document.querySelector('[data-canvas-editable]').dataset.canvasEditable,
      titleDisabled: document.querySelector('h2').closest('button').disabled,
      addDisabled: [...document.querySelectorAll('button')].find(button => button.textContent.includes('Add Slides')).disabled,
      sortable: document.querySelectorAll('[data-slide-sortable]').length,
      slideActions: document.querySelectorAll('[aria-label="Slide actions"]').length,
    }));
    assert.deepEqual(before, { canvas: 'false', titleDisabled: true, addDisabled: true, sortable: 0, slideActions: 0 });
    await page.evaluate(() => window.editor.hydrate(1, 1, true));
    assert.equal(await page.evaluate(() => document.querySelectorAll('[data-slide-sortable]').length), 1);
    assert.equal(await page.evaluate(() => document.querySelectorAll('[aria-label="Slide actions"]').length), 1);
    await page.evaluate(() => window.editor.editPage('editable'));
    await saved(page, 1);
    await page.evaluate(() => window.editor.hydrate(2, 2, false));
    await page.keyboard.down('Control');
    await page.keyboard.press('z');
    await page.keyboard.up('Control');
    assert.equal(await page.evaluate(() => window.editor.data().slides[0].content.text), 'editable');
    assert.equal(await page.evaluate(() => window.editor.calls.length), 1);
    await page.close();
  });
});
