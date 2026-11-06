// What must never reach a conversation record, judged the same way in the page (before a record is sent) and in the
// Worker (before it is stored): the Worker imports this file. Two questions:
//
//   redactText(text)       → {text, hits}: credentials and direct identifiers replaced by a marker ([redacted:email] …).
//                            Used on every recorded question and answer; a hit is counted in the record, never kept.
//   sensitiveReason(text)  → null, or why a turn must not be recorded at all: it carries an identifier of a person
//                            (an ID or record number, a name or birth-date field), it reads as a patient's case (a
//                            patient with age or sex, the sections of a medical record), or it pairs clinical detail
//                            with contact details. A conversation that once said such a thing is not recorded again.
//
// and, for the check before a question goes to a model outside the visitor's computer (ui/phidialog.js):
//
//   phiReason(text)        → sensitiveReason, except that an age band or range ("65岁以上患者", "18-75岁", "over 65
//                            years old") and a record's sections named as a topic ("主诉和现病史的写法", "chief complaint
//                            and HPI format") are not read as a patient's case or record: a research question about a
//                            population, or about how records are written, is sent unasked (and still not recorded).
//
// These are heuristics and err toward leaving a turn out: a scientific question that only names a disease, a herb or a
// pulse and tongue picture ("舌红苔黄，脉数是什么证？") is recordable; "患者，女，32岁，…" is not. The record's other
// guards (uploaded files, project files, clinical tools; web/js/core/turnrecord.js) do not depend on these.

const mark = (name) => `[redacted:${name}]`;

