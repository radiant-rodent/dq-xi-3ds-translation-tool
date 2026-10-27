// Applies a .dq11patch (dq11tool/patchfile.py) to a decrypted Dragon Quest XI .3ds, entirely in
// the browser (or Node 20+ for tests). The rebuild mirrors dq11tool/ctr/build.py byte for byte.
//
//   const patch = await loadPatch(blobOrResponse);
//   const rom = await openRom(file);            // throws a readable Error when it does not fit
//   checkSource(patch, rom);
//   const files = await applyFiles(patch, rom, progress);
//   const sink = new BlobSink();
//   await buildImage(rom, files, sink, patch.header.keep_update_partitions, progress);
//   download(new Blob(sink.parts))             // or buildCia(sink, progress)
//
// A sink is {write(position, part), close(), byRef}: a part is a Uint8Array or, where it has
// not been read anyway or byRef is set, a Blob (a slice of the ROM file, at most CHUNK bytes).
// Most of the image is unchanged ROM data, so BlobSink (byRef) keeps only references to it and
// the result costs ~100 MB of memory. progress(stage, done, total).
//
// The built image is read through its parts (BlobSink.slice), never as one Blob made of them:
// Chrome on Android failed to read such a Blob (NotReadableError) when the ROM came from the
// system file picker, although it read slices of the file itself fine.

const MEDIA_UNIT = 0x200;
const BLOCK = 0x1000;
const CHUNK = 8 << 20;

const align = (x, a) => Math.ceil(x / a) * a;
const hex = (buf) => Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
const sha256 = async (u8) => new Uint8Array(await crypto.subtle.digest("SHA-256", u8));
const dv = (u8) => new DataView(u8.buffer, u8.byteOffset, u8.byteLength);

