import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";

export const assetOrigin = "https://ppt-assets.invalid";
const hash = bytes => createHash("sha256").update(bytes).digest("hex");

export async function inside(root, target) {
  const base = await fs.realpath(root);
  const actual = await fs.realpath(target);
  const relative = path.relative(base, actual);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("snapshot_path_invalid");
  return actual;
}

export async function snapshotInput(task, root, cache = new Map()) {
  if (!task.snapshotPath) return {snapshot: task.snapshot, assets: new Map()};
  if (!/^[a-f0-9]{64}$/.test(task.snapshotHash)) throw new Error("snapshot_hash_invalid");
  const file = await inside(root, task.snapshotPath);
  const stat = await fs.stat(file);
  if (stat.size > 4 * 1024 * 1024) throw new Error("snapshot_size_exceeded");
  const raw = await fs.readFile(file);
  if (hash(raw) !== task.snapshotHash) throw new Error("snapshot_changed");
  const snapshot = JSON.parse(raw);
  const assets = new Map();
  let total = 0;
  for (const [digest, entry] of Object.entries(snapshot.assetManifest ?? {})) {
    if (!/^[a-f0-9]{64}$/.test(digest) || !/^[a-z0-9.-]+$/.test(entry.file)
      || !/^(font|image|application)\/[a-z0-9.+-]+$/i.test(entry.mime)) throw new Error("snapshot_asset_invalid");
    total += entry.size;
    if (!Number.isSafeInteger(entry.size) || entry.size < 1 || entry.size > 32 * 1024 * 1024
      || total > 64 * 1024 * 1024) throw new Error("snapshot_asset_budget_exceeded");
    const resource = await inside(root, path.join(root, "resources", entry.file));
    const info = await fs.stat(resource, {bigint: true});
    if (Number(info.size) !== entry.size) throw new Error("snapshot_asset_changed");
    const identity = [info.dev, info.ino, info.size, info.mtimeNs, info.ctimeNs].join(":");
    const cached = cache.get(digest);
    let bytes = cached?.identity === identity ? cached.bytes : null;
    if (!bytes) {
      bytes = await fs.readFile(resource);
      if (hash(bytes) !== digest) throw new Error("snapshot_asset_changed");
      // Bounded per-generation cache; the pinned files remain authoritative.
      if ([...cache.values()].reduce((n, value) => n + value.bytes.length, 0) + bytes.length > 64 * 1024 * 1024) cache.clear();
      cache.set(digest, {bytes, identity});
    }
    assets.set(assetOrigin + "/" + digest, {bytes, mime: entry.mime});
  }
  return {snapshot, assets};
}
