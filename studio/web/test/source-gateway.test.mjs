import assert from "node:assert/strict";
import { test } from "node:test";
import { createSourceTransport } from "../js/runtime/source-gateway.js";

test("Python source transport uses only the same-origin worker endpoint", () => {
  let request;
  class XHR {
    status = 200;
    responseText = '{"status":200,"body_base64":"e30=","headers":{}}';
    open(...args) { request = args; }
    setRequestHeader(k, v) { assert.equal(k, "Content-Type"); assert.equal(v, "application/json"); }
    send(data) { assert.equal(data, '{"url":"https://example.org"}'); assert.equal(this.timeout, 45000); }
  }
  const send = createSourceTransport("https://science.impf.ai/", { XMLHttpRequest: XHR });
  assert.equal(JSON.parse(send('{"url":"https://example.org"}', 90000)).status, 200);
  assert.deepEqual(request, ["POST", "https://science.impf.ai/api/sources/request", false]);
});

test("gateway policy errors remain failures for the Python backend", () => {
  class XHR {
    status = 403;
    responseText = '{"error":{"type":"denied","message":"route denied"}}';
    open() {} setRequestHeader() {} send() {}
  }
  const send = createSourceTransport("https://science.impf.ai/", { XMLHttpRequest: XHR });
  assert.equal(JSON.parse(send("{}")).error.message, "route denied");
});

test("missing worker XHR and malformed gateway JSON fail explicitly", () => {
  assert.throws(() => createSourceTransport("https://science.impf.ai/", { XMLHttpRequest: null })("{}"), /no XMLHttpRequest/);
  class XHR {
    status = 502; responseText = '<html>bad gateway</html>';
    open() {} setRequestHeader() {} send() {}
  }
  assert.throws(() => createSourceTransport("https://science.impf.ai/", { XMLHttpRequest: XHR })("{}"), SyntaxError);
});

test("worker XHR deadline keeps a timeout classification", () => {
  class XHR {
    open() {} setRequestHeader() {}
    send() { const error = new Error("request timed out"); error.name = "TimeoutError"; throw error; }
  }
  const send = createSourceTransport("https://science.impf.ai/", { XMLHttpRequest: XHR });
  assert.equal(JSON.parse(send("{}")).error.type, "timeout");
});

test("a gateway refusal carries the gateway's own status and Retry-After to Python", () => {
  class XHR {
    status = 429;
    responseText = '{"error":{"type":"rate_limited","message":"数据库服务查询队列已满，请稍后重试"}}';
    open() {} setRequestHeader() {} send() {}
    getResponseHeader(name) { return name === "Retry-After" ? "10" : null; }
  }
  const out = JSON.parse(createSourceTransport("https://science.impf.ai/", { XMLHttpRequest: XHR })("{}"));
  assert.equal(out.error.type, "rate_limited");
  assert.deepEqual(out.gateway, { status: 429, retry_after_s: 10 });
});

test("an upstream answer relayed by the gateway is not marked as a gateway refusal", () => {
  class XHR {
    status = 200;
    responseText = '{"status":403,"body_base64":"","headers":{}}';
    open() {} setRequestHeader() {} send() {}
    getResponseHeader() { return null; }
  }
  const out = JSON.parse(createSourceTransport("https://science.impf.ai/", { XMLHttpRequest: XHR })("{}"));
  assert.equal(out.status, 403);
  assert.equal(out.gateway, undefined);
});
