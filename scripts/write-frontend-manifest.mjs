import fs from "node:fs/promises";
import path from "node:path";
import {createHash} from "node:crypto";
const root = process.cwd();
const files = {};
async function walk(base) {
  for (const entry of await fs.readdir(base, {withFileTypes:true})) {
    if (["node_modules", ".next-build", ".git", ".local", ".playwright-cli"].includes(entry.name)) continue;
    const file = path.join(base, entry.name);
    if (entry.isDirectory()) await walk(file);
    else if (entry.isFile()) files[path.relative(root, file).replaceAll(path.sep,"/")] = createHash("sha256").update(await fs.readFile(file)).digest("hex");
  }
}
await walk(root);
await fs.writeFile("/app/frontend-source-manifest.json", JSON.stringify({algorithm:"sha256",files}));
