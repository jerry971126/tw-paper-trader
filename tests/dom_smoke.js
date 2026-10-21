// 極簡假 DOM：執行 docs/index.html 內的 JavaScript，確認不會丟錯，並模擬滑鼠/鍵盤互動。
// 只能抓執行期錯誤與文字內容，看不出排版；排版請實際用瀏覽器開啟檢查。
// 用法：node tests/dom_smoke.js <index.html>
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const m = html.match(/<script id="data" type="application\/json">([\s\S]*?)<\/script>/);
const dataText = m[1].replace(/<\\\//g, "</");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(x => x[1]);
const main = scripts[scripts.length - 1];

class El {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.style = {}; this.listeners = {};
    this.className = ""; this.offsetWidth = 220; this.offsetHeight = 110; this.clientWidth = 800; }
  append(...k) { for (const x of k) { if (x == null) continue; this.children.push(typeof x === "string" ? { text: x } : x); } }
  replaceChildren(...k) { this.children = []; this.append(...k); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  observe() {}
  getBoundingClientRect() { const w = +this.attrs.width || 800, h = +this.attrs.height || 360; return { left: 0, top: 0, width: w, height: h }; }
  set textContent(v) { this.children = [{ text: String(v) }]; }
  get textContent() { return flat(this); }
}
function flat(n) { return n.text !== undefined ? n.text : (n.children || []).map(flat).join(" "); }
const registry = {}, svgs = [];
global.document = {
  getElementById: id => (id === "data" ? { textContent: dataText } : (registry[id] = registry[id] || new El("#" + id))),
  querySelector: s => (registry[s] = registry[s] || new El(s)),
  createElement: t => new El(t),
  createElementNS: (ns, t) => { const e = new El(t); if (t === "svg") svgs.push(e); return e; },
};
global.ResizeObserver = class { observe() {} };
global.requestAnimationFrame = f => f();
global.cancelAnimationFrame = () => {};

let failed = false;
try { new Function(main)(); } catch (e) { failed = true; console.error("JS ERROR:", e.stack); }
if (!failed) {
  const q = s => flat(registry[s] || new El(s));
  console.log("sub    :", q("#sub"));
  console.log("banner :", q("#banner"));
  console.log("tiles  :", q("#tiles").replace(/\s+/g, " ").slice(0, 260));
  const svg = svgs[0];
  if (svg) {
    const texts = svg.children.filter(c => c.tag === "text").map(c => flat(c));
    console.log("svg    : children", svg.children.length, "| axis labels:", texts.join(" "));
    try {
      for (const x of [0, 80, 300, 700, 5000]) (svg.listeners.pointermove || []).forEach(f => f({ clientX: x, clientY: 150 }));
      (svg.listeners.keydown || []).forEach(f => f({ key: "ArrowLeft", preventDefault() {} }));
      (svg.listeners.pointerleave || []).forEach(f => f());
      console.log("tooltip: OK ->", flat(registry["#chart"].children.slice(-1)[0]).replace(/\s+/g, " ").slice(0, 120));
    } catch (e) { failed = true; console.error("INTERACTION ERROR:", e.stack); }
  } else console.log("svg    : (none — 空狀態)");
  console.log("perf   :", q("#perf").replace(/\s+/g, " ").slice(0, 200));
  console.log("pending:", q("#pending").replace(/\s+/g, " ").slice(0, 160));
}
process.exit(failed ? 1 : 0);
