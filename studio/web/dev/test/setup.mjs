// A DOM for the UI tests (linkedom), with the few browser APIs the UI modules touch at call time.
import { parseHTML } from "linkedom";

const { window, document } = parseHTML("<!doctype html><html lang=\"zh-Hans\"><head></head><body><div id=\"app\"></div><div id=\"toasts\"></div><div id=\"announcer\"></div><div id=\"announcer-assertive\"></div></body></html>");

for (const k of ["window", "document", "Node", "Element", "HTMLElement", "DocumentFragment", "Event", "CustomEvent"]) {
  if (globalThis[k] === undefined) globalThis[k] = k === "window" ? window : k === "document" ? document : window[k];
}
if (typeof globalThis.KeyboardEvent === "undefined") {
  globalThis.KeyboardEvent = class KeyboardEvent extends window.Event {
    constructor(type, init = {}) {
      super(type, init);
      Object.assign(this, { key: init.key || "", code: init.code || "", keyCode: init.keyCode || 0, shiftKey: Boolean(init.shiftKey), altKey: Boolean(init.altKey), ctrlKey: Boolean(init.ctrlKey), metaKey: Boolean(init.metaKey), isComposing: Boolean(init.isComposing) });
    }
  };
}
globalThis.requestAnimationFrame ||= (fn) => setTimeout(() => fn(Date.now()), 0);
globalThis.matchMedia ||= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.matchMedia ||= globalThis.matchMedia;
globalThis.innerWidth ||= 1440;
globalThis.innerHeight ||= 900;
const memory = new Map();
globalThis.localStorage ||= {
  getItem: (k) => (memory.has(k) ? memory.get(k) : null), setItem: (k, v) => memory.set(k, String(v)), removeItem: (k) => memory.delete(k), clear: () => memory.clear(),
};

export { window, document };
