// Language and strings. The UI registers its own strings (js/ui/strings.js) with registerStrings(); the core modules
// register theirs below. The governance vocabulary is the fixed glossary (glossary.js), never free translation.

import { Emitter } from "./events.js";
import { CATEGORIES, CLAIM_KINDS, DESIGNS, FAMILIES, STATES, claimKindInfo, codeInfo, qualityLabel, tierOf } from "./glossary.js";
import { LANG_KEY, loadSettings, onSettingsChange, updateSettings } from "./settings.js";
import { storage } from "./util.js";

const LANGS = ["zh", "en"];
const dicts = { zh: Object.create(null), en: Object.create(null) };
const bus = new Emitter();
let current = detectLang();

/** The language to start in: ?lang=, then the saved choice, then the browser's language. */
export function detectLang() {
  try {
    const q = new URLSearchParams(globalThis.location?.search || "").get("lang");
    if (LANGS.includes(q)) return q;
  } catch { /* no location */ }
  const saved = storage.get(LANG_KEY);
  if (LANGS.includes(saved)) return saved;
  const s = storage.getJSON("tcmstudio.settings");
  if (s && LANGS.includes(s.lang)) return s.lang;
  const nav = globalThis.navigator?.languages?.[0] || globalThis.navigator?.language || "";
  if (nav) return /^zh\b/i.test(nav) ? "zh" : "en";
  return "zh";
}

export function lang() {
  return current;
}

/** Switch language, persist it (settings.lang and localStorage["tcmscience.lang"]), and tell listeners. */
export function setLang(l) {
  if (!LANGS.includes(l)) throw new RangeError(`unsupported language: ${l}`);
  const changed = l !== current;
  current = l;
  if (loadSettings().lang !== l) updateSettings({ lang: l });
  else storage.set(LANG_KEY, l);
  try { if (globalThis.document?.documentElement) globalThis.document.documentElement.lang = l === "zh" ? "zh-Hans" : "en"; } catch { /* no DOM */ }
  if (changed) bus.emit("change", l);
}

export function onLangChange(fn) {
  return bus.on("change", fn);
}

/** Add or override strings for a language. Later registrations win. */
export function registerStrings(l, dict) {
  if (!LANGS.includes(l)) throw new RangeError(`unsupported language: ${l}`);
  Object.assign(dicts[l], dict || {});
}

/**
 * Look a key up in the current language, then the other one, then return the key itself. `{name}` placeholders are
 * filled from vars; a value that is a function is called with vars (for plurals and numbers).
 */
export function t(key, vars, l = current) {
  let v = dicts[l][key];
  if (v === undefined) v = dicts[l === "zh" ? "en" : "zh"][key];
  if (v === undefined) return key;
  if (typeof v === "function") return String(v(vars || {}));
  if (!vars) return v;
  return v.replace(/\{(\w+)\}/g, (m, name) => (vars[name] === undefined || vars[name] === null ? m : String(vars[name])));
}

const pick = (rec, l) => (rec ? rec[l || current] ?? rec.en : null);

/** zh/en labels for the governance vocabulary, in the current language unless one is given. */
export const glossary = {
  evidenceTier(id, l) {
    const tier = tierOf(id);
    return tier ? pick(tier, l) : String(id ?? "");
  },
  claimKind(id, l) {
    const info = claimKindInfo(id);
    return info ? pick(info, l) : String(id ?? "");
  },
  /** A state of any vocabulary: release states, artifact status, claim/inquiry/goal verdicts, call and job states. */
  verdictState(id, l, group) {
    const key = String(id ?? "");
    const groups = group ? [group] : ["release", "release_summary", "claim", "artifact", "inquiry", "goal", "call", "job", "retraction", "licensing"];
    for (const g of groups) if (STATES[g]?.[key]) return pick(STATES[g][key], l);
    return key;
  },
  /** The short explanation of a refusal or violation code (CLM005 → 证据等级不能支撑该主张类型). */
  code(id, l) {
    return pick(codeInfo(id), l);
  },
  codeRemedy(id, l) {
    const info = codeInfo(id);
    return info.remedy ? pick(info.remedy, l) : null;
  },
  category(id, l) {
    return pick(CATEGORIES.find((c) => c.id === id), l) ?? String(id ?? "");
  },
  design(id, l) {
    return pick(DESIGNS[String(id ?? "").toLowerCase()], l) ?? String(id ?? "");
  },
  family(id, l) {
    return pick(FAMILIES[id], l) ?? String(id ?? "");
  },
  familyShort(id, l) {
    return pick(FAMILIES[id]?.short, l) ?? String(id ?? "");
  },
  quality(dimension, value, l) {
    const q = qualityLabel(dimension, value);
    return q ? pick(q, l) : String(value ?? "");
  },
  claimKinds: () => Object.keys(CLAIM_KINDS),
};

