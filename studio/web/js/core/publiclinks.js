// The project's own pages are not part of the Studio's interface: the repository, its GitHub Pages (the project
// homepage and the Arena) and their raw/API hosts are linked from nowhere in the deployed app. The pages themselves stay
// where they are; only Studio stops pointing at them.
//
// Most of the interface is the app's own copy, which simply names none of them. Some text comes from the runtime and
// is shown as it came: a source card's citation ("… · <repository URL> · snapshot …"), a tool's result text, and the
// model's answer, which may repeat what a tool returned. That text passes through scrubProjectRefs() before it is
// shown, and through isProjectUrl() before anything becomes a link. Third-party links (a paper, a database, another
// project's repository) are untouched: only this project's own addresses are taken out.

// owner and repository as they appear in the addresses; matched without regard to case, as GitHub resolves them
const OWNER = "psknlr";
const REPO = "tcmscience";

/** True for an address of this project's repository or its GitHub Pages site (any path under them, any scheme). */
export function isProjectUrl(raw) {
  if (typeof raw !== "string" || !raw.trim()) return false;
  let u;
  try {
    u = new URL(/^[a-z][a-z0-9+.-]*:/i.test(raw.trim()) ? raw.trim() : `https://${raw.trim()}`);
  } catch {
    return false;
  }
  const host = u.hostname.toLowerCase().replace(/\.$/, "");
  const parts = u.pathname.split("/").filter(Boolean).map((p) => p.toLowerCase());
  if (host === `${OWNER}.github.io`) return parts[0] === REPO || parts.length === 0;
  if (["github.com", "www.github.com", "raw.githubusercontent.com", "codeload.github.com"].includes(host)) {
    return parts[0] === OWNER && (parts[1] || "").replace(/\.git$/, "") === REPO;
  }
  if (host === "api.github.com") return parts[0] === "repos" && parts[1] === OWNER && parts[2] === REPO;
  return false;
}

// a URL-like run of characters: stops at whitespace, quotes, brackets and CJK punctuation, so a URL inside JSON text
// or a Chinese sentence is cut where it ends
const URLISH = /(?:https?:\/\/|\b(?=(?:www\.)?github\.com\/|raw\.githubusercontent\.com\/|[a-z0-9-]+\.github\.io\b))[^\s"'<>()[\]{}\\，。；、：（）【】「」《》]+/gi;
const TRAILING = /[.,;:!?]+$/;

/**
 * `text` without this project's addresses. A citation's separators are tidied where the address stood
 * ("A · <url> · B" → "A · B"), an emptied "(…)" or "<…>" goes with it; any other text is left exactly as it was.
 * Never throws; a non-string comes back unchanged.
 */
export function scrubProjectRefs(text) {
  if (typeof text !== "string" || !text) return text;
  if (!/github/i.test(text)) return text; // the common case: nothing to look at
  let changed = false;
  let out = text.replace(URLISH, (m) => {
    const tail = TRAILING.exec(m)?.[0] || "";
    const url = tail ? m.slice(0, -tail.length) : m;
    if (!isProjectUrl(url)) return m;
    changed = true;
    return `\u0000${tail}`;
  });
  if (!changed) return text;
  out = out
    .replace(/\u0000[ \t]*[·•|][ \t]*/g, "") // "A · <url> · B" → "A · B", and "<url> · B" → "B"
    .replace(/[ \t]*[·•|][ \t]*\u0000/g, "") // "A · <url>" at the end
    .replace(/[(<（][ \t]*\u0000[ \t]*[)>）]/g, "") // "(<url>)"
    .replace(/\u0000/g, "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/[ \t]+$/gm, "");
  return out;
}
