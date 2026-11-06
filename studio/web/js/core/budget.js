// The context budget of a turn: how much one request to the model may hold, measured before it is sent, and how a
// request over it is made to fit (compaction). Pure: nothing here reads the page, the store or the network.
//
// Measure. A request is measured as it is sent: its UTF-8 bytes exactly, and its tokens estimated as one per CJK code
// point plus one per four other bytes, with a 20% margin. That is an estimate for every tokenizer, never a count.
//
// Budget. min(0.8 × the model's context window − the answer's max tokens, in tokens; 0.9 × the relay's body cap, in
// bytes, for the Tao-S1 relay only). A provider that states no window gets 32 000 tokens.
//
// Compaction. A request over budget gives up, in this order, until it fits: the tool results of earlier turns, this
// turn's older tool results (oldest first), then earlier assistant text. A compacted result keeps its header (status,
// summary, limits, release, the kernel's verdict on each claim, refusals, citations: the text before "Result: " that
// the runner's envelope.compose_text writes) and the Outcome line under it; its body is replaced by a pointer to the
// inspector. The system prompt and the newest step's results are never compacted for a call that may run tools: when
// such a request still does not fit, the agent calls no more tools and asks for the answer (agent.js), and only that
// last request may shorten the newest results too. A request under budget is returned unchanged.

export const DEFAULT_CONTEXT_TOKENS = 32000;
/** The share of the context window a request and its answer may take. */
export const WINDOW_SHARE = 0.8;
/** The share of the relay's body cap a request may take. */
export const BODY_SHARE = 0.9;
/** The estimate's margin over one token per CJK code point and per four other bytes. */
export const MARGIN = 1.2;
/** Characters kept of an earlier assistant text, or of a result that has no envelope header. */
export const KEEP_CHARS = 600;
export const SHORTENED = "[shortened to fit the model's context: full result in the inspector; call the tool again for detail]";
const TEXT_SHORTENED = "… [shortened to fit the model's context; the full text is in the conversation]";
const RESULT_MARK = "\nResult: ";

/** CJK code points of the Basic Multilingual Plane: scripts, punctuation, compatibility and full-width forms, Hangul. */
const cjkBmp = (c) => (c >= 0x2e80 && c <= 0x9fff) || (c >= 0xac00 && c <= 0xd7af) || (c >= 0xf900 && c <= 0xfaff)
  || (c >= 0xfe30 && c <= 0xfe4f) || (c >= 0xff00 && c <= 0xffef);

/** {bytes, units} of a string: its UTF-8 bytes, and token units (1 per CJK code point, 1 per 4 other bytes). */
export function measure(text) {
  const s = typeof text === "string" ? text : String(text ?? "");
  let other = 0; // UTF-8 bytes of everything that is not CJK
  let cjk = 0; // CJK code points
  let cjkBytes = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 0x80) other++;
    else if (c < 0x800) other += 2;
    else if (c >= 0xd800 && c <= 0xdbff) {
      const lo = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
      if (lo >= 0xdc00 && lo <= 0xdfff) {
        i++;
        const cp = 0x10000 + ((c - 0xd800) << 10) + (lo - 0xdc00);
        if (cp >= 0x20000 && cp <= 0x3ffff) { cjk++; cjkBytes += 4; } else other += 4;
      } else other += 3; // a lone surrogate is written as U+FFFD
    } else if (cjkBmp(c)) { cjk++; cjkBytes += 3; }
    else other += 3;
  }
  return { bytes: other + cjkBytes, units: cjk + other / 4 };
}

export const tokensOf = (units) => Math.ceil(units * MARGIN);

/** {bytes, tokens} of a text: what measure() gives, with the token estimate. */
export function estimate(text) {
  const m = measure(text);
  return { bytes: m.bytes, tokens: tokensOf(m.units) };
}

const posNum = (v) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : 0);

/**
 * What one request to this provider may hold: {tokens, bytes}. `provider.contextTokens` is the model's window (32 000
 * when unknown) and `provider.maxTokens` its answer; a window too small for the answer setting still leaves a quarter
 * of it for the request. `bytes` caps the relay's body only (provider.maxBodyBytes); Infinity elsewhere.
 */
