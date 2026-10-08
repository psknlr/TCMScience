// Test environment for the web-core modules under Node: Web Storage (Node 22 has none without a flag), and a fixed
// language so string assertions are stable. Import this first in every test file.

class MemoryStorage {
  #m = new Map();
  get length() { return this.#m.size; }
  key(i) { return [...this.#m.keys()][i] ?? null; }
  getItem(k) { return this.#m.has(String(k)) ? this.#m.get(String(k)) : null; }
  setItem(k, v) { this.#m.set(String(k), String(v)); }
  removeItem(k) { this.#m.delete(String(k)); }
  clear() { this.#m.clear(); }
}

for (const name of ["localStorage", "sessionStorage"]) {
  let ok = false;
  try { ok = Boolean(globalThis[name]) && typeof globalThis[name].getItem === "function"; } catch { ok = false; }
  if (!ok) Object.defineProperty(globalThis, name, { value: new MemoryStorage(), configurable: true, writable: true });
}

globalThis.localStorage.setItem("tcmscience.lang", "zh");

export function resetStorage() {
  globalThis.localStorage.clear();
  globalThis.sessionStorage.clear();
  globalThis.localStorage.setItem("tcmscience.lang", "zh");
}