// ------------------------------------------------------------------------------------------------- core strings

registerStrings("zh", {
  "core.project.untitled": "新项目",
  "core.md.copy": "复制",
  "core.runner.label": "本机 Runner",
  "core.browser.label": "浏览器",
  "core.browser.missing": "浏览器内运行环境未载入，Python 工具暂不可用。",
  "core.relay.unreachable": "无法连接 Tao-S1 中继：网络不通或服务暂时不可用。请稍后再试，或在「设置 → 模型」里接入自己的模型 API。",
  "core.relay.off": "Tao-S1 暂未开放：站点的模型密钥尚未配置，或已被管理者暂停。可以在「设置 → 模型」里接入自己的模型 API 或本地模型。",
  // the relay's errors by type (studio/edge/src/relay.js); its own Chinese sentence is shown when it sends one
  "core.relay.error.https_required": "请通过 https 访问 Tao-S1。",
  "core.relay.error.not_found": "Tao-S1 中继不认识这个请求。可以在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.forbidden_origin": "Tao-S1 只供 TCMScience Studio 网页（science.impf.ai）使用。可以在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.not_configured": "Tao-S1 目前没有开放。可以在「设置 → 模型」里接入自己的模型 API 或本地模型。",
  "core.relay.error.bad_request": "Tao-S1 中继无法读取这个请求。请新开一个对话后重试，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.too_large": "对话太长，超过了 Tao-S1 的上限。请新开一个对话，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.model_not_allowed": "这里只提供 Tao-S1。请选用 Tao-S1，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.rate_limited": ({ retry }) => (retry ? `请求太频繁，请 ${retry} 秒后再试。` : "请求太频繁，请稍后再试。"),
  "core.relay.error.daily_limit": "今天的 Tao-S1 免费额度已用完（北京时间每天 8:00，即 UTC 零点重置）。请到时再来，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.total_limit": "Tao-S1 今天的总额度（全体访客共用）已用完（北京时间每天 8:00，即 UTC 零点重置）。请到时再来，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.unavailable": "Tao-S1 暂时不可用，请稍后再试，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.upstream_unreachable": "Tao-S1 暂时连不上模型服务，请稍后再试，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.upstream_auth": "Tao-S1 暂时不可用（模型服务没有接受中继的密钥），请稍后再试，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.upstream_rate": "Tao-S1 的模型服务暂时繁忙，请稍后再试，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.upstream_quota": "Tao-S1 的模型额度暂时用完了，请稍后再试，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.upstream_error": "Tao-S1 的模型服务报告了问题，这次回答没有完成。请稍后再试，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.upstream_rejected": "Tao-S1 无法完成这个请求：模型服务没有接受它。请检查「设置 → 模型」里的温度、最大输出等选项，换一种说法，或新开一个对话后重试。",
  "core.relay.error.relay_error": "Tao-S1 中继遇到问题，请稍后再试，或在「设置 → 模型」里改用自己的模型。",
  "core.relay.error.not_relay": "本机 Runner 不提供 Tao-S1。Tao-S1 只接受 https://science.impf.ai 与 http://127.0.0.1:8765 上的页面：请在默认端口 8765 启动 Runner，或在「设置 → 模型」中使用自己的模型 API 或本地模型。",
  "core.runner.proxy_unreachable": "无法连接本机 Runner（{url}）：请确认 tcmstudio serve 正在运行，然后重试。",
  "core.provider.unreachable": "无法连接 {host}：网络不通，或该服务不允许浏览器直接访问（CORS）。可连接本机 Runner 后改为「经 Runner 转发」。",
  "core.provider.unreachable_local": "无法连接 {label}（{host}）。{fix}",
  "core.provider.bad_url": "模型接口地址无效：{url}",
  "core.provider.no_key": "请先在「设置 → 模型」里为 {label} 填写 API Key。",
  "core.provider.http": "模型服务返回 {status}{hint}：{message}",
  "core.provider.hint.auth": "（API Key 无效或无权限）",
  "core.provider.hint.not_found": "（地址或模型名有误）",
  "core.provider.hint.rate": "（请求过于频繁或额度不足）",
  "core.provider.hint.server": "（服务端暂时不可用）",
  "core.provider.stream_error": "模型服务报错：{message}",
  "core.provider.unparseable": "无法解析模型服务的回复：{text}",
  "core.provider.refusal": "模型拒绝回答这次请求{category}。",
  "core.provider.truncated_tool": "输出达到上限，工具参数不完整，未执行。可在设置中调高「最大输出」后重试。",
  "core.tool.bad_json": "参数不是合法的 JSON：{error}",
  "core.tool.not_object": "参数必须是 JSON 对象",
  "core.agent.stopped": "已停止。已运行的工具结果保留在「过程」中。",
  "core.agent.max_steps": "已达到每轮最多 {n} 次模型调用的上限，回答可能不完整。可在设置中调高上限。",
  "core.agent.retry": "连接中断，正在重试（第 {n} 次）…",
  "core.router.unknown_tool": "没有名为 {name} 的工具。",
  "core.router.human_only": "已拒绝：{name} 只能由人完成，不是工具调用。",
  "core.router.needs_runner": "{name} 需要本机 Runner，当前未连接。浏览器内计算仍可用；连接 Runner 后可运行。",
  "core.router.browser_only_setting": "{name} 只能在本机 Runner 上运行，而计算目标已设为「浏览器」。",
  "core.router.runner_setting_offline": "计算目标已设为「本机 Runner」，但 Runner 未连接。",
  "core.router.missing_deps": "{name} 在本机 Runner 上缺少依赖：{missing}。未安装时不会用近似结果顶替。",
  "core.router.no_runtime": "{name} 现在没有可用的运行环境。",
  "core.router.network_off": "本项目未开启联网，{name} 需要访问网络。可在项目设置中开启联网后重试。",
  "core.router.denied": "未获批准：{name} 没有运行。",
  "core.router.needs_approval": "{name} 需要你的批准才能运行。",
  "core.router.cancelled": "已停止：{name} 没有结果。",
  "core.router.runtime_error": "{name} 运行出错：{message}",
  "core.approval.network": "访问网络：{hosts}",
  "core.approval.job": "在本机 Runner 上启动任务：{what}",
  "core.approval.confirm": "运行需要确认的操作：{what}",
  "core.approval.remote_upload": "把数据发送到第三方服务：{hosts}",
  "core.approval.first_runner_call": "第一次在本机 Runner 上为本项目运行工具",
  "core.local.catalog": "目录检索：{n} 条匹配",
  "core.local.capabilities": "当前可用：浏览器 {browser}，本机 Runner {runner}",
  "core.release.header": "发布状态 {passed}/{total} · {verdict}",
  "core.release.authorized": "已准予发布",
  "core.release.not_authorized": "未准予发布",
  "core.release.unmet.schema_valid": "结构无效",
  "core.release.unmet.evidence_verified": "引文未核验",
  "core.release.unmet.outputs_verified": "输出未核验",
  "core.release.unmet.execution_declared": "未声明执行",
  "core.release.unmet.execution_attested": "执行未证明",
  "core.ladder.caption": "主张类型：{kind} —— 需要 {needs}；本主张最弱证据：{weakest} → {result}",
  "core.ladder.supports": "支撑",
  "core.ladder.does_not": "不支撑",
  "core.ladder.or": " 或 ",
  "core.ladder.and": "、",
  "core.ladder.caption_unlicensed": "主张类型：{kind} —— 需要 {needs}；本主张还引用了 {unlicensed}，不能支撑该类型 → 不支撑",
  "core.ladder.caption_refused": "主张类型：{kind} —— 需要 {needs}；本主张最弱证据：{weakest}，但内核判定所引证据并非都能支撑该类型 → 不支撑",
  "core.runner.not_running": "未检测到本机 Runner（{url}）。请在终端运行 tcmstudio serve，然后点「连接」。",
  "core.runner.lna_denied": "浏览器阻止了本站访问「本机上的应用」。请在地址栏左侧的站点设置中，把「本机上的应用 / 本地网络访问」改为允许，然后重试。",
  "core.runner.lna_prompt": "浏览器接下来会询问是否允许本站访问「本机上的应用」。请选择允许，这样页面才能连接 127.0.0.1 上的 Runner。数据不会离开你的电脑。",
  "core.runner.mixed_content": "本页经 HTTPS 打开，浏览器不允许它访问其他机器上的 http:// 地址。请在运行 Runner 的电脑上用 127.0.0.1 打开，或为 Runner 配置 HTTPS。",
  "core.runner.cors": "Runner 拒绝了本页的来源（{origin}）。请用 tcmstudio serve --allow-origin {origin} 重新启动。",
  "core.runner.token": "Runner 需要配对令牌。请点终端里打印的配对链接，或在「计算」里粘贴令牌。",
  "core.runner.timeout": "连接本机 Runner 超时（{url}）。",
  "core.runner.http": "本机 Runner 返回 {status}：{message}",
  "core.local_model.cors.ollama": "请用 OLLAMA_ORIGINS={origin} ollama serve 重新启动 Ollama 后重试（macOS 应用：launchctl setenv OLLAMA_ORIGINS {origin}，再重启 Ollama）。也可以连接本机 Runner 转发。",
  "core.local_model.cors.lmstudio": "请在 LM Studio 的 Developer → Server Settings 中打开「Enable CORS」后重试，或连接本机 Runner 转发。",
  "core.local_model.cors.vllm": "请用 vllm serve … --allowed-origins '[\"{origin}\"]' 重新启动 vLLM，或连接本机 Runner 转发。",
  "core.local_model.cors.llamacpp": "请确认 llama-server 正在运行（它默认允许跨域）；仍失败时可连接本机 Runner 转发。",
  "core.local_model.cors.generic": "请确认本地模型服务正在运行并允许来源 {origin} 跨域访问，或连接本机 Runner 转发。",
});

