"""Command-line entry point for syndrome differentiation and prescription support
(``bioagent clinic``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "dispatch", "COMMANDS"]

COMMANDS = ("clinic",)


def register(sub: argparse._SubParsersAction) -> None:
    c = sub.add_parser("clinic", help="TCM syndrome differentiation and draft prescriptions "
                                      "for a licensed practitioner to review and sign")
    cs = c.add_subparsers(dest="clinic_cmd")
    t = cs.add_parser("template", help="write a blank 四诊 intake with its form")
    t.add_argument("--out", default="", help="file to write (default: print)")
    a = cs.add_parser("assess", help="red flags, differentiation and a draft prescription")
    a.add_argument("intake", help="the intake JSON")
    a.add_argument("--out", required=True, help="session directory")
    a.add_argument("--add", action="append", default=[], help="HERB[:GRAMS], repeatable")
    a.add_argument("--remove", action="append", default=[], help="HERB, repeatable")
    a.add_argument("--dose", action="append", default=[], help="HERB:GRAMS, repeatable")
    a.add_argument("--apply-textbook", action="store_true",
                   help="apply the 《方剂学》 加减 whose findings are present")
    a.add_argument("--days", type=int, default=7)
    a.add_argument("--json", action="store_true")
    s = cs.add_parser("sign", help="the practitioner's decision on a session")
    s.add_argument("session", help="session directory")
    s.add_argument("--practitioner", required=True)
    s.add_argument("--licence", required=True, help="practising licence number")
    s.add_argument("--decision", required=True, choices=("accept", "modify", "reject"))
    s.add_argument("--reason", action="append", default=[], help="ISSUE_ID=REASON, repeatable")
    s.add_argument("--add", action="append", default=[], help="HERB[:GRAMS] (modify)")
    s.add_argument("--remove", action="append", default=[], help="HERB (modify)")
    s.add_argument("--dose", action="append", default=[], help="HERB:GRAMS (modify)")
    s.add_argument("--note", default="")
    v = cs.add_parser("verify", help="re-check a session directory")
    v.add_argument("session")
    k = cs.add_parser("check", help="check a prescription you wrote against an intake")
    k.add_argument("intake")
    k.add_argument("--herbs", required=True, help="HERB:GRAMS,HERB:GRAMS,...")
    f = cs.add_parser("followup", help="compare a follow-up visit with the baseline")
    f.add_argument("visits", help="JSON: {syndrome?, baseline: {...}, current: {...}}")
    f.add_argument("--json", action="store_true")
    g = cs.add_parser("agreement", help="agreement with practitioners' labels on cases")
    g.add_argument("cases", help="JSON or JSONL cases, each with intake and expert labels")
    g.add_argument("--out", default="", help="directory for agreement.md / agreement.json")
    g.add_argument("--allow-synthetic", action="store_true",
                   help="run synthetic cases as a test of the harness (not evidence)")


def _mods(adds, removes, doses) -> list[dict]:
    mods: list[dict] = []
    for r in removes:
        mods.append({"remove": r})
    for item in adds:
        herb, sep, g = item.partition(":")
        mods.append({"add": herb, "dose": float(g)} if sep else {"add": herb})
    for item in doses:
        herb, sep, g = item.partition(":")
        if not sep:
            raise ValueError(f"--dose {item!r} is HERB:GRAMS")
        mods.append({"dose": herb, "g": float(g)})
    return mods


def dispatch(a: argparse.Namespace) -> int | None:
    if a.cmd != "clinic":
        return None
    from .intake import IntakeError, read_intake, template
    from .pack import PackError, load_pack

    try:
        pack = load_pack()
    except PackError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    cmd = getattr(a, "clinic_cmd", None)
    if cmd is None:
        print("usage: bioagent clinic {template,assess,sign,verify,check,followup,agreement}",
              file=sys.stderr)
        return 2
    try:
        if cmd == "template":
            text = json.dumps(template(pack), ensure_ascii=False, indent=2)
            if a.out:
                Path(a.out).write_text(text + "\n", encoding="utf-8")
                print(f"wrote {a.out}")
            else:
                print(text)
            return 0
        if cmd == "assess":
            from .session import assess
            s = assess(a.intake, a.out, pack=pack, days=a.days,
                       modifications=_mods(a.add, a.remove, a.dose),
                       apply_textbook=a.apply_textbook)
            if a.json:
                print((Path(a.out) / "session.json").read_text(encoding="utf-8"))
                return 0
            print(f"status: {s.status}")
            for f in s.red_flags:
                print(f"  red flag {f.id}: {f.evidence} — {f.why}"
                      + (" (cleared)" if f.cleared else ""))
            if s.differentiation is not None:
                for c in s.differentiation.candidates[:3]:
                    print(f"  {c.syndrome}: {c.status}, score {c.score:.2f}")
            if s.prescription is not None and s.prescription.lines:
                rx = s.prescription
                print(f"  draft: {rx.formula} — " + "、".join(
                    f"{ln.herb}{ln.dose_g:g}g" for ln in rx.lines))
                for i in rx.issues:
                    if i.severity in ("stop", "block", "warn"):
                        print(f"  [{i.severity}] {i.id}: {i.message}")
            print(f"report: {Path(a.out) / 'report.html'}")
            return 0
        if cmd == "sign":
            from .session import SignOffError, sign
            reasons = {}
            for item in a.reason:
                key, sep, why = item.partition("=")
                if not sep:
                    print(f"--reason {item!r} is ISSUE_ID=REASON", file=sys.stderr)
                    return 2
                reasons[key] = why
            try:
                rec = sign(a.session, practitioner=a.practitioner, licence=a.licence,
                           decision=a.decision, reasons=reasons, note=a.note,
                           modifications=_mods(a.add, a.remove, a.dose), pack=pack)
            except SignOffError as exc:
                print(f"not signed: {exc}", file=sys.stderr)
                return 1
            print(f"signed: {rec['decision']} by {rec['practitioner']} at {rec['signed_at']}")
            return 0
        if cmd == "verify":
            from .session import verify
            ok, problems = verify(a.session)
            print("verified" if ok else "NOT VERIFIED:\n" + "\n".join(f"  - {p}"
                                                                      for p in problems))
            return 0 if ok else 1
        if cmd == "check":
            from .prescribe import _line_for, age_fraction, check
            intake = read_intake(a.intake, pack)
            fraction, _ = age_fraction(pack, intake.patient.age)
            lines = []
            for item in [x for x in a.herbs.split(",") if x.strip()]:
                herb, sep, g = item.strip().partition(":")
                if not sep:
                    print(f"{item!r} is HERB:GRAMS", file=sys.stderr)
                    return 2
                line, _ = _line_for(pack, herb, float(g), fraction, basis="practitioner")
                lines.append(line)
            issues = check(lines, intake, pack)
            for i in issues:
                print(f"[{i.severity}] {i.id}: {i.message}")
            if not issues:
                print("no issues found by the pack's rules (this is not a safety guarantee)")
            return 1 if any(i.severity == "stop" for i in issues) else 0
        if cmd == "followup":
            from .followup import assess_followup, parse_visit
            doc = json.loads(Path(a.visits).read_text(encoding="utf-8"))
            r = assess_followup(parse_visit(doc["baseline"]), parse_visit(doc["current"]), pack,
                                syndrome=doc.get("syndrome"))
            if a.json:
                print(json.dumps(r.as_dict(), ensure_ascii=False, indent=2))
                return 0
            red = "" if r.reduction is None else f", reduction {r.reduction:.0%}"
            print(f"{r.days} days: score {r.baseline_total:g} → {r.current_total:g}{red} "
                  f"({r.category})")
            for x in r.actions:
                print(f"  - {x}")
            return 0
        if cmd == "agreement":
            from .validate import agreement, read_cases
            rep = agreement(read_cases(a.cases), pack, allow_synthetic=a.allow_synthetic)
            md = rep.markdown()
            if a.out:
                out = Path(a.out)
                out.mkdir(parents=True, exist_ok=True)
                (out / "agreement.md").write_text(md, encoding="utf-8")
                (out / "agreement.json").write_text(
                    json.dumps(rep.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            print(md)
            return 0
    except (IntakeError, ValueError, OSError, KeyError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 2
