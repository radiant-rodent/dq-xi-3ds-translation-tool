// node tests/patcher_test.mjs ORIGINAL.3ds PATCH.dq11patch OUT.3ds [REFERENCE.3ds [OUT.cia REFERENCE.cia]]
// Runs the browser patcher (docs/dq11patch.js) under Node and, given the .3ds that
// `python -m dq11tool.patchfile ... --3ds REFERENCE.3ds` built, checks both are identical;
// with OUT.cia, also builds the CIA from OUT.3ds and compares it with REFERENCE.cia
// (dq11tool.ctr.cia.build_cia of REFERENCE.3ds).
import { createHash } from "node:crypto";
import { createReadStream, openAsBlob, statSync } from "node:fs";
import { open } from "node:fs/promises";
import { applyFiles, buildCia, buildImage, checkSource, layeredFsZip, loadPatch, openRom } from "../docs/dq11patch.js";

const [romPath, patchPath, outPath, refPath, ciaPath, refCiaPath] = process.argv.slice(2);
if (!outPath) { console.error("usage: node tests/patcher_test.mjs ORIGINAL.3ds PATCH OUT.3ds [REFERENCE.3ds]"); process.exit(2); }

const t0 = Date.now();
const progress = (stage, i, n) => process.stdout.write(`\r${stage} ${Math.round(100 * i / n)}%   `);
const patch = await loadPatch(await openAsBlob(patchPath));
// a File stand-in: Node's openAsBlob reports size 0 for files of 4 GiB and more
async function fileLike(path) {
  const fh = await open(path, "r");
  return {
    size: statSync(path).size,
    slice: (a, b) => ({ size: b - a, arrayBuffer: async () => {
      const buf = new Uint8Array(b - a);
      const { bytesRead } = await fh.read(buf, 0, buf.length, a);
      return buf.buffer.slice(0, bytesRead);
    } }),
  };
}
const rom = await openRom(await fileLike(romPath));
await checkSource(patch, rom);
const files = await applyFiles(patch, rom, progress);
const zip = layeredFsZip(patch, files);
console.log(`\n${files.romfs.size} RomFS files, LayeredFS zip ${zip.size} bytes`);

const fh = await open(outPath, "w+");
const sink = {
  write: async (pos, part) => {
    const u8 = part instanceof Uint8Array ? part : new Uint8Array(await part.arrayBuffer());
    await fh.write(u8, 0, u8.length, pos);
  },
  close: () => fh.close(),
};
const size = await buildImage(rom, files, sink, patch.header.keep_update_partitions, progress);
console.log(`\nwrote ${size} bytes in ${((Date.now() - t0) / 1000).toFixed(0)} s`);

const digest = (p) => new Promise((res, rej) => {
  const h = createHash("sha256");
  createReadStream(p).on("data", (d) => h.update(d)).on("end", () => res(h.digest("hex"))).on("error", rej);
});
let ok = true;
if (refPath) {
  const [a, b] = await Promise.all([digest(outPath), digest(refPath)]);
  console.log(a === b ? `identical to the reference (${a})` : `DIFFERENT: ${a} vs ${b}`);
  ok = a === b;
}
if (ciaPath) {
  const t1 = Date.now();
  const parts = await buildCia(await fileLike(outPath), progress, patch.header.cia);
  const out = await open(ciaPath, "w");
  for (const p of parts) {
    const u8 = p instanceof Uint8Array ? p : new Uint8Array(await p.arrayBuffer());
    await out.write(u8);
  }
  await out.close();
  console.log(`
wrote ${ciaPath} in ${((Date.now() - t1) / 1000).toFixed(0)} s`);
  if (refCiaPath) {
    const [a, b] = await Promise.all([digest(ciaPath), digest(refCiaPath)]);
    console.log(a === b ? `CIA identical to the reference (${a})` : `CIA DIFFERENT: ${a} vs ${b}`);
    ok &&= a === b;
  }
}
process.exit(ok ? 0 : 1);
