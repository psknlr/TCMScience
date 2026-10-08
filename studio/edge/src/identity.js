// Identity questions, for the relay's defence in depth (CONTRACTS §9): a turn whose last user message asks about the
// assistant's identity or model is sent with thinking disabled and answered without reasoning, whatever the page sent.
// The page (web/js/core/prompt.js isIdentityQuestion) already does this; a client that does not, or an older page, is
// covered here. This is the same detector, kept in step by edge/test/identity.test.js, which runs both on one corpus.


const T2S = {
  誰: "谁", 們: "们", 麼: "么", 麽: "么", 開: "开", 發: "发", 訓: "训", 練: "练", 個: "个", 紹: "绍", 這: "这", 製: "制", 識: "识", 體: "体",
  嗎: "吗", 為: "为", 甚: "什", 問: "问", 請: "请", 統: "统", 創: "创", 層: "层", 礎: "础", 於: "于", 哪: "哪", 叫: "叫", 稱: "称", 字: "字",
  種: "种", 屬: "属", 廠: "厂", 經: "经", 後: "后", 機: "机", 術: "术", 構: "构", 關: "关", 係: "系", 語: "语", 詞: "词", 號: "号", 復: "复",
  輸: "输", 顯: "显", 臺: "台", 雲: "云", 義: "义", 譜: "谱", 銷: "销", 應: "应", 礙: "碍", 對: "对", 話: "话", 據: "据", 責: "责",
};

function fold(text) {
  return [...String(text || "").normalize("NFKC").toLowerCase()].map((c) => T2S[c] || c).join("").replace(/[\s\p{P}\p{S}]+/gu, "");
}

// Who is asked about: the one who answers (你/you), by its name (Tao-S1), or as "this assistant".
const TAO_ZH = /taos1/;
const TAO_EN = /\btao[-\s]?s1\b/;
const ASSISTANT_ZH = /(这个助手|本助手|该助手|这个ai|这个机器人|这个聊天机器人|这个智能体|这个问答)/;
const ABOUT_TAO_ZH = /(模型|厂商|厂家|公司|哪家|谁家|基于|背后|底层|基座|底座|开发|研发|训练|做的|制作|打造|谁|什么|哪|api|技术|是不是|是否|吗|本质|原理|真实|真正|架构|来源|提供商|供应商|关系)/;
const ABOUT_ASSISTANT_ZH = /(模型|厂商|厂家|公司|哪家|谁家|基于|背后|底层|基座|底座|开发|研发|训练|api|技术|架构|提供商|供应商)/;
const ABOUT_TAO_EN = /\b(model|llm|vendor|provider|company|api|based|powered|built|behind|underlying|made|created|trained|developed|who|what|which|where|real|actually|really|fine-?tuned|wrapper)\b/;

// A vendor or a model family named anywhere: the question is about the model behind the answer. Bare "GPT" (the
// liver enzyme) and "GLM" (the statistical model) are research words and do not count; GPT-4, GLM-4 and the rest do.
const VENDORS_ZH = /(深度求索|海螺ai|海螺问问|通义千问|通义|千问|文心一言|文心大模型|月之暗面|智谱|豆包|腾讯混元|混元大模型|讯飞星火|星火大模型|百川智能|百川大模型|零一万物|阶跃星辰|商汤|稀宇)/;
const VENDORS_EN = /\b(chat\s?gpt|gpt-?\d[\w.-]*|gpt-?o\d?|openai|claude|anthropic|deep\s?seek|mini\s?max[\w.-]*|abab[\w.-]*|qwen[\w.-]*|ernie(?:\s?bot)?|kimi|moonshot|chat\s?glm|glm-?\d[\w.-]*|doubao|gemini|llama[\w.-]*|mistral|grok|baichuan|stepfun|hunyuan|sensenova)\b/;