registerStrings("en", {
  "core.project.untitled": "New project",
  "core.md.copy": "Copy",
  "core.runner.label": "Local runner",
  "core.browser.label": "Browser",
  "core.browser.missing": "The in-browser runtime is not loaded, so Python tools are not available here.",
  "core.relay.unreachable": "Cannot reach the Tao-S1 relay: the network is down or the service is briefly unavailable. Try again later, or connect your own model API in Settings → Model.",
  "core.relay.off": "Tao-S1 is not available right now: the site's model key is not configured, or the owner has paused it. You can connect your own model API or a local model in Settings → Model.",
  "core.relay.error.https_required": "Tao-S1 must be reached over HTTPS.",
  "core.relay.error.not_found": "The Tao-S1 relay does not recognise this request. You can use your own model in Settings → Model.",
  "core.relay.error.forbidden_origin": "Tao-S1 serves only the TCMScience Studio page (science.impf.ai). You can use your own model in Settings → Model.",
  "core.relay.error.not_configured": "Tao-S1 is not available right now. You can connect your own model API or a local model in Settings → Model.",
  "core.relay.error.bad_request": "The Tao-S1 relay could not read this request. Start a new conversation and try again, or use your own model in Settings → Model.",
  "core.relay.error.too_large": "This conversation is longer than Tao-S1 accepts. Start a new conversation, or use your own model in Settings → Model.",
  "core.relay.error.model_not_allowed": "Only Tao-S1 is offered here. Choose Tao-S1, or use your own model in Settings → Model.",
  "core.relay.error.rate_limited": ({ retry }) => (retry ? `Too many requests to Tao-S1. Try again in ${retry} s.` : "Too many requests to Tao-S1. Try again in a moment."),
  "core.relay.error.daily_limit": "Your free Tao-S1 quota for today is used up. It resets every day at 00:00 UTC (08:00 Beijing time). Come back then, or use your own model in Settings → Model.",
  "core.relay.error.total_limit": "Tao-S1's quota for today, shared by all visitors, is used up. It resets every day at 00:00 UTC (08:00 Beijing time). Come back then, or use your own model in Settings → Model.",
  "core.relay.error.unavailable": "Tao-S1 is briefly unavailable. Try again later, or use your own model in Settings → Model.",
  "core.relay.error.upstream_unreachable": "Tao-S1 cannot reach its model service right now. Try again later, or use your own model in Settings → Model.",
  "core.relay.error.upstream_auth": "Tao-S1 is briefly unavailable (the model service did not accept the relay's key). Try again later, or use your own model in Settings → Model.",
  "core.relay.error.upstream_rate": "Tao-S1's model service is busy. Try again later, or use your own model in Settings → Model.",
  "core.relay.error.upstream_quota": "Tao-S1's model quota is used up for now. Try again later, or use your own model in Settings → Model.",
  "core.relay.error.upstream_error": "Tao-S1's model service reported a problem, so this answer was not finished. Try again later, or use your own model in Settings → Model.",
  "core.relay.error.upstream_rejected": "Tao-S1 could not complete this request: the model service did not accept it. Check temperature and max output in Settings → Model, rephrase the question, or start a new conversation.",
  "core.relay.error.relay_error": "The Tao-S1 relay hit a problem. Try again later, or use your own model in Settings → Model.",
  "core.relay.error.not_relay": "This local runner does not provide Tao-S1. Tao-S1 accepts only pages on https://science.impf.ai and http://127.0.0.1:8765: start the runner on its default port 8765, or use your own model API or a local model in Settings → Model.",
  "core.runner.proxy_unreachable": "Cannot reach the local runner ({url}). Check that tcmstudio serve is running, then try again.",
  "core.provider.unreachable": "Cannot reach {host}: the network is down, or the service does not accept calls from a browser (CORS). Connect the local runner and route model calls through it.",
  "core.provider.unreachable_local": "Cannot reach {label} ({host}). {fix}",
  "core.provider.bad_url": "The model endpoint is not a valid URL: {url}",
  "core.provider.no_key": "Add an API key for {label} in Settings → Model first.",
  "core.provider.http": "The model service returned {status}{hint}: {message}",
  "core.provider.hint.auth": " (the API key is invalid or lacks permission)",
  "core.provider.hint.not_found": " (wrong address or model name)",
  "core.provider.hint.rate": " (too many requests, or the quota is used up)",
  "core.provider.hint.server": " (the service is briefly unavailable)",
  "core.provider.stream_error": "The model service reported an error: {message}",
  "core.provider.unparseable": "Could not read the model service's reply: {text}",
  "core.provider.refusal": "The model declined this request{category}.",
  "core.provider.truncated_tool": "The output limit was reached before the tool arguments were complete; the tool was not run. Raise Max output in Settings and try again.",
  "core.tool.bad_json": "The arguments are not valid JSON: {error}",
  "core.tool.not_object": "The arguments must be a JSON object",
  "core.agent.stopped": "Stopped. Tool results that finished are kept in Run.",
  "core.agent.max_steps": "Reached the limit of {n} model calls for one turn; the answer may be incomplete. You can raise the limit in Settings.",
  "core.agent.retry": "Connection interrupted; retrying ({n})…",
  "core.router.unknown_tool": "There is no tool named {name}.",
  "core.router.human_only": "Refused: {name} is a person's act, not a tool call.",
  "core.router.needs_runner": "{name} needs the local runner, which is not connected. In-browser compute still works; connect the runner to run this.",
  "core.router.browser_only_setting": "{name} runs only on the local runner, and compute is set to Browser.",
  "core.router.runner_setting_offline": "Compute is set to Local runner, but the runner is not connected.",
  "core.router.missing_deps": "{name} is missing dependencies on the local runner: {missing}. Nothing approximate is substituted.",
  "core.router.no_runtime": "{name} has no runtime available right now.",
  "core.router.network_off": "Web access is off for this project, and {name} reaches the network. Turn web access on in the project settings and try again.",
  "core.router.denied": "Not approved: {name} did not run.",
  "core.router.needs_approval": "{name} needs your approval to run.",
  "core.router.cancelled": "Stopped: {name} has no result.",
  "core.router.runtime_error": "{name} failed to run: {message}",
  "core.approval.network": "Reach the network: {hosts}",
  "core.approval.job": "Start a job on the local runner: {what}",
  "core.approval.confirm": "Run an action that needs confirmation: {what}",
  "core.approval.remote_upload": "Send data to a third-party service: {hosts}",
  "core.approval.first_runner_call": "First tool run on the local runner for this project",
  "core.local.catalog": "Catalog search: {n} matches",
  "core.local.capabilities": "Available now: browser {browser}, local runner {runner}",
  "core.release.header": "Release state {passed}/{total} · {verdict}",
  "core.release.authorized": "release authorized",
  "core.release.not_authorized": "not authorized for release",
  "core.release.unmet.schema_valid": "Schema not valid",
  "core.release.unmet.evidence_verified": "Evidence not verified",
  "core.release.unmet.outputs_verified": "Outputs not verified",
  "core.release.unmet.execution_declared": "Execution not declared",
  "core.release.unmet.execution_attested": "Execution not attested",
  "core.ladder.caption": "Claim kind: {kind} — needs {needs}; weakest evidence here: {weakest} → {result}",
  "core.ladder.supports": "licensed",
  "core.ladder.does_not": "not licensed",
  "core.ladder.or": " or ",
  "core.ladder.and": ", ",
  "core.ladder.caption_unlicensed": "Claim kind: {kind} — needs {needs}; this claim also cites {unlicensed}, which cannot license this kind → not licensed",
  "core.ladder.caption_refused": "Claim kind: {kind} — needs {needs}; weakest evidence here: {weakest}, but the kernel found cited evidence that does not license it → not licensed",
  "core.runner.not_running": "No local runner found at {url}. Run tcmstudio serve in a terminal, then press Connect.",
  "core.runner.lna_denied": "The browser blocked this site from reaching apps on this device. Allow \"Apps on device / Local network access\" in the site settings (left of the address bar), then try again.",
  "core.runner.lna_prompt": "The browser will ask whether this site may reach apps on this device. Choose Allow so the page can connect to the runner on 127.0.0.1. Your data does not leave your computer.",
  "core.runner.mixed_content": "This page was opened over HTTPS, so the browser will not let it reach http:// addresses on other machines. Open the page on the computer that runs the runner (127.0.0.1), or give the runner HTTPS.",
  "core.runner.cors": "The runner refused this page's origin ({origin}). Restart it with tcmstudio serve --allow-origin {origin}.",
  "core.runner.token": "The runner needs its pairing token. Open the pairing link printed in the terminal, or paste the token under Compute.",
  "core.runner.timeout": "Timed out connecting to the local runner ({url}).",
  "core.runner.http": "The local runner returned {status}: {message}",
  "core.local_model.cors.ollama": "Restart Ollama with OLLAMA_ORIGINS={origin} ollama serve and try again (macOS app: launchctl setenv OLLAMA_ORIGINS {origin}, then restart Ollama). Or connect the local runner to forward the calls.",
  "core.local_model.cors.lmstudio": "Turn on \"Enable CORS\" under Developer → Server Settings in LM Studio and try again, or connect the local runner to forward the calls.",
  "core.local_model.cors.vllm": "Restart vLLM with vllm serve … --allowed-origins '[\"{origin}\"]', or connect the local runner to forward the calls.",
  "core.local_model.cors.llamacpp": "Check that llama-server is running (it allows cross-origin calls by default); if it still fails, connect the local runner to forward the calls.",
  "core.local_model.cors.generic": "Check that the local model server is running and allows the origin {origin}, or connect the local runner to forward the calls.",
});

// a save in another place (the settings dialog, another tab) switches the language here too
onSettingsChange(({ settings }) => {
  if (settings && LANGS.includes(settings.lang) && settings.lang !== current) {
    current = settings.lang;
    bus.emit("change", current);
  }
});
