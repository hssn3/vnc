// Screenshot a page over the Chrome DevTools Protocol (no puppeteer needed).
// usage: node scripts/cdp_shot.mjs <port> <url> <out.png> [waitMs]
import fs from "node:fs";

const [, , port, url, out, waitMsArg] = process.argv;
const waitMs = Number(waitMsArg || 10000);

const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const page = list.find((t) => t.type === "page");
if (!page) throw new Error("no page target");

const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => {
  ws.onopen = res;
  ws.onerror = rej;
});

let id = 0;
const pending = new Map();
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg);
    pending.delete(msg.id);
  }
};
const send = (method, params = {}) =>
  new Promise((res) => {
    const i = ++id;
    pending.set(i, res);
    ws.send(JSON.stringify({ id: i, method, params }));
  });

await send("Page.enable");
await send("Emulation.setDeviceMetricsOverride", {
  width: 1600, height: 900, deviceScaleFactor: 1, mobile: false,
});
await send("Page.navigate", { url });
await new Promise((r) => setTimeout(r, waitMs));

// optional 6th arg "pin": keep the glass toolbar visible for the screenshot
if (process.argv[6] === "pin") {
  await send("Runtime.evaluate", { expression: "document.body.classList.add('pinned')" });
  await new Promise((r) => setTimeout(r, 700));
}

const state = await send("Runtime.evaluate", {
  expression: `JSON.stringify({
     title: document.title,
     status: (document.getElementById('status')||{}).textContent,
     ready: !!(document.getElementById('stage')||{}).classList && document.getElementById('stage').classList.contains('ready'),
     overlay: (document.getElementById('overlay')||{}).className,
     canvas: (() => { const c = document.querySelector('#screen canvas'); return c ? c.width + 'x' + c.height + ' css:' + c.style.width + 'x' + c.style.height : null; })()
  })`,
  returnByValue: true,
});
console.log("state:", state.result?.result?.value);

const shot = await send("Page.captureScreenshot", { format: "png" });
fs.writeFileSync(out, Buffer.from(shot.result.data, "base64"));
console.log("saved", out);
ws.close();