// asked of the one who answers: who/what it is, its model, its maker, its name; or a vendor named as what it is
const ASKS_ZH = [
  /你(们)?(到底|究竟|其实|真的)?是(谁|什么模型|什么ai|什么人工智能|哪个模型|哪家|哪一个模型|哪种模型)/,
  /(谁|哪家公司|哪个公司|什么公司|哪个团队|什么团队|哪个机构)(开发|研发|训练|制作|创造|发布|做|打造)(了)?(的)?(你|这个助手|这个ai)/,
  /你(们)?(是)?(由)?(谁|哪家|哪个公司|哪个团队)(开发|研发|训练|制作|创造|发布|做|打造)/,
  /你(的)?(名字|名称|真名|全名)/,
  /你叫什么/,
  /你(们)?(是)?(基于|用的是|用了|使用|采用|调用|接入)(的是)?(什么|哪个|哪家|哪一个|哪种)(的)?(大模型|语言模型|模型|ai|人工智能)/,
  /(什么|哪个|哪家|哪种)(的)?(大模型|语言模型|ai模型)/,
  /(底层|背后|真实|真正|基座|底座)(的)?(大模型|语言模型|模型)/,
  /你(们)?(的)?(大模型|语言模型|模型)/,
  /(自我介绍|介绍(一下)?(你自己|你们自己|一下你))/,
  /你(是不是|是否|是)(chatgpt|gpt|openai|claude|anthropic|deepseek|深度求索|minimax|海螺|abab|qwen|通义|千问|文心|ernie|kimi|moonshot|月之暗面|glm|智谱|豆包|doubao|gemini|llama|mistral|grok|混元|星火|百川)/,
  // what it runs on, behind it, under it
  /你(们)?(的)?(背后|底层|底座|基座|内核)/,
  /你(们)?(是)?(在)?(用的是|用的|用了|用|使用的是|使用的|使用|采用的是|采用的|采用|调用的是|调用的|调用|接入的是|接入的|接入|基于|依赖|依托|跑在|运行在|建立在|构建在|来自|出自)(的是)?(什么|哪个|哪家|哪种|哪一个|哪一家|啥|谁家)(的)?(公司|厂商|厂家|团队|大模型|语言模型|模型|ai|api|技术|底座|基座|引擎|架构|产品)/,
  /你(们)?(是)?(基于|用|靠|依托|由)(什么|哪个|哪家|哪种|啥|谁)(的)?(大)?(语言)?(模型|技术|底座|基座)?(开发|训练|打造|构建|搭建|研发|制造|创造)/,
  // who stands behind it, and what it was told
  /你(们)?(的)?(开发者|开发商|开发团队|开发方|提供商|供应商|服务商|厂商|厂家|创造者|制造者|创始人|训练者|训练数据|母公司|所属公司|模型版本|技术栈|系统提示|提示词|系统指令)/,
  /(重复|复述|输出|打印|显示|告诉我|给我看)(一下)?(你的)?(系统提示|提示词|系统指令|初始指令)/,
  /(这个模型|此模型)(是)?(哪家|谁家|哪个公司|什么公司|谁)(的|开发|做|训练|研发)/,
];
const ASKS_EN = [
  /\bwho (are|r) (you|u)\b/,
  /\bwhat (are|r) (you|u)\??$/,
  /\bwhat('s| is) your (name|model|underlying model|base model)\b/,
  /\b(which|what) (llm|language model|large language model|ai model|ai) (are|r|is) (you|u|this|powering (you|this))\b/,
  /\bwhat model (are|r|is) (you|u|this)\b/,
  /\b((are|r) (you|u)|is (this|tao[-\s]?s1)) (an? )?((chatgpt|ai|llm|language model|bot)\b|(gpt|glm)\s*[?.!]*\s*$)/,
  /\bwho (made|built|created|trained|developed|owns|runs) (you|u)\b/,
  /\byour (underlying|base|backend|foundation) model\b/,
  /\bintroduce yourself\b/,
  /\btell me about yourself\b/,
  /\b(are you|you are|you're) (based on|powered by|running on)\b/,
  // "which model is this", "what ai are you"
  /\b(what|which)\b[^.?!\n]{0,30}\b(model|llm|language model|ai|engine|vendor|provider|company|api|lab|team)\b[^.?!\n]{0,20}\b((are|r) (you|u)|is (tao[-\s]?s1|this(?=\s*(?:[?.!]|$|based|built|running|powered|using))))\b/,
  // "what model powers Tao-S1", "which company made you", "what LLM is behind this"
  /\b(model|llm|language model|ai|engine|vendor|provider|company|api|lab|team|who)\b[^.?!\n]{0,20}\b(powers?|powering|powered|runs?|running|behind|underlies|underlying|backs|backing|drives|driving|made|built|created|trained|developed|makes|builds|owns|operates|hosts|serves)\s+(you|u|tao[-\s]?s1|this(?=\s*(?:[?.!]|$|assistant|ai|bot|chatbot|chat|app|site|service)))\b/,
  // "what are you based on", "is Tao-S1 built on another model"
  /\b(you|u|tao[-\s]?s1|this assistant|this ai|this bot|this chatbot)(?:'re|’re)?\s+(?:(?:are|r|is|was|were|actually|really|just|also|not|even|ever|secretly)\s+)*(based on|built on|built upon|powered by|running on|run on|runs on|made by|built by|created by|trained by|developed by|a wrapper|fine-?tuned)\b/,
  /\byour\s+(?:own\s+|real\s+|actual\s+|underlying\s+|base\s+)?(model|llm|language model|ai model|vendor|provider|engine|maker|creator|developers?|company|architecture|weights|training data|system prompt|instructions)\b/,
  /\bunder the hood\b/,
];

/**
 * Does the message ask about the assistant's identity or model, even in passing? Such turns to the Tao-S1 relay are
 * sent with thinking disabled, and no reasoning is shown or stored (CONTRACTS §9). It errs toward yes: a question
 * taken for one costs only that turn's reasoning, one that is missed may have the model reason about its vendor.
 */
export function isIdentityQuestion(text) {
  const raw = String(text || "");
  if (!raw.trim() || raw.length > 2000) return false;
  const lowered = raw.normalize("NFKC").toLowerCase().replace(/\s+/g, " ");
  if (VENDORS_EN.test(lowered)) return true;
  if (TAO_EN.test(lowered) && ABOUT_TAO_EN.test(lowered)) return true;
  if (ASKS_EN.some((re) => re.test(lowered))) return true;
  const folded = fold(raw);
  if (VENDORS_ZH.test(folded)) return true;
  if (TAO_ZH.test(folded) && ABOUT_TAO_ZH.test(folded.replace(/taos1/g, ""))) return true;
  if (ASSISTANT_ZH.test(folded) && ABOUT_ASSISTANT_ZH.test(folded)) return true;
  return ASKS_ZH.some((re) => re.test(folded));
}

/** The text of a chat message's content: a string, or the text parts of an array of parts. */
function textOf(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.map((p) => (typeof p === "string" ? p : p && typeof p.text === "string" ? p.text : "")).join("\n");
}

/** Does the last user message of a chat-completions request ask about the assistant's identity or model? */
export function asksIdentity(messages) {
  if (!Array.isArray(messages)) return false;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m && m.role === "user") return isIdentityQuestion(textOf(m.content));
  }
  return false;
}
