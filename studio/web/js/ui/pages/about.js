// About (DESIGN §4.1, §9.5): what Studio is, where the computation and the data are, what it does not claim, privacy.
// It links nowhere: the project's homepage, the Arena and the repository are not part of the Studio's interface
// (core/publiclinks.js keeps them out of runtime text too).

import { t } from "../../core/i18n.js";
import { fill, h } from "../dom.js";
import { keyValue } from "../primitives.js";

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
    h("section.about__sec", h("h2.about__h", t("ui.about.privacy")), h("p", t("ui.about.privacy_text")),
      h("p", t("ui.about.privacy_records"), " ", h("a", { href: "#/settings/privacy" }, t("ui.about.privacy_settings")))),
    h("section.about__sec", h("h2.about__h", t("ui.about.versions")),
      keyValue([
        ["tcmstudio", v.tcmstudio || "—"], ["bioagent", v.bioagent || "—"], ["psh", v.psh || "—"],
        [t("ui.about.catalog"), app.catalog ? t("ui.about.catalog_n", { core: app.catalog.core.length, entries: app.catalog.entries.length }) : "—"],
      ], { className: "kv--mono-keys" })),
    h("p.about__motto.serif", t("ui.about.motto"))));
  fill(main, page);
  return { el: page };
}
