"""Write envelope_texts.json: the text the model reads for two fixture envelopes, used by web/test/budget.test.mjs.

The text is the runner's own tcmstudio.envelope.compose_text (status, summary, limits, release, the kernel's verdict on
each claim, refusals, citations, then the result as JSON), over the governance of the fixture envelopes written by
make_governance_fixtures.py and a result large enough to be worth compacting. Re-run after a change to compose_text:

    python3 -I studio/web/test/fixtures/make_envelope_text_fixtures.py studio/web/test/fixtures
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tcmstudio.envelope import compose_text


def text_of(envelope: dict, rows: int) -> str:
    result = {"rows": [{"id": f"r{i}", "herb": "甘草", "note": "记载 recorded relation " * 6, "evidence": f"E{1 + i % 2}"} for i in range(rows)]}
    return compose_text(tool=envelope["tool"], via=envelope["via"], status=envelope["status"], summary=envelope["summary"],
                        governance=envelope["governance"], citations=envelope["citations"], model_view=result,
                        error=envelope.get("error"), job=envelope.get("job"))


def main(out: str) -> None:
    here = Path(out)
    texts = {}
    for name, rows in (("claims_refused", 200), ("safety_released", 120)):
        envelope = json.loads((here / f"envelope_{name}.json").read_text(encoding="utf-8"))
        texts[name] = text_of(envelope, rows)
    (here / "envelope_texts.json").write_text(json.dumps(texts, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent))
