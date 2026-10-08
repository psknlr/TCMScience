// About (DESIGN §4.1, §9.5): what Studio is, where the computation and the data are, what it does not claim, links.

import { t } from "../../core/i18n.js";
import { fill, h } from "../dom.js";
import { icon } from "../icons.js";
import { keyValue } from "../primitives.js";

const LINKS = [
  ["project", "https://psknlr.github.io/TCMScience/", "bookOpen"],
  ["arena", "https://psknlr.github.io/TCMScience/arena/", "scale"],
  ["github", "https://github.com/psknlr/TCMScience", "external"],
];

export function mountAbout(app, main) {
  const v = app.catalog?.versions || {};
  const list = (key, n) => h("ul.about__list", Array.from({ length: n }, (_, i) => h("li", t(`ui.about.${key}.${i + 1}`))));
  const page = h("div.page.page--about", h("article.page__inner.about",
    h("p.kicker", "TCMScience Studio"),
    h("h1.about__title.serif", t("ui.about.title")),
    h("p.about__lede", t("ui.about.lede")),
    h("section.about__sec", h("h2.about__h", t("ui.about.what")), list("what", 4)),
    h("section.about__sec", h("h2.about__h", t("ui.about.where")), list("where", 4)),
    h("section.about__sec.about__sec--claims", h("h2.about__h", t("ui.about.not")), list("not", 5)),
    h("section.about__sec", h("h2.about__h", t("ui.about.privacy")), h("p", t("ui.about.privacy_text"))),
    h("section.about__sec", h("h2.about__h", t("ui.about.links")),
      h("ul.about__links", { role: "list" }, LINKS.map(([id, href, ic]) => h("li", h("a.about__link", { href, target: "_blank", rel: "noopener noreferrer" },
        icon(ic, { size: 16 }), h("span", h("span.about__link-title", t(`ui.about.link.${id}`)), h("span.about__link-url", href.replace(/^https:\/\//, "")))))))),
    h("section.about__sec", h("h2.about__h", t("ui.about.versions")),
      keyValue([
        ["tcmstudio", v.tcmstudio || "—"], ["bioagent", v.bioagent || "—"], ["psh", v.psh || "—"],
        [t("ui.about.catalog"), app.catalog ? t("ui.about.catalog_n", { core: app.catalog.core.length, entries: app.catalog.entries.length }) : "—"],
      ], { className: "kv--mono-keys" })),
    h("p.about__motto.serif", t("ui.about.motto"))));
  fill(main, page);
  return { el: page };
}
