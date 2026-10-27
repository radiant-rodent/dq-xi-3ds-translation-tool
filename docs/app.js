import { BlobSink, applyFiles, buildCia, buildImage, checkSource, loadPatch, openRom } from "./dq11patch.js";

const PATCH_URL = "patch/DQXI_EN.dq11patch";
const $ = (id) => document.getElementById(id);

let patch = null, rom = null, romName = "", files = null, image = null, busy = false;

function status(text, cls = "") {
  $("status").textContent = text;
  $("status").className = cls;
}

// how long each stage took, shown when a job is done (tells where a slow device spends its time)
let stages = [];
function progress(stage, done, total) {
  const now = performance.now(), last = stages[stages.length - 1];
  if (!last || last.stage !== stage) stages.push({ stage, start: now, end: now });
  else last.end = now;
  const bar = $("bar");
  bar.hidden = false;
  bar.value = total ? done / total : 0;
  status(`${stage}… ${total ? Math.floor(100 * done / total) : 0}%`);
}

function updateButtons() {
  const ready = patch && rom && !busy;
  $("make-3ds").disabled = $("make-cia").disabled = !ready;
}

async function usePatch(source, label) {
  try {
    patch = await loadPatch(source);
    files = image = null;
    const h = patch.header;
    $("patch-info").innerHTML = `<span class="ok">✓</span> ${label}: ${h.name || "patch"} ${h.version || ""} (${h.files.length} files)`;
    if (rom) await checkRom();
  } catch (e) {
    patch = null;
    $("patch-info").innerHTML = `<span class="err">✗ ${e.message}</span>`;
  }
  updateButtons();
}

async function checkRom() {
  const info = $("rom-info");
  try {
    await checkSource(patch, rom);
    info.innerHTML = `<span class="ok">✓</span> ${romName}: ${rom.ncch.productCode}, original Japanese game. Ready.`;
  } catch (e) {
    rom = null;
    info.innerHTML = `<span class="err">✗ ${e.message}</span>`;
  }
  updateButtons();
}

async function useRom(file) {
  rom = null; files = image = null;
  romName = file.name;
  $("rom-info").textContent = `Reading ${file.name}…`;
  updateButtons();
  try {
    rom = await openRom(file);
  } catch (e) {
    $("rom-info").innerHTML = `<span class="err">✗ ${e.message}</span>`;
    return;
  }
  if (!patch) { $("rom-info").textContent = `${file.name}: waiting for the patch to load…`; return; }
  await checkRom();
}

// The patched .3ds: a BlobSink (mostly slices of the player's file, so keeping it is cheap), or
// a file in the page's own storage on Android and wherever the browser cannot read a Blob made
// of such slices. Android Chrome reopens the picked file for every slice it reads, so saving a
// Blob of ~21000 of them never finished ("Failed - network error"); writing the image while
// building costs no extra pass over the game. The .cia is made from it, so saving both builds
// the game only once.
async function patchedImage() {
  if (!image) {
    if (!files) files = await applyFiles(patch, rom, progress);
    const byRef = !/Android/i.test(navigator.userAgent) && await slicesReadable(rom.file);
    const size = Math.max(...rom.partitions.map((p) => p.offset + p.size));   // about the patched size
    const sink = byRef ? new BlobSink() : await StorageSink.create("image.3ds", size);
    try {
      await buildImage(rom, files, sink, patch.header.keep_update_partitions, progress);
    } catch (e) {
      await sink.abort?.();
      throw e;
    }
    image = sink instanceof BlobSink ? sink : await sink.file();
  }
  return image;
}

async function slicesReadable(file) {
  try {
    await new Blob([new Uint8Array(1), file.slice(0, 16)]).slice(1, 17).arrayBuffer();
    return true;
  } catch (e) {
    if (e?.name === "NotReadableError") return false;
    throw e;
  }
}

async function storageDir(size) {
  if (!navigator.storage?.getDirectory) throw new Error("This browser cannot save the file. Try Chrome on a computer.");
  const { quota, usage } = await navigator.storage.estimate();
  if (quota - usage < size)
    throw new Error(`Not enough free space: the phone needs about ${2 * Math.ceil(size / 2 ** 30)} GB free (a temporary copy, then the download).`);
  return navigator.storage.getDirectory();
}

// A sink writing the image to a file in the page's own storage (origin private file system).
class StorageSink {
  static async create(name, size) {
    const handle = await (await storageDir(size)).getFileHandle(name, { create: true });
    return new StorageSink(handle, await handle.createWritable());
  }
  constructor(handle, out) { this.handle = handle; this.out = out; this.byRef = false; }
  async write(position, part) {
    const data = part instanceof Uint8Array ? part : new Uint8Array(await part.arrayBuffer());
    await this.out.write({ type: "write", position, data });
  }
  async close() { await this.out.close(); }
  async abort() { await this.out.abort().catch(() => {}); }
  file() { return this.handle.getFile(); }
}

