// Synchronous Python I/O stays in the dedicated Worker, never on the UI thread.
// The gateway enforces the generated source registry independently of this client.
export function createSourceTransport(siteUrl, { XMLHttpRequest: XHR = globalThis.XMLHttpRequest } = {}) {
  const endpoint = new URL("api/sources/request", siteUrl).href;
  return (packetJson, timeoutMs = 30000) => {
    if (typeof XHR !== "function") throw new Error("This worker has no XMLHttpRequest source transport");
    const xhr = new XHR();
    xhr.open("POST", endpoint, false);
    xhr.timeout = Math.max(1000, Math.min(45000, Number(timeoutMs) || 30000));
    xhr.setRequestHeader("Content-Type", "application/json");
    try { xhr.send(packetJson); } catch (error) {
      if (error?.name === "TimeoutError") return JSON.stringify({ error: { type: "timeout", message: "source gateway timed out" } });
      throw error;
    }
    if (!xhr.status) throw new Error("Source gateway network request failed");
    if (xhr.responseText.length > 12 * 1024 * 1024) throw new Error("Source gateway response exceeded the browser limit");
    // Error wrappers also reach Python, so its honest UNAVAILABLE/FAILED handling applies.
    const result = JSON.parse(xhr.responseText);
    if (xhr.status >= 400 && !result.error) throw new Error(`Source gateway HTTP ${xhr.status}`);
    return JSON.stringify(result);
  };
}
