import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createHash} from "node:crypto";
import {snapshotInput, inside} from "../snapshot-input.mjs";
const hash = bytes => createHash("sha256").update(bytes).digest("hex");

test("immutable assets are bounded, scoped and revalidated after same-size tampering", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "ppt-snapshot-test-"));
  try {
    await fs.mkdir(path.join(root, "resources"));
    const bytes = Buffer.from("original-font");
    const digest = hash(bytes);
    const asset = path.join(root, "resources", digest + ".ttf");
    await fs.writeFile(asset, bytes);
    const raw = JSON.stringify({slides: [], assetManifest: {[digest]: {file: digest + ".ttf", mime: "font/ttf", size: bytes.length}}});
    const snapshotPath = path.join(root, "snapshot.json");
    await fs.writeFile(snapshotPath, raw);
    const task = {snapshotPath, snapshotHash: hash(raw)};
    const cache = new Map();
    assert.equal((await snapshotInput(task, root, cache)).assets.size, 1);
    assert.equal((await snapshotInput(task, root, cache)).assets.size, 1);
    await fs.writeFile(asset, Buffer.from("tampered-font"));
    await assert.rejects(snapshotInput(task, root, cache), /asset_changed/);
    await fs.writeFile(snapshotPath, "{}");
    await assert.rejects(snapshotInput(task, root, cache), /snapshot_changed/);
    await assert.rejects(inside(path.join(root, "resources"), snapshotPath), /path_invalid/);
  } finally { await fs.rm(root, {recursive:true, force:true}); }
});