// Saves parts (Uint8Arrays and slices of the player's file) as one download. Chrome on Android
// may fail to read a Blob built from slices of a picked file; then the parts are written to a
// real file in the page's own storage (origin private file system) and that file is offered.
async function save(parts, name) {
  const blob = new Blob(parts, { type: "application/octet-stream" });
  let at = 0;
  const i = parts.findIndex((p) => { if (p instanceof Uint8Array) { at += p.length; return false; } return true; });
  try {
    if (i >= 0) await blob.slice(at, at + 1).arrayBuffer();
    download(blob, name);
    return;
  } catch (e) {
    if (e?.name !== "NotReadableError") throw e;
  }
  const dir = await storageDir(blob.size);
  const handle = await dir.getFileHandle(name, { create: true });
  const out = await handle.createWritable();
  let done = 0;
  try {
    for (const p of parts) {
      await out.write(p instanceof Uint8Array ? p : new Uint8Array(await p.arrayBuffer()));
      done += p.length ?? p.size;
      progress("Preparing the download", done, blob.size);
    }
    await out.close();
  } catch (e) {
    await out.abort().catch(() => {});
    await dir.removeEntry(name).catch(() => {});
    throw e;
  }
  download(await handle.getFile(), name);
}

// Temporary copies from earlier visits (they cannot be removed while the download reads them).
navigator.storage?.getDirectory?.().then(async (dir) => {
  for await (const name of dir.keys()) if (/\.(3ds|cia)$/i.test(name)) await dir.removeEntry(name).catch(() => {});
}).catch(() => {});

function download(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  // the URL is not revoked: a multi-GB download may still be reading it long after the click
}

function outName(ext) {
  return romName.replace(/\.(3ds|cci)$/i, "") + "_EN" + ext;
}

// Phones stop or throttle a page whose screen turns off or that goes to the background.
let wakeLock = null, wasHidden = false;
async function keepAwake(on) {
  try {
    if (on && !wakeLock && "wakeLock" in navigator) {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.onrelease = () => { wakeLock = null; };
    } else if (!on && wakeLock) await wakeLock.release();
  } catch { /* not supported or refused: the job runs anyway */ }
}
document.addEventListener("visibilitychange", () => {
  if (!busy) return;
  if (document.hidden) wasHidden = true;
  else keepAwake(true);               // the lock is dropped while the page is hidden
});

// NotReadableError: the browser can no longer read the .3ds the player chose.
function lostFile() {
  rom = null; files = image = null;
  $("rom").value = "";
  $("rom-info").innerHTML = `<span class="err">✗ The browser lost access to ${romName}. Choose it again.</span>`;
  status("✗ The browser could no longer read your .3ds file" +
    (wasHidden ? " after this page went to the background" : "") + ". " +
    "On a phone: copy the .3ds to the internal storage (not an SD card or a cloud folder), choose it again, " +
    "and keep this page open with the screen on until the download starts.", "err");
}

function timings() {
  const parts = stages.map((x, i) => [x.stage, ((stages[i + 1]?.start ?? x.end) - x.start) / 1000])
    .filter(([, s]) => s >= 1).map(([stage, s]) => `${stage.toLowerCase()} ${Math.round(s)} s`);
  return parts.length ? ` (${parts.join(", ")})` : "";
}

async function run(job) {
  busy = true; wasHidden = false; stages = []; updateButtons();
  await keepAwake(true);
  try {
    await job();
  } catch (e) {
    if (e?.name === "NotReadableError") lostFile();
    else status(`✗ ${e.message}`, "err");
    console.error(e);
  } finally {
    busy = false; updateButtons();
    keepAwake(false);
  }
}

$("make-3ds").onclick = () => run(async () => {
  const name = outName(".3ds"), t0 = performance.now();
  const img = await patchedImage();
  await save(img instanceof BlobSink ? img.parts : [img], name);
  status(`✓ ${name} is ready in ${Math.round((performance.now() - t0) / 1000)} s${timings()}. Your browser saves it like any download.`, "ok");
});

$("make-cia").onclick = () => run(async () => {
  const name = outName(".cia"), t0 = performance.now();
  await save(await buildCia(await patchedImage(), progress, patch.header.cia), name);
  status(`✓ ${name} is ready in ${Math.round((performance.now() - t0) / 1000)} s${timings()}. Install it with FBI.`, "ok");
});

$("rom").onchange = (e) => e.target.files[0] && useRom(e.target.files[0]);
$("patch-file").onchange = (e) => e.target.files[0] && usePatch(e.target.files[0], e.target.files[0].name);
const drop = $("drop");
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => {
  e.preventDefault(); drop.classList.remove("over");
  if (e.dataTransfer.files[0]) useRom(e.dataTransfer.files[0]);
};

// no-cache: always ask whether the patch changed (a cached old one lacks newer shortcuts)
fetch(PATCH_URL, { cache: "no-cache" }).then((r) => {
  if (!r.ok) throw new Error(`could not load the patch (${r.status})`);
  return usePatch(r, "Current patch");
}).catch((e) => { $("patch-info").innerHTML = `<span class="err">✗ ${e.message}</span>`; });
