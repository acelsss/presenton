// One process belongs to one generation; EOF and parent death close Chromium.
import readline from "node:readline";
import fs from "node:fs/promises";
import path from "node:path";
import puppeteer from "puppeteer";
import { renderSnapshot } from "./snapshot-export.mjs";
import { snapshotInput, inside } from "./snapshot-input.mjs";

// stdout is exclusively the control protocol; renderer diagnostics use stderr.
console.log = (...args) => console.error(...args);

const root = await fs.realpath(process.argv[2]);
const parent = Number(process.argv[3]);
const cache = new Map();
const browser = await puppeteer.launch({headless: true,
  executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
  args: ["--no-sandbox", "--disable-gpu"]});
let closed = false;
async function close() {
  if (closed) return;
  closed = true;
  await browser.close().catch(() => {});
  process.exit(0);
}
process.on("SIGTERM", close);
process.on("SIGINT", close);
const watchdog = setInterval(() => {
  try { process.kill(parent, 0); } catch { void close(); }
}, 1000);
watchdog.unref();
const write = value => process.stdout.write(JSON.stringify(value) + "\n");
write({ready: true, pid: process.pid, browserPid: browser.process()?.pid});
const input = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
for await (const line of input) {
  if (Buffer.byteLength(line) > 64 * 1024) { write({error: {code:"render_request_too_large"}}); continue; }
  let task;
  try {
    task = JSON.parse(line);
    if (task.type === "close") break;
    await inside(root, task.outputDirectory);
    const {snapshot, assets} = await snapshotInput(task, root, cache);
    const result = await renderSnapshot({...task, snapshot}, {
      browser, assets, tempDirectory: process.env.TEMP_DIRECTORY,
      browserLaunchOptions: {executablePath: process.env.PUPPETEER_EXECUTABLE_PATH},
    });
    write({requestId: task.requestId, ...result});
  } catch {
    write({requestId: task?.requestId, error: {code:"snapshot_render_failed"}});
  }
}
await close();
