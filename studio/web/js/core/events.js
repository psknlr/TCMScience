// A tiny event emitter. A listener that throws is reported and does not stop the others.

export class Emitter {
  #listeners = new Map();

  /** Subscribe; returns the function that unsubscribes. `type` "*" receives every event as (type, payload). */
  on(type, fn) {
    if (typeof fn !== "function") throw new TypeError("Emitter.on: listener must be a function");
    let set = this.#listeners.get(type);
    if (!set) this.#listeners.set(type, (set = new Set()));
    set.add(fn);
    return () => this.off(type, fn);
  }

  once(type, fn) {
    const off = this.on(type, (...args) => { off(); fn(...args); });
    return off;
  }

  off(type, fn) {
    const set = this.#listeners.get(type);
    if (!set) return;
    set.delete(fn);
    if (!set.size) this.#listeners.delete(type);
  }

  emit(type, payload) {
    for (const fn of [...(this.#listeners.get(type) || [])]) call(fn, payload);
    if (type !== "*") for (const fn of [...(this.#listeners.get("*") || [])]) call(fn, type, payload);
  }

  listenerCount(type) {
    return this.#listeners.get(type)?.size || 0;
  }

  clear() {
    this.#listeners.clear();
  }
}

function call(fn, ...args) {
  try {
    fn(...args);
  } catch (err) {
    // one broken subscriber (a UI panel) must not break the agent loop or the store
    (globalThis.reportError || console.error)(err);
  }
}
