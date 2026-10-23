import { BlobSink, applyFiles, buildCia, buildImage, checkSource, loadPatch, openRom } from "./dq11patch.js";

const PATCH_URL = "patch/DQXI_EN.dq11patch";
const $ = (id) => document.getElementById(id);

let patch = null, rom = null, romName = "", files = null, image = null, busy = false;

function status(text, cls = "") {
  $("status").textContent = text;
  $("status").className = cls;
}

function progress(stage, done, total) {
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

// The patched .3ds as a Blob (mostly slices of the player's file, so keeping it is cheap);
// the .cia is made from it, so saving both builds the game only once.
async function patchedImage() {
  if (!image) {
    if (!files) files = await applyFiles(patch, rom, progress);
    const sink = new BlobSink();
    await buildImage(rom, files, sink, patch.header.keep_update_partitions, progress);
    image = sink.blob;
  }
  return image;
}

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

async function run(job) {
  busy = true; updateButtons();
  try {
    await job();
  } catch (e) {
    status(`✗ ${e.message}`, "err");
    console.error(e);
  } finally {
    busy = false; updateButtons();
  }
}

$("make-3ds").onclick = () => run(async () => {
  const name = outName(".3ds"), t0 = performance.now();
  download(await patchedImage(), name);
  status(`✓ ${name} is ready (${Math.round((performance.now() - t0) / 1000)} s). Your browser saves it like any download.`, "ok");
});

$("make-cia").onclick = () => run(async () => {
  const name = outName(".cia"), t0 = performance.now();
  const parts = await buildCia(await patchedImage(), progress);
  download(new Blob(parts, { type: "application/octet-stream" }), name);
  status(`✓ ${name} is ready (${Math.round((performance.now() - t0) / 1000)} s). Install it with FBI.`, "ok");
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

fetch(PATCH_URL).then((r) => {
  if (!r.ok) throw new Error(`could not load the patch (${r.status})`);
  return usePatch(r, "Current patch");
}).catch((e) => { $("patch-info").innerHTML = `<span class="err">✗ ${e.message}</span>`; });