// [name, pattern, replacement(match, ...groups)]: in this order (keys before the generic key=value rule). A replacement
// keeps what names the secret (a field's label, a scheme) and replaces only the secret.
const REDACTIONS = [
  ["private_key", /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)/g, () => mark("private_key")],
  ["jwt", /\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}/g, () => mark("jwt")],
  ["api_key", /\b(?:sk-ant-[A-Za-z0-9_-]{16,}|sk-(?:proj-|live-|test-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[abprs]-[A-Za-z0-9-]{10,}|hf_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,})/g, () => mark("api_key")],
  // whatever follows an Authorization header's label is its credential
  ["bearer", /(\bAuthorization\s*[:=]\s*(?:(?:Bearer|Basic|Token)\s+)?)(?!\[redacted:)[A-Za-z0-9._~+/-]{8,}=*/gi, (m, pre) => pre + mark("bearer")],
  // in prose, a credential after the scheme: one with a digit, or a capital after a small letter (base64, a random
  // token); "a basic understanding" or "Basic Understanding" is not one
  ["bearer", /(\b(?:[Bb]earer|[Bb]asic|BEARER|BASIC)\s+)(?:(?=[A-Za-z._~+/-]*\d)|(?=[A-Za-z0-9._~+/-]*[a-z][A-Za-z0-9._~+/-]*[A-Z]))[A-Za-z0-9._~+/-]{12,}=*/g, (m, pre) => pre + mark("bearer")],
  ["url_credentials", /\b(https?:\/\/)[^\s/:@]+:[^\s/@]+@/gi, (m, scheme) => `${scheme}${mark("url_credentials")}@`],
  ["url_secret", /([?&](?:key|api_key|apikey|token|access_token|auth|sig|signature|password|secret|code)=)[^&\s#"'<>,，。；)）]+/gi, (m, pre) => pre + mark("url_secret")],
  ["secret", /((?:\b(?:api[_-]?key|apikey|secret(?:[_-]?key)?|access[_-]?key|auth[_-]?token|token|password|passwd|pwd))|密码|口令|密钥)(\s*[:=：]\s*["']?)(?!\[redacted:)[^\s"',;，；]{6,}/gi, (m, name, sep) => name + sep + mark("secret")],
  ["email", /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, () => mark("email")],
  ["id_number", /(?<![0-9A-Za-z])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9A-Za-z])/g, () => mark("id_number")],
  // the label a whole word ("patient notes", "patient identification" and "hospital notes" are not one)
  ["record_number", /(住院号|门诊号|病历号|病案号|就诊号|就诊卡号|医保卡?号|社保卡?号|身份证(?:号码?)?|护照(?:号码?)?|床号|\bMRN\b|\bmedical record (?:no\b\.?|number\b|#)|\bpatient (?:id|no|number)\b\.?|\bhospital (?:no|number)\b\.?)(\s*[:：#]?\s*)[A-Za-z0-9][A-Za-z0-9-]{2,}/gi, (m, label, sep) => label + sep + mark("record_number")],
  ["name", /(患者姓名|病人姓名|姓名|\bpatient name|\bname of (?:the )?patient)(\s*[:：]\s*)[^\s,，;；。:：]{1,20}/gi, (m, label, sep) => label + sep + mark("name")],
  ["birth_date", /(出生日期|出生年月|生日|\bdate of birth|\bDOB\b)(\s*[:：]?\s*)\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?/gi, (m, label, sep) => label + sep + mark("birth_date")],
  // a mainland mobile number, written whole or in groups (138 0013 8000, 138-0013-8000)
  ["mobile", /(?<!\d)(?:\+?86[- \u3000]?)?1[3-9]\d(?:[- \u3000]?\d{4}){2}(?!\d)/g, () => mark("mobile")],
  // a telephone number in one of its written forms: with a country code, a bracketed area code, a Chinese landline
  // (0xx-xxxxxxxx) or a North American one (xxx-xxx-xxxx); never a run of plain numbers, as in a table
  ["phone", /(?:\+\d{1,3}[- ]?\(?\d{1,4}\)?[- ]?\d{3,4}[- ]?\d{3,4}|\(0?\d{2,4}\)\s?\d{3,4}[- ]?\d{4}|(?<![\d-])0\d{2,3}-\d{7,8}(?![\d-])|(?<![\d-])\d{3}-\d{3}-\d{4}(?![\d-]))/g, () => mark("phone")],
  // not inside a longer dotted number; one that ends a sentence ("… 10.0.0.1.") is an address all the same
  ["ip_address", /(?<!\d)(?<!\d\.)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d|\.\d)/g, () => mark("ip_address")],
];

/** Full-width digits, Latin letters and the few signs an address or a number is written with (@ . - + _; a Chinese
 * input method types "１３８００１３８０００" or "ａｂｃ＠ｅｘａｍｐｌｅ．ｃｏｍ") as their ASCII forms; Chinese punctuation (，。：？（）) and
 * spacing are left as written. */
export function foldWidth(text) {
  return String(text).replace(/[\uFF10-\uFF19\uFF21-\uFF3A\uFF41-\uFF5A\uFF20\uFF0E\uFF0D\uFF0B\uFF3F]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0));
}

/** `text` with credentials and identifiers replaced by [redacted:<kind>]; `hits` counts the replacements. What is kept
 * is the text with full-width digits and letters written as ASCII (foldWidth), so none slips past in that form. */
export function redactText(text) {
  if (typeof text !== "string" || !text) return { text: typeof text === "string" ? text : "", hits: 0 };
  let out = foldWidth(text);
  let hits = 0;
  for (const [, re, replace] of REDACTIONS) {
    out = out.replace(re, (...m) => {
      hits++;
      return replace(...m);
    });
  }
  return { text: out, hits };
}

// identifiers of a person: one is enough to keep the turn out
const IDENTIFIERS = ["id_number", "record_number", "name", "birth_date"];
const CONTACT = ["email", "mobile", "phone"];

// a patient described as a person: age or sex beside a word for the patient, or the shorthand of a case ("女，32岁")
const CASE_ZH = [
  /(患者|病人|病者|患儿|就诊者|来诊者|求诊者)[^。\n]{0,12}?(?:[男女](?:性)?[，,、\s]|\d{1,3}\s*(?:岁|周岁|个月|月龄))/,
  /(?<![一-鿿])[男女](?:性)?\s*[，,、]\s*\d{1,3}\s*(?:岁|周岁)/,
  /\d{1,3}\s*(?:岁|周岁)[^。\n]{0,6}?(?:患者|患儿|病人|男性|女性|男孩|女孩|男童|女童|男子|女子)/,
];
const CASE_EN = [
  /\b(?:patient|pt)\b[^.\n]{0,30}?\b(?:\d{1,3}[- ]?(?:year|yr|y)s?[- ]?old|\d{1,3}\s*(?:yo|y\/o)|male|female|man|woman|boy|girl)\b/i,
  /\b\d{1,3}[- ](?:year|yr)s?[- ]old (?:male|female|man|woman|boy|girl|patient|child)\b/i,
];
// the sections of a medical record (not the words of a scientific question: no tongue or pulse here)
const SECTIONS = [
  /主诉/, /现病史/, /既往史/, /个人史/, /婚育史/, /月经史/, /家族史/, /过敏史/, /体格检查/, /辅助检查/, /入院诊断/, /出院诊断/, /初步诊断/,
  /诊疗经过/, /入院时间/, /就诊日期/, /\bchief complaint\b/i, /\bhistory of present illness\b/i, /\bHPI\b/, /\bpast medical history\b/i,
  /\bPMH\b/, /\bphysical exam(?:ination)?\b/i, /\badmission diagnosis\b/i, /\bdischarge diagnosis\b/i, /\bfamily history\b/i, /\bsocial history\b/i,
  /\bdate of admission\b/i,
];

function found(text, names) {
  const hit = new Set();
  for (const [name, re] of REDACTIONS) {
    if (!names.includes(name)) continue;
    re.lastIndex = 0;
    if (re.test(text)) hit.add(name);
    re.lastIndex = 0;
  }
  return hit;
}

/**
 * Why `text` must not be recorded: "identifier" | "clinical_record" | "clinical_case" | "contact_with_clinical", or
 * null. The contract's exclusion reason for any of them is "sensitive_text" (telemetry-contract.js EXCLUSIONS).
 */
export function sensitiveReason(text) {
  if (typeof text !== "string" || !text.trim()) return null;
  const s = text.normalize("NFKC");
  if (found(s, IDENTIFIERS).size) return "identifier";
  const sections = SECTIONS.filter((re) => re.test(s)).length;
  if (sections >= 2) return "clinical_record";
  if (CASE_ZH.some((re) => re.test(s)) || CASE_EN.some((re) => re.test(s))) return "clinical_case";
  if (sections >= 1 && found(s, CONTACT).size) return "contact_with_clinical";
  return null;
}

// an age band or range describes a population, not a person: set aside before phiReason reads a case
const AGE_UNIT = "(?:周岁|岁|个月|月龄)";
const POPULATION = [
  new RegExp(`\\d{1,3}\\s*${AGE_UNIT}?\\s*[-–—~至到]\\s*\\d{1,3}\\s*${AGE_UNIT}`, "g"), // 18-75岁, 18岁至75岁, 3~6个月
  new RegExp(`\\d{1,3}\\s*${AGE_UNIT}\\s*[及或]?\\s*(?:以上|以下|以内|之间)`, "g"), // 65岁以上, 18 岁及以下
  new RegExp(`(?:[≥≤><]=?|大于|小于|超过|高于|低于|不足|不满|未满|年满)\\s*\\d{1,3}\\s*${AGE_UNIT}`, "g"), // ≥65岁, 未满18岁
  /\b(?:over|under|above|below|older than|younger than|at least)\s+\d{1,3}\s*(?:years?|yrs?)(?:[- ]?old)?/gi,
  /\b\d{1,3}\s*(?:-|–|—|to)\s*\d{1,3}[- ]?(?:years?|yrs?)(?:[- ]?old)?/gi,
  /\b\d{1,3}[- ]?(?:years?|yrs?)(?:[- ]?old)?\s+(?:and|or)\s+(?:older|over|above|younger|under|below)\b/gi,
];
// a record's sections named as a topic (listed, then what is asked of them), never a section followed by its content:
// set aside before phiReason counts sections
const SECTION = `(?:${SECTIONS.map((re) => re.source).join("|")})`;
const SECTION_LIST = `${SECTION}(?:\\s*(?:和|与|及|以及|、|,|，|/|或|and|or|&)\\s*${SECTION})*`;
const TOPICS = [
  new RegExp(`${SECTION_LIST}\\s*(?:的|之|应该?|该|要|需要|有什么|有何|是|分别|各|都)?\\s*(?:写法|书写|格式|模板|规范|要求|区别|不同|差别|异同|含义|定义|要点|怎么写|如何写|怎样写|如何书写|是什么|写什么|包括哪些|包括什么|指什么|有哪些)`, "gi"),
  new RegExp(`(?:如何|怎样|怎么|怎么样)\\s*(?:书写|写|记录|采集|询问)\\s*${SECTION_LIST}`, "gi"),
  new RegExp(`什么是\\s*${SECTION_LIST}`, "gi"),
  new RegExp(`${SECTION_LIST}\\s+(?:sections?|formats?|templates?|structure|definitions?|layout)\\b`, "gi"),
  new RegExp(`\\b(?:difference|differences|distinction) between\\s+${SECTION_LIST}`, "gi"),
  new RegExp(`\\bhow (?:do (?:I|you|we)|should (?:I|you|we)|to) (?:write|document|record|take)\\s+(?:(?:a|an|the)\\s+)?${SECTION_LIST}`, "gi"),
];

/**
 * Why `text` should not go to a model outside this computer unasked (ui/phidialog.js): sensitiveReason's reason, but
 * a case or a record is read again once its age bands and ranges, and the record sections it names as a topic, are
 * set aside. An identifier is one whatever else the text says.
 */
export function phiReason(text) {
  const why = sensitiveReason(text);
  if (why !== "clinical_case" && why !== "clinical_record" && why !== "contact_with_clinical") return why;
  let s = text.normalize("NFKC");
  for (const re of [...POPULATION, ...TOPICS]) s = s.replace(re, "");
  return sensitiveReason(s);
}

/** The first reason any of `texts` gives, or null. */
export function sensitiveAny(texts) {
  for (const t of texts || []) {
    const why = sensitiveReason(t);
    if (why) return why;
  }
  return null;
}