export function budgetOf(provider = {}) {
  const window = posNum(provider.contextTokens) || DEFAULT_CONTEXT_TOKENS;
  const tokens = Math.max(Math.floor(WINDOW_SHARE * window - posNum(provider.maxTokens)), Math.floor(window / 4));
  const cap = provider.relay ? posNum(provider.maxBodyBytes) : 0;
  return { tokens, bytes: cap ? Math.floor(BODY_SHARE * cap) : Infinity };
}

// The error a too long request gets: the provider's context_length_exceeded (a 400), the relay's too_large (a 413).
// core/llm/sse.js marks these `compactable`; the types and words are read here too, for a client that does not.
const OVERFLOW_TYPES = new Set(["context_length_exceeded", "too_large", "request_too_large"]);
const OVERFLOW_WORDS = /context[ _-]?length|context window|maximum context|prompt is too long|too many tokens|input is too long/i;

/** Is this model error a request too long for the model or the relay, one a shorter request may get past? */
export function isCompactable(err) {
  if (!err || typeof err !== "object") return false;
  if (err.compactable === true) return true;
  if (OVERFLOW_TYPES.has(String(err.type || ""))) return true;
  if (err.status === 413) return true;
  return err.status === 400 && OVERFLOW_WORDS.test(String(err.message || ""));
}

/**
 * A tool result as a compacted request carries it: the envelope header and the lines under the result (Outcome, the
 * failure budget's notes), the result body replaced by SHORTENED. A text without the header keeps its first
 * KEEP_CHARS characters, its Outcome line and the citation ids of the part left out. Never longer than the input.
 */
export function compactResult(text) {
  if (typeof text !== "string" || text.includes(`${RESULT_MARK}${SHORTENED}`) || text.endsWith(SHORTENED)) return text;
  let out;
  const at = text.indexOf(RESULT_MARK);
  if (at >= 0) {
    // the result is one line of compact JSON (envelope.fit_json); what the agent added under it follows (its Outcome
    // line and notes: a few lines, or only the Outcome line when there is more)
    const rest = text.slice(at + RESULT_MARK.length);
    const nl = rest.indexOf("\n");
    let tail = nl >= 0 ? rest.slice(nl) : "";
    if (tail.length > KEEP_CHARS) tail = tail.split("\n").filter((l) => /^Outcome: /.test(l)).map((l) => `\n${l}`).join("");
    out = `${text.slice(0, at)}${RESULT_MARK}${SHORTENED}${tail}`;
  } else {
    const dropped = text.slice(KEEP_CHARS);
    const outcome = dropped.split("\n").filter((l) => /^Outcome: /.test(l));
    const cites = [...new Set(dropped.match(/\[E\d+\]/g) || [])];
    out = [`${text.slice(0, KEEP_CHARS)}… ${SHORTENED}`, cites.length ? `Citations in the part left out: ${cites.join(" ")}` : "", ...outcome].filter(Boolean).join("\n");
  }
  return out.length < text.length ? out : text;
}

/** An earlier assistant text, shortened to its first KEEP_CHARS characters. */
export function compactText(text) {
  if (typeof text !== "string" || text.length <= KEEP_CHARS + TEXT_SHORTENED.length || text.endsWith(TEXT_SHORTENED)) return text;
  return `${text.slice(0, KEEP_CHARS)}${TEXT_SHORTENED}`;
}

// ------------------------------------------------------------------------------------------------- messages

const isToolResults = (m) => m?.role === "tool" || (m?.role === "user" && Array.isArray(m.content) && m.content.some((b) => b?.type === "tool_result"));
const callsOf = (m) => m?.role === "assistant" && ((Array.isArray(m.tool_calls) && m.tool_calls.length > 0)
  || (Array.isArray(m.content) && m.content.some((b) => b?.type === "tool_use")));

