// Serves the patched game as an ordinary download (docs/app.js, saveViaWorker): the page keeps
// the parts (Uint8Arrays and Blobs: the image in the page's own storage) and opens dl/<id>/<name>;
// this worker asks the page for that id's parts and streams them as an attachment. Android
// Chrome failed to save multi-GB blob: URLs ("Failed - network error"), while a streamed
// response is downloaded like any other large file. Nothing else is intercepted.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("message", () => {});        // the page pings while a download runs

self.addEventListener("fetch", (e) => {
  const m = new URL(e.request.url).pathname.match(/\/dl\/([0-9a-z]+)\/([^/]+)$/);
  if (m) e.respondWith(serve(m[1], decodeURIComponent(m[2])));
});

async function ask(id) {
  for (const client of await self.clients.matchAll({ type: "window", includeUncontrolled: true })) {
    const job = await new Promise((resolve) => {
      const ch = new MessageChannel();
      ch.port1.onmessage = (e) => resolve(e.data);
      client.postMessage({ type: "download", id }, [ch.port2]);
      setTimeout(() => resolve(null), 3000);
    });
    if (job) return job;
  }
  return null;
}

async function serve(id, name) {
  const job = await ask(id);
  if (!job) return new Response("This download has expired: create the file again on the patcher page.",
                                { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  let i = 0, reader = null;
  const body = new ReadableStream({
    async pull(c) {
      for (;;) {
        if (reader) {
          const { value, done } = await reader.read();
          if (!done) { c.enqueue(value); return; }
          reader = null;
        }
        if (i === job.parts.length) { c.close(); return; }
        const p = job.parts[i++];
        if (p instanceof Blob) reader = p.stream().getReader();
        else if (p.length) { c.enqueue(p); return; }
      }
    },
    cancel() { reader?.cancel(); },
  });
  return new Response(body, { headers: {
    "Content-Type": "application/octet-stream",
    "Content-Length": String(job.size),
    "Content-Disposition": `attachment; filename="${name}"`,
  } });
}