function concat(parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

// ------------------------------------------------------------------------------- patch file

export async function loadPatch(source) {
  const blob = source instanceof Blob ? source : await source.blob();
  const raw = new Uint8Array(await new Response(blob.stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer());
  if (new TextDecoder().decode(raw.subarray(0, 8)) !== "DQ11PTCH") throw new Error("This is not a .dq11patch file.");
  const hlen = dv(raw).getUint32(8, true);
  const header = JSON.parse(new TextDecoder().decode(raw.subarray(12, 12 + hlen)));
  if (header.format !== 1) throw new Error(`Unsupported patch format ${header.format}; reload the page.`);
  return { header, data: raw.subarray(12 + hlen) };
}

export function applyDelta(src, ops, size) {
  const out = new Uint8Array(size);
  let pos = 0, o = 0, prevEnd = 0;
  const varint = () => {
    let v = 0, mul = 1, b;
    do { b = ops[pos++]; v += (b & 0x7f) * mul; mul *= 128; } while (b >= 0x80);
    return v;
  };
  while (o < size) {
    const v = varint(), len = Math.floor(v / 2);
    if (v % 2) {
      out.set(ops.subarray(pos, pos + len), o);
      pos += len;
    } else {
      const z = varint();
      const start = prevEnd + (z % 2 ? -(z + 1) / 2 : z / 2);
      if (start < 0 || start + len > src.length) throw new Error("corrupt patch (copy out of range)");
      out.set(src.subarray(start, start + len), o);
      prevEnd = start + len;
    }
    o += len;
  }
  if (o !== size || pos !== ops.length) throw new Error("corrupt patch (length mismatch)");
  return out;
}

// --------------------------------------------------------------------------------- BLZ codec
// Port of dq11tool/native/blz.c: the output must match it exactly (the patch checks SHA-256).

export function blzDecompress(inp) {
  const n = inp.length;
  if (n < 8) throw new Error("BLZ: too short");
  const d = dv(inp), tb = d.getUint32(n - 8, true), extra = d.getUint32(n - 4, true);
  const top = tb & 0xffffff, bottom = tb >>> 24;
  if (bottom < 8 || bottom > 11 || top < bottom || top > n) throw new Error("BLZ: bad footer");
  const outSize = n + extra, out = new Uint8Array(outSize);
  out.set(inp);
  let dst = outSize, src = n - bottom;
  const end = n - top;
  while (src > end) {
    const flag = out[--src];
    for (let i = 0; i < 8; i++) {
      if (!((flag << i) & 0x80)) {
        if (dst - end < 1 || src - end < 1) throw new Error("BLZ: corrupt");
        out[--dst] = out[--src];
      } else {
        if (src - end < 2) throw new Error("BLZ: corrupt");
        const hi = out[--src], lo = out[--src], tok = (hi << 8) | lo;
        const off = (tok & 0xfff) + 3, len = ((tok >> 12) & 0xf) + 3;
        if (dst - end < len || dst + off > outSize) throw new Error("BLZ: corrupt");
        for (let j = 0; j < len; j++) { out[dst - 1] = out[dst - 1 + off]; dst--; }
      }
      if (src <= end) break;
    }
  }
  return out;
}

export function blzCompress(inp) {
  const inSize = inp.length;
  if (inSize <= 8) return null;
  const HBITS = 16, MAX_LEN = 0xf + 3, MAX_DIST = 0xfff + 3;
  const head = new Int32Array(1 << HBITS).fill(-1);
  const prev = new Int32Array(inSize);
  const tmp = new Uint8Array(inSize + 16);
  const hash3 = (q) => Math.imul((inp[q] << 8) ^ (inp[q + 1] << 4) ^ inp[q + 2], 0x9e3779b1) >>> (32 - HBITS);
  let p = inSize, d = inSize, ok = true, inserted = inSize;
  while (p > 0 && ok) {
    const flagpos = --d;
    if (d < 0) { ok = false; break; }
    tmp[flagpos] = 0;
    for (let i = 0; i < 8; i++) {
      while (inserted > p) {
        inserted--;
        if (inserted + 3 <= inSize) {
          const h = hash3(inserted);
          prev[inserted] = head[h];
          head[h] = inserted;
        }
      }
      let bestLen = 0, bestDist = 0;
      const maxlen = Math.min(MAX_LEN, p);
      if (maxlen >= 3) {
        const t = p - 3;
        let s = head[hash3(t)], chain = 256;
        while (s >= 0 && chain-- > 0) {
          const dist = s - t;
          if (dist > MAX_DIST) break;
          if (dist >= 3) {
            let l = 0;
            while (l < maxlen && p - 1 - l + dist < inSize && inp[p - 1 - l] === inp[p - 1 - l + dist]) l++;
            if (l > bestLen) { bestLen = l; bestDist = dist; if (l === maxlen) break; }
          }
          s = prev[s];
        }
      }
      if (bestLen >= 3) {
        if (d < 2) { ok = false; break; }
        tmp[flagpos] |= 0x80 >> i;
        const tok = ((bestLen - 3) << 12) | (bestDist - 3);
        tmp[--d] = tok >> 8;
        tmp[--d] = tok & 0xff;
        p -= bestLen;
      } else {
        if (d < 1) { ok = false; break; }
        tmp[--d] = inp[--p];
      }
      if (p <= 0) break;
    }
  }
  if (!ok) return null;

  // largest raw prefix such that in-place decoding never overwrites unread compressed bytes
  const cbuf = tmp.subarray(d, inSize), csize = inSize - d;
  let orig = inSize, cpos = csize, origSafe = 0, compSafe = 0, over = false;
  while (orig > 0) {
    const flag = cbuf[--cpos];
    for (let i = 0; i < 8; i++) {
      if (!((flag << i) & 0x80)) {
        cpos--; orig--;
      } else {
        const len = ((cbuf[--cpos] >> 4) & 0xf) + 3;
        cpos--;
        orig -= len;
        if (orig < cpos) { origSafe = orig; compSafe = cpos; over = true; break; }
      }
      if (orig <= 0) break;
    }
    if (over) break;
  }
  const compLen = csize - compSafe, padOff = origSafe + compLen, footOff = (padOff + 3) & ~3;
  const total = footOff + 8, top = total - origSafe, bottom = total - padOff;
  if (total >= inSize || top > 0xffffff) return null;
  const out = new Uint8Array(total);
  out.set(inp.subarray(0, origSafe));
  out.set(cbuf.subarray(compSafe, compSafe + compLen), origSafe);
  out.fill(0xff, padOff, footOff);
  const od = dv(out);
  od.setUint32(footOff, (top | (bottom << 24)) >>> 0, true);
  od.setUint32(footOff + 4, inSize - total, true);
  return out;
}

// ------------------------------------------------------------------------------- ROM parsing

// RomFS of an NCCH at absolute offset `romfsAt`: file list (metadata order) and level-3 layout.
async function parseRomfs(read, romfsAt) {
  const td = new TextDecoder();
  const ivfc = await read(romfsAt, 0x60), id = dv(ivfc);
  if (td.decode(ivfc.subarray(0, 4)) !== "IVFC" || id.getUint32(4, true) !== 0x10000) throw new Error("Bad RomFS header.");
  const mhs = id.getUint32(8, true);
  const lvl = [0, 1, 2].map((i) => ({
    size: Number(id.getBigUint64(0x0c + i * 0x18 + 8, true)), blockLog2: id.getUint32(0x0c + i * 0x18 + 16, true) }));
  const l3Off = align(0x60 + mhs, 2 ** lvl[2].blockLog2);
  const l3At = romfsAt + l3Off;
  const lh = dv(await read(l3At, 0x28)), w = (i) => lh.getUint32(i * 4, true);
  const [dmtOff, dmtSize, fmtOff, fmtSize, dataOff] = [w(3), w(4), w(7), w(8), w(9)];
  const dmt = await read(l3At + dmtOff, dmtSize), fmt = await read(l3At + fmtOff, fmtSize);
  const dd = dv(dmt), fd = dv(fmt), u16 = new TextDecoder("utf-16le");
  const files = [];
  const nameAt = (buf, view, off) => u16.decode(buf.subarray(off + 4, off + 4 + view.getUint32(off, true)));
  const walk = (off, path) => {
    const base = path.replace(/\/$/, "");
    let f = dd.getUint32(off + 12, true);
    while (f !== 0xffffffff) {
      files.push({ path: `${base}/${nameAt(fmt, fd, f + 0x1c)}`, offset: Number(fd.getBigUint64(f + 8, true)),
                   size: Number(fd.getBigUint64(f + 16, true)), metaOff: fmtOff + f });
      f = fd.getUint32(f + 4, true);
    }
    let c = dd.getUint32(off + 8, true);
    while (c !== 0xffffffff) {
      walk(c, `${base}/${nameAt(dmt, dd, c + 0x14)}`);
      c = dd.getUint32(c + 4, true);
    }
  };
  walk(0, "/");
  return { at: romfsAt, ivfc, l3Off, l3Size: lvl[2].size, dataOff, files,
           fileAt: (rf) => l3At + dataOff + rf.offset };
}

export async function openRom(file) {
  const read = async (off, len) => new Uint8Array(await file.slice(off, off + len).arrayBuffer());
  const ncsdHeader = await read(0, 0x4000);
  const td = new TextDecoder();
  if (td.decode(ncsdHeader.subarray(0x100, 0x104)) !== "NCSD")
    throw new Error("This is not a .3ds cartridge image. Dump the game card as a decrypted .3ds (GodMode9).");
  const nd = dv(ncsdHeader);
  const partitions = [];
  for (let i = 0; i < 8; i++) {
    const off = nd.getUint32(0x120 + i * 8, true), size = nd.getUint32(0x124 + i * 8, true);
    if (size) partitions.push({ index: i, offset: off * MEDIA_UNIT, size: size * MEDIA_UNIT });
  }
  const p0 = partitions.find((p) => p.index === 0);
  if (!p0) throw new Error("The image has no game partition.");
  if (p0.offset + p0.size > file.size) throw new Error("The file is shorter than its header says (incomplete dump or download).");
  const h = await read(p0.offset, 0x200);
  if (td.decode(h.subarray(0x100, 0x104)) !== "NCCH") throw new Error("The game partition is not an NCCH.");
  const hd = dv(h), u32 = (o) => hd.getUint32(o, true);
  const ncch = {
    header: h,
    programId: hd.getBigUint64(0x118, true).toString(16).toUpperCase().padStart(16, "0"),
    productCode: td.decode(h.subarray(0x150, 0x160)).replace(/\0+$/, ""),
    decrypted: !!(h[0x18f] & 0x04),
    exefsOffset: u32(0x1a0) * MEDIA_UNIT, exefsSize: u32(0x1a4) * MEDIA_UNIT,
    exefsHashSize: u32(0x1a8) * MEDIA_UNIT, exheaderSize: u32(0x180),
    romfsOffset: u32(0x1b0) * MEDIA_UNIT, romfsSize: u32(0x1b4) * MEDIA_UNIT,
  };
  const rom = { file, read, ncsdHeader, partitions, p0, ncch };
  if (!ncch.decrypted) return rom;   // checkSource explains; parsing encrypted data is pointless

  const exefsAt = p0.offset + ncch.exefsOffset;
  const eh = await read(exefsAt, 0x200), ed = dv(eh);
  rom.exefs = [];
  for (let i = 0; i < 10; i++) {
    const name = td.decode(eh.subarray(i * 16, i * 16 + 8)).replace(/\0+$/, "");
    if (name) rom.exefs.push({ name, offset: ed.getUint32(i * 16 + 8, true), size: ed.getUint32(i * 16 + 12, true) });
  }
  rom.readExefs = (e) => read(exefsAt + 0x200 + e.offset, e.size);

  rom.romfs = await parseRomfs(read, p0.offset + ncch.romfsOffset);
  rom.readRomfs = (rf) => read(rom.romfs.fileAt(rf), rf.size);
  return rom;
}

export async function sourceId(ncchHeader) {
  return hex(await sha256(concat([ncchHeader.subarray(0, 0x188), ncchHeader.subarray(0x190, 0x200)])));
}

export async function checkSource(patch, rom) {
  const want = patch.header.source;
  if (rom.ncch.programId !== want.title_id)
    throw new Error(`This is a different game (title ID ${rom.ncch.programId}, ${rom.ncch.productCode}); ` +
                    `the patch needs ${want.product_code} (title ID ${want.title_id}).`);
  if (!rom.ncch.decrypted)
    throw new Error("The image is encrypted. Dump it again as a decrypted .3ds, or decrypt it first " +
                    "(GodMode9: select the file, then \"NCSD image options\" → \"Decrypt file\").");
  if (await sourceId(rom.ncch.header) !== want.ncch_sha256)
    throw new Error("This copy of the game is a different version or a modified image; the patch needs " +
                    "the original Japanese cartridge dump.");
}

// --------------------------------------------------------------------------- patched files

// Returns {romfs: Map(path -> bytes), exefs: Map(name -> bytes), code: decompressed code.bin}
export async function applyFiles(patch, rom, progress = () => {}) {
  const byPath = new Map(rom.romfs.files.map((f) => [f.path, f]));
  const out = { romfs: new Map(), exefs: new Map(), code: null };
  const list = patch.header.files;
  for (let i = 0; i < list.length; i++) {
    const e = list[i];
    if (i % 16 === 0) progress("Translating game files", i, list.length);
    let orig;
    if (e.path.startsWith("exefs:")) {
      const x = rom.exefs.find((x) => x.name === e.path.slice(6));
      if (!x) throw new Error(`The image has no ${e.path}.`);
      orig = await rom.readExefs(x);
    } else {
      const rf = byPath.get(e.path);
      if (!rf) throw new Error(`The image has no ${e.path}.`);
      orig = await rom.readRomfs(rf);
    }
    if (orig.length !== e.src_size || hex(await sha256(orig)) !== e.src_sha256)
      throw new Error(`${e.path} in your image differs from the original game (bad dump?).`);
    const src = e.src_dec === "blz" ? blzDecompress(orig) : orig;
    const dec = applyDelta(src, patch.data.subarray(e.off, e.off + e.len), e.dst_dec_size);
    let data = dec;
    if (e.dst_enc === "blz") data = blzCompress(dec);
    else if (e.dst_enc === "paca") data = concat([dec.subarray(0, 8), blzCompress(dec.subarray(8)) || new Uint8Array(0)]);
    if (!data || data.length !== e.dst_size || hex(await sha256(data)) !== e.dst_sha256)
      throw new Error(`Patching ${e.path} gave a wrong result (please report this).`);
    if (e.path.startsWith("exefs:")) {
      out.exefs.set(e.path.slice(6), data);
      if (e.path === "exefs:.code") out.code = dec;
    } else out.romfs.set(e.path, data);
  }
  progress("Translating game files", list.length, list.length);
  return out;
}

// ---------------------------------------------------------------------------- image rebuild

// Collects the image as Blob parts; earlier positions can be overwritten if they lie inside
// one Uint8Array part (the headers, written as placeholders first). Afterwards it serves as the
// image for openRom/buildCia: slice(a, b) reads the parts one by one.
export class BlobSink {
  constructor() { this.parts = []; this.starts = []; this.size = 0; this.byRef = true; }
  write(pos, part) {
    const len = part.length ?? part.size;
    if (pos === this.size) {
      this.parts.push(part); this.starts.push(pos); this.size += len;
      return;
    }
    let i = this.starts.length - 1;
    while (i >= 0 && this.starts[i] > pos) i--;
    const p = this.parts[i], at = pos - this.starts[i];
    if (i < 0 || !(p instanceof Uint8Array) || at + len > p.length) throw new Error("BlobSink: unsupported overwrite");
    p.set(part, at);
  }
  close() {}
  // the parts covering [a, b): subarrays of the Uint8Array parts, slices of the Blob parts
  pieces(a, b) {
    let lo = 0, hi = this.starts.length - 1;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (this.starts[m] <= a) lo = m; else hi = m - 1; }
    const out = [];
    for (let i = lo; i < this.parts.length && this.starts[i] < b; i++) {
      const p = this.parts[i], s = this.starts[i];
      const from = Math.max(a - s, 0), to = Math.min(b - s, p.length ?? p.size);
      if (to > from) out.push(p instanceof Uint8Array ? p.subarray(from, to) : p.slice(from, to));
    }
    return out;
  }
  slice(a, b) {
    const pieces = this.pieces(a, b);
    return { size: b - a, arrayBuffer: async () => {
      const bufs = await Promise.all(pieces.map(async (p) => p instanceof Uint8Array ? p : new Uint8Array(await p.arrayBuffer())));
      return concat(bufs).buffer;
    } };
  }
}

class Writer {
  constructor(sink) { this.sink = sink; this.pos = 0; }
  async write(part) {
    const len = part.length ?? part.size;
    if (len) await this.sink.write(this.pos, part);
    this.pos += len;
  }
  async zeros(n) {
    while (n > 0) { const k = Math.min(n, CHUNK); await this.write(new Uint8Array(k)); n -= k; }
  }
  async copy(rom, off, size) {
    while (size > 0) { const k = Math.min(size, CHUNK); await this.write(rom.file.slice(off, off + k)); off += k; size -= k; }
  }
}

// Collects SHA-256 of every 0x1000 block of what passes through (RomFS level 3). Two buffers:
// one is hashed while the next is filled.
class HashingWriter {
  constructor(w) {
    this.w = w; this.bufs = [new Uint8Array(CHUNK), new Uint8Array(CHUNK)]; this.buf = this.bufs[0];
    this.fill = 0; this.hashes = []; this.pending = Promise.resolve();
  }
  // u8: the bytes; ref: what the sink gets instead (a ROM slice holding the same bytes)
  async write(u8, ref = null) {
    await this.w.write(ref && this.w.sink.byRef ? ref : u8);
    let o = 0;
    while (o < u8.length) {
      const k = Math.min(u8.length - o, CHUNK - this.fill);
      this.buf.set(u8.subarray(o, o + k), this.fill);
      this.fill += k; o += k;
      if (this.fill === CHUNK) await this.flush();
    }
  }
  async zeros(n) {
    while (n > 0) { const k = Math.min(n, CHUNK); await this.write(new Uint8Array(k)); n -= k; }
  }
  async flush() {
    const n = this.fill, jobs = [];
    for (let i = 0; i < n; i += BLOCK) jobs.push(sha256(this.buf.subarray(i, i + BLOCK)));
    const prev = this.pending;
    this.pending = Promise.all([prev, Promise.all(jobs)]).then(([, h]) => { for (const x of h) this.hashes.push(x); });
    await prev;                       // the other buffer is free again
    this.buf = this.buf === this.bufs[0] ? this.bufs[1] : this.bufs[0];
    this.fill = 0;
  }
  async finish() {
    if (this.fill % BLOCK) await this.write(new Uint8Array(BLOCK - this.fill % BLOCK));
    await this.flush();
    await this.pending;
    return concat(this.hashes);
  }
}

// Reads [start, end) of a file front to back as one stream: one open of the file instead of
// one per RomFS file (21000 of them), which is slow for files picked on Android. read(at, len)
// must be called with increasing positions; skipped bytes are discarded.
class SequentialReader {
  constructor(file, start, end) {
    this.pos = start; this.cur = new Uint8Array(0); this.off = 0;
    this.reader = file.slice(start, end).stream().getReader();
  }
  async next() {
    const { value, done } = await this.reader.read();
    if (done) throw new Error("The file ended early (incomplete dump?).");
    this.cur = value; this.off = 0;
  }
  async read(at, len) {
    if (at < this.pos) throw new Error("SequentialReader: backwards read");
    for (let skip = at - this.pos; skip > 0;) {
      if (this.off === this.cur.length) await this.next();
      const k = Math.min(skip, this.cur.length - this.off);
      this.off += k; skip -= k;
    }
    const out = new Uint8Array(len);
    for (let o = 0; o < len;) {
      if (this.off === this.cur.length) await this.next();
      const k = Math.min(len - o, this.cur.length - this.off);
      out.set(this.cur.subarray(this.off, this.off + k), o);
      this.off += k; o += k;
    }
    this.pos = at + len;
    return out;
  }
  cancel() { this.reader.cancel().catch(() => {}); }
}

async function hashLevel(data) {
  const jobs = [];
  for (let i = 0; i < data.length; i += BLOCK) {
    let blk = data.subarray(i, i + BLOCK);
    if (blk.length < BLOCK) { const p = new Uint8Array(BLOCK); p.set(blk); blk = p; }
    jobs.push(sha256(blk));
  }
  return concat(await Promise.all(jobs));
}

async function buildExefs(rom, replace) {
  const header = new Uint8Array(0x200), hv = dv(header), parts = [];
  let len = 0;
  for (let i = 0; i < rom.exefs.length; i++) {
    const e = rom.exefs[i];
    const content = replace.get(e.name) || await rom.readExefs(e);
    header.set(new TextEncoder().encode(e.name), i * 16);
    hv.setUint32(i * 16 + 8, len, true);
    hv.setUint32(i * 16 + 12, content.length, true);
    header.set(await sha256(content), 0x1e0 - i * 0x20);
    const padded = align(content.length + len, MEDIA_UNIT) - len;
    parts.push(content, new Uint8Array(padded - content.length));
    len += padded;
  }
  return concat([header, ...parts]);
}

async function writeRomfs(w, rom, replace, progress) {
  const rfs = rom.romfs, start = w.pos;
  const meta = await rom.read(rfs.at + rfs.l3Off, rfs.dataOff), mv = dv(meta);
  const order = [...rfs.files].sort((a, b) => a.offset - b.offset);
  const layout = [];
  let pos = 0;
  for (const f of order) {
    const nw = replace.get(f.path), size = nw ? nw.length : f.size;
    layout.push({ f, off: pos, size, nw });
    mv.setBigUint64(f.metaOff + 8, BigInt(pos), true);
    mv.setBigUint64(f.metaOff + 16, BigInt(size), true);
    pos = align(pos + size, 0x10);
  }
  const last = layout[layout.length - 1];
  const l3Size = rfs.dataOff + (last ? last.off + last.size : 0);

  await w.write(new Uint8Array(BLOCK));          // IVFC header + master hash, filled in below
  const hw = new HashingWriter(w);
  await hw.write(meta);
  const kept = layout.filter((x) => !x.nw && x.size);
  const reader = rom.file.stream && kept.length
    ? new SequentialReader(rom.file, rfs.fileAt(kept[0].f), rfs.fileAt(kept[kept.length - 1].f) + kept[kept.length - 1].size)
    : { read: rom.read, cancel() {} };
  // the next chunk is read while the current one is written and hashed
  const chunks = (function* () {
    for (const { f, size } of kept)
      for (let p = 0; p < size; p += CHUNK) yield [rfs.fileAt(f) + p, Math.min(size - p, CHUNK)];
  })();
  const fetchNext = () => { const n = chunks.next(); return n.done ? null : reader.read(...n.value); };
  let ahead = fetchNext();
  try {
    let cur = 0;
    for (let i = 0; i < layout.length; i++) {
      const { f, off, size, nw } = layout[i];
      if (off > cur) { await hw.zeros(off - cur); cur = off; }
      if (nw) await hw.write(nw);
      else {
        let p = 0;
        while (p < size) {
          const k = Math.min(size - p, CHUNK), at = rfs.fileAt(f) + p;
          const data = await ahead;
          ahead = fetchNext();
          await hw.write(data, rom.file.slice(at, at + k));
          p += k;
        }
      }
      cur += size;
      if (i % 256 === 0) progress("Writing the translated game", w.pos - start, rfs.l3Size);
    }
  } finally {
    ahead?.catch(() => {});
    reader.cancel();
  }
  const level2 = await hw.finish();
  const level1 = await hashLevel(level2);
  const master = await hashLevel(level1);
  await w.write(level1); await w.zeros(align(level1.length, BLOCK) - level1.length);
  await w.write(level2); await w.zeros(align(level2.length, BLOCK) - level2.length);
  const end = w.pos;

  const l2Log = align(level1.length, BLOCK), l3Log = align(l2Log + level2.length, BLOCK);
  const hdr = rfs.ivfc.slice(), h = dv(hdr);
  hdr.set(new TextEncoder().encode("IVFC"), 0);
  h.setUint32(4, 0x10000, true); h.setUint32(8, master.length, true);
  [[0, level1.length], [l2Log, level2.length], [l3Log, l3Size]].forEach(([lo, sz], i) => {
    h.setBigUint64(0x0c + i * 0x18, BigInt(lo), true);
    h.setBigUint64(0x14 + i * 0x18, BigInt(sz), true);
    h.setUint32(0x1c + i * 0x18, 12, true);
  });
  if (0x60 + master.length > BLOCK) throw new Error("master hash too large");
  const superblock = concat([hdr, master]);
  await w.sink.write(start, superblock);
  return { size: end - start, superblock };
}

export async function buildImage(rom, files, sink, keepUpdatePartitions = true, progress = () => {}) {
  const { ncch, p0, ncsdHeader } = rom;
  const w = new Writer(sink);
  await w.write(ncsdHeader.slice());          // a copy: patched in place below
  const ncchStart = w.pos;
  if (ncchStart !== p0.offset) throw new Error("Unexpected partition layout.");
  await w.write(await rom.read(p0.offset, ncch.exefsOffset));   // NCCH header (patched below) ... plain region
  const exefsBytes = files.exefs.size ? await buildExefs(rom, files.exefs)
                                      : await rom.read(p0.offset + ncch.exefsOffset, ncch.exefsSize);
  await w.write(exefsBytes);
  const exefsSize = align(exefsBytes.length, MEDIA_UNIT);
  await w.zeros(exefsSize - exefsBytes.length);
  const romfsOff = align(ncch.exefsOffset + exefsSize, BLOCK);
  await w.zeros(ncchStart + romfsOff - w.pos);
  const { size: romfsSize, superblock } = await writeRomfs(w, rom, files.romfs, progress);
  const romfsAligned = align(romfsSize, MEDIA_UNIT);
  await w.zeros(romfsAligned - romfsSize);
  const ncchSize = romfsOff + romfsAligned;

  const hdr = ncch.header.slice(), h = dv(hdr);
  h.setUint32(0x104, ncchSize / MEDIA_UNIT, true);
  h.setUint32(0x1a0, ncch.exefsOffset / MEDIA_UNIT, true);
  h.setUint32(0x1a4, exefsSize / MEDIA_UNIT, true);
  hdr.set(await sha256(exefsBytes.subarray(0, ncch.exefsHashSize)), 0x1c0);
  h.setUint32(0x1b0, romfsOff / MEDIA_UNIT, true);
  h.setUint32(0x1b4, romfsAligned / MEDIA_UNIT, true);
  const mhs = dv(superblock).getUint32(8, true);
  const hashUnits = align(0x60 + mhs, MEDIA_UNIT) / MEDIA_UNIT;
  const sb = new Uint8Array(hashUnits * MEDIA_UNIT);
  sb.set(superblock.subarray(0, sb.length));
  h.setUint32(0x1b8, hashUnits, true);
  hdr.set(await sha256(sb), 0x1e0);
  await sink.write(ncchStart, hdr);

  const table = new Map([[0, [p0.offset, ncchSize]]]);
  let pos = ncchStart + ncchSize;
  for (const p of rom.partitions) {
    if (p.index === 0 || (!keepUpdatePartitions && (p.index === 6 || p.index === 7))) continue;
    pos = align(pos, MEDIA_UNIT);
    await w.zeros(pos - w.pos);
    table.set(p.index, [pos, p.size]);
    progress("Copying the other partitions", 0, 1);
    await w.copy(rom, p.offset, p.size);
    pos += p.size;
  }
  const used = pos;

  const nh = ncsdHeader.slice(), n = dv(nh);
  for (let i = 0; i < 8; i++) {
    const [off, size] = table.get(i) || [0, 0];
    n.setUint32(0x120 + i * 8, off / MEDIA_UNIT, true);
    n.setUint32(0x124 + i * 8, size / MEDIA_UNIT, true);
    if (!table.has(i)) n.setBigUint64(0x190 + i * 8, 0n, true);
  }
  if (used > n.getUint32(0x104, true) * MEDIA_UNIT) throw new Error("The rebuilt image exceeds the cartridge capacity.");
  n.setUint32(0x300, used, true);
  await sink.write(0, nh);
  if (w.pos !== used) throw new Error("image size mismatch");
  await sink.close();
  progress("Done", 1, 1);
  return used;
}

// ------------------------------------------------------------------------------------ CIA
// Port of dq11tool/ctr/cia.py: an installable, fakesigned CIA (needs Luma3DS signature patches,
// install with FBI) made from a built .3ds. Certificates and ticket come from a system-update CIA
// inside the image itself.

const K256 = Int32Array.from([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);

// Incremental SHA-256 (WebCrypto only hashes whole buffers; CIA contents are gigabytes).
export class Sha256 {
  constructor() {
    this.h = Int32Array.from([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    this.w = new Int32Array(64); this.buf = new Uint8Array(64); this.fill = 0; this.len = 0;
  }
  blocks(u8, off, end) {
    const w = this.w, h = this.h;
    let h0 = h[0], h1 = h[1], h2 = h[2], h3 = h[3], h4 = h[4], h5 = h[5], h6 = h[6], h7 = h[7];
    for (; off < end; off += 64) {
      for (let i = 0, j = off; i < 16; i++, j += 4) w[i] = (u8[j] << 24) | (u8[j + 1] << 16) | (u8[j + 2] << 8) | u8[j + 3];
      for (let i = 16; i < 64; i++) {
        const a = w[i - 15], b = w[i - 2];
        const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
        const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, hh = h7;
      for (let i = 0; i < 64; i++) {
        const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        const t1 = (hh + S1 + ((e & f) ^ (~e & g)) + K256[i] + w[i]) | 0;
        const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        const t2 = (S0 + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
      h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + hh) | 0;
    }
    h[0] = h0; h[1] = h1; h[2] = h2; h[3] = h3; h[4] = h4; h[5] = h5; h[6] = h6; h[7] = h7;
  }
  update(u8) {
    let o = 0;
    this.len += u8.length;
    if (this.fill) {
      o = Math.min(64 - this.fill, u8.length);
      this.buf.set(u8.subarray(0, o), this.fill);
      this.fill += o;
      if (this.fill < 64) return this;
      this.blocks(this.buf, 0, 64);
      this.fill = 0;
    }
    const full = o + Math.floor((u8.length - o) / 64) * 64;
    this.blocks(u8, o, full);
    this.buf.set(u8.subarray(full), 0);
    this.fill = u8.length - full;
    return this;
  }
  digest() {
    const bits = this.len * 8, pad = new Uint8Array(this.fill < 56 ? 64 - this.fill : 128 - this.fill);
    pad[0] = 0x80;
    const pv = dv(pad);
    pv.setUint32(pad.length - 8, Math.floor(bits / 2 ** 32));
    pv.setUint32(pad.length - 4, bits >>> 0);
    this.update(pad);
    const out = new Uint8Array(32), ov = dv(out);
    for (let i = 0; i < 8; i++) ov.setUint32(i * 4, this.h[i]);
    return out;
  }
}

const CIA_ALIGN = 64, META_SIZE = 0x3ac0;

async function findTemplateCia(img) {
  for (const idx of [7, 6]) {
    const p = img.partitions.find((x) => x.index === idx);
    if (!p) continue;
    const h = dv(await img.read(p.offset, 0x200));
    if (!h.getUint32(0x1b4, true)) continue;          // no RomFS
    const rfs = await parseRomfs(img.read, p.offset + h.getUint32(0x1b0, true) * MEDIA_UNIT);
    for (const f of rfs.files) {
      if (!f.path.toLowerCase().endsWith(".cia") || f.size >= 16 << 20) continue;
      const d = await img.read(rfs.fileAt(f), f.size), v = dv(d);
      if (v.getUint32(0, true) === 0x2020 && v.getUint32(8, true) && v.getUint32(12, true) === 0x350) return d;
    }
  }
  throw new Error("No system CIA found in the update partition to take the certificate chain from.");
}

function ciaTicket(template, titleId) {
  const t = template.slice(), v = dv(t);
  t.fill(0, 4, 0x104);                              // blank signature (fakesigned)
  t.fill(0, 0x1bf, 0x1cf);                          // title key (contents are not encrypted)
  v.setBigUint64(0x1d0, 0x0004000000000000n | (titleId & 0xffffffffn));   // ticket id
  v.setUint32(0x1d8, 0);                            // console id
  v.setBigUint64(0x1dc, titleId);
  v.setUint16(0x1e6, 0);                            // ticket title version
  t[0x1f0] = 0; t[0x1f1] = 0;                       // license type, common key index
  v.setUint32(0x21c, 0);                            // eShop account id
  return t;
}

async function ciaTmd(titleId, saveSize, contents) {
  const hdr = new Uint8Array(0xc4), hv = dv(hdr);
  hdr.set(new TextEncoder().encode("Root-CA00000003-CP0000000b"), 0);
  hdr[0x40] = 1;                                    // version
  hv.setBigUint64(0x4c, titleId);
  hv.setUint32(0x54, 0x40);                         // title type (CTR)
  hv.setUint32(0x5a, saveSize, true);               // save data size (little endian)
  hv.setUint16(0x9e, contents.length);
  const chunks = new Uint8Array(0x30 * contents.length), cv = dv(chunks);
  contents.forEach(({ index, flags, size, digest }, i) => {
    cv.setUint32(i * 0x30, i); cv.setUint16(i * 0x30 + 4, index); cv.setUint16(i * 0x30 + 6, flags);
    cv.setBigUint64(i * 0x30 + 8, BigInt(size)); chunks.set(digest, i * 0x30 + 0x10);
  });
  const info = new Uint8Array(0x24 * 64);
  dv(info).setUint16(2, contents.length);
  info.set(await sha256(chunks), 4);
  hdr.set(await sha256(info), 0xa4);
  const sig = new Uint8Array(4 + 0x100 + 0x3c);
  dv(sig).setUint32(0, 0x00010004);
  return concat([sig, hdr, info, chunks]);
}

// image: the built .3ds (a BlobSink, a Blob, or anything with size/slice); known: the patch's
// "cia" entry (dq11tool/patchfile.py), which spares hashing the game when it matches.
// Returns the CIA as Blob parts.
export async function buildCia(image, progress = () => {}, known = null) {
  const img = await openRom(image);
  const titleId = BigInt("0x" + img.ncch.programId);
  const exheader = img.ncch.exheaderSize ? await img.read(img.p0.offset + 0x200, 0x800) : new Uint8Array(0);
  const saveSize = exheader.length ? Number(dv(exheader).getBigUint64(0x1c0, true)) : 0;
  const iconEntry = img.exefs.find((e) => e.name === "icon");
  const icon = iconEntry ? await img.readExefs(iconEntry) : new Uint8Array(0);

  const tpl = await findTemplateCia(img), tv = dv(tpl);
  const hs = tv.getUint32(0, true), cs = tv.getUint32(8, true), ts = tv.getUint32(12, true);
  const certOff = align(hs, CIA_ALIGN), certs = tpl.slice(certOff, certOff + cs);
  const tikOff = align(certOff + cs, CIA_ALIGN);
  const ticket = ciaTicket(tpl.subarray(tikOff, tikOff + ts), titleId);

  const contents = [{ index: 0, flags: 0x0000, p: img.p0 }];
  const man = img.partitions.find((x) => x.index === 1);
  if (man) contents.push({ index: 1, flags: 0x4000, p: man });   // optional content
  const total = contents.reduce((n, c) => n + c.p.size, 0);
  let done = 0;
  for (const c of contents) {
    c.size = c.p.size;
    if (c.index === 0 && known && known.size === c.size && hex(await sha256(img.ncch.header)) === known.ncch_sha256) {
      c.digest = Uint8Array.from(known.sha256.match(/../g), (x) => parseInt(x, 16));
      done += c.size;
      continue;
    }
    const h = new Sha256();
    for (let o = 0; o < c.p.size; o += CHUNK) {
      const k = Math.min(CHUNK, c.p.size - o);
      h.update(await img.read(c.p.offset + o, k));
      done += k;
      progress("Hashing the CIA contents", done, total);
    }
    c.digest = h.digest();
  }
  const tmd = await ciaTmd(titleId, saveSize, contents);
  const meta = new Uint8Array(META_SIZE);
  if (exheader.length) meta.set(exheader.subarray(0x40, 0x1c0), 0);   // dependency list
  dv(meta).setUint32(0x300, 2, true);                                // core version
  meta.set(icon.subarray(0, 0x36c0), 0x400);

  const header = new Uint8Array(0x2020), hv = dv(header);
  hv.setUint32(0, 0x2020, true);
  hv.setUint32(8, certs.length, true); hv.setUint32(12, ticket.length, true);
  hv.setUint32(16, tmd.length, true); hv.setUint32(20, meta.length, true);
  hv.setBigUint64(24, BigInt(contents.reduce((n, c) => n + align(c.size, CIA_ALIGN), 0)), true);
  for (const { index } of contents) header[0x20 + (index >> 3)] |= 0x80 >> (index % 8);

  const out = [];
  let pos = 0;
  const pad = () => {
    const n = align(pos, CIA_ALIGN) - pos;
    if (n) { out.push(new Uint8Array(n)); pos += n; }
  };
  for (const part of [header, certs, ticket, tmd]) { out.push(part); pos += part.length; pad(); }
  for (const { p } of contents) {
    for (let o = 0; o < p.size; o += CHUNK) {
      const k = Math.min(CHUNK, p.size - o);
      const a = p.offset + o, b = a + k;
      out.push(...(image.pieces ? image.pieces(a, b) : [image.slice(a, b)])); pos += k;
    }
    pad();
  }
  out.push(meta); pos += meta.length; pad();
  progress("Done", 1, 1);
  return out;
}

// ------------------------------------------------------------------------- LayeredFS zip

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();
function crc32(u8) {
  let c = 0xffffffff;
  for (let i = 0; i < u8.length; i++) c = CRC_TABLE[(c ^ u8[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

// Luma3DS game patching folder (luma/titles/<title id>/romfs + code.bin) as a stored zip.
export function layeredFsZip(patch, files) {
  const tid = patch.header.source.title_id;
  const entries = [...files.romfs].map(([p, d]) => [`luma/titles/${tid}/romfs${p}`, d]);
  if (files.code) entries.push([`luma/titles/${tid}/code.bin`, files.code]);
  const parts = [], central = [];
  let off = 0;
  for (const [name, data] of entries) {
    const nb = new TextEncoder().encode(name), crc = crc32(data);
    const lh = new Uint8Array(30), l = dv(lh);
    l.setUint32(0, 0x04034b50, true); l.setUint16(4, 10, true); l.setUint32(14, crc, true);
    l.setUint32(18, data.length, true); l.setUint32(22, data.length, true); l.setUint16(26, nb.length, true);
    const ch = new Uint8Array(46), c = dv(ch);
    c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 10, true); c.setUint32(16, crc, true);
    c.setUint32(20, data.length, true); c.setUint32(24, data.length, true); c.setUint16(28, nb.length, true);
    c.setUint32(42, off, true);
    parts.push(lh, nb, data);
    central.push(ch, nb);
    off += 30 + nb.length + data.length;
  }
  const cdSize = central.reduce((n, p) => n + p.length, 0);
  const eocd = new Uint8Array(22), e = dv(eocd);
  e.setUint32(0, 0x06054b50, true); e.setUint16(8, entries.length, true); e.setUint16(10, entries.length, true);
  e.setUint32(12, cdSize, true); e.setUint32(16, off, true);
  return new Blob([...parts, ...central, eocd], { type: "application/zip" });
}