/** A message's tool results compacted: [message, ids of the calls whose result got shorter]. */
function compactResults(m) {
  const ids = [];
  if (m.role === "tool") {
    const content = compactResult(m.content);
    if (content === m.content) return [m, ids];
    ids.push(m.tool_call_id);
    return [{ ...m, content }, ids];
  }
  const content = m.content.map((b) => {
    if (b?.type !== "tool_result") return b;
    if (typeof b.content === "string") {
      const c = compactResult(b.content);
      if (c === b.content) return b;
      ids.push(b.tool_use_id);
      return { ...b, content: c };
    }
    if (!Array.isArray(b.content)) return b;
    let changed = false;
    const inner = b.content.map((x) => {
      if (x?.type !== "text") return x;
      const c = compactResult(x.text);
      if (c === x.text) return x;
      changed = true;
      return { ...x, text: c };
    });
    if (!changed) return b;
    ids.push(b.tool_use_id);
    return { ...b, content: inner };
  });
  return ids.length ? [{ ...m, content }, ids] : [m, ids];
}

/** An assistant message with its text shortened (its tool calls and any thinking left as they are). */
function compactAssistant(m) {
  if (typeof m.content === "string") {
    const c = compactText(m.content);
    return c === m.content ? m : { ...m, content: c };
  }
  if (!Array.isArray(m.content)) return m;
  let changed = false;
  const content = m.content.map((b) => {
    if (b?.type !== "text") return b;
    const c = compactText(b.text);
    if (c === b.text) return b;
    changed = true;
    return { ...b, text: c };
  });
  return changed ? { ...m, content } : m;
}

/** {bytes, units} of one message as it is written into the request's messages array (its comma included). */
function costOf(m) {
  const c = measure(JSON.stringify(m));
  return { bytes: c.bytes + 1, units: c.units + 0.25 };
}

/**
 * Fit one request to `budget` ({tokens, bytes}: budgetOf). `base`: the earlier turns and the question, as provider
 * messages; `turn`: this turn's messages so far; `fixed`: {bytes, units} of the request without these messages (the
 * system prompt, the tools, the parameters: measure() of its JSON); `lastResort`: the request that asks for the answer
 * may shorten the newest step's results too. Returns {messages, bytes, tokens, fits, compacted (ids of the tool calls
 * whose results were shortened), changed}. Under budget, `messages` holds the input messages themselves.
 */
export function fitRequest({ base = [], turn = [], fixed = { bytes: 0, units: 0 }, budget = { tokens: Infinity, bytes: Infinity }, lastResort = false }) {
  const messages = [...base, ...turn];
  const costs = messages.map(costOf);
  let bytes = fixed.bytes + costs.reduce((a, c) => a + c.bytes, 0);
  let units = fixed.units + costs.reduce((a, c) => a + c.units, 0);
  const over = () => bytes > budget.bytes || tokensOf(units) > budget.tokens;
  const compacted = [];
  let changed = false;
  const replace = (i, next) => {
    if (next === messages[i]) return;
    const c = costOf(next);
    bytes += c.bytes - costs[i].bytes;
    units += c.units - costs[i].units;
    messages[i] = next;
    costs[i] = c;
    changed = true;
  };
  if (over()) {
    // the newest step's results: those after this turn's last assistant message that asked for tools
    let newest = messages.length;
    for (let i = messages.length - 1; i >= base.length; i--) if (callsOf(messages[i])) { newest = i + 1; break; }
    let lastAssistant = -1;
    for (let i = messages.length - 1; i >= 0; i--) if (messages[i]?.role === "assistant") { lastAssistant = i; break; }
    const results = (from, to) => {
      for (let i = from; i < to && over(); i++) {
        if (!isToolResults(messages[i])) continue;
        const [next, ids] = compactResults(messages[i]);
        compacted.push(...ids);
        replace(i, next);
      }
    };
    results(0, base.length); // 1. earlier turns' results
    results(base.length, newest); // 2. this turn's older results, oldest first
    for (let i = 0; i < messages.length && over(); i++) { // 3. earlier assistant text
      if (messages[i]?.role === "assistant" && i !== lastAssistant) replace(i, compactAssistant(messages[i]));
    }
    if (lastResort) results(newest, messages.length); // the answer's request: the newest results too
  }
  return { messages, bytes, tokens: tokensOf(units), fits: !over(), compacted, changed };
}
