"""A consultation: intake → red flags → differentiation → draft → practitioner sign-off.

    from bioagent.clinic.session import assess, sign
    s = assess("intake.json", "visit/")              # report.md / report.html / session.json
    sign("visit/", practitioner="…", licence="…", decision="accept",
         reasons={"pregnancy_unknown:牡丹皮": "已确认未孕（尿hCG阴性）"})

The order is fixed and each step can stop the next:

1. **Red flags.** One that is not cleared means status ``refer``: no differentiation is
   offered and nothing is drafted.
2. **Differentiation.** If no syndrome meets the criteria the status is
   ``needs_information`` and the report lists what to ask; an emergency syndrome means
   ``refer``.
3. **Draft.** The base formula, fitted and checked (``prescribe``). A ``stop`` issue
   makes the draft ``blocked``.
4. **Sign-off.** Nothing is a prescription until a practitioner signs it: accepts it,
   modifies it (the changes are checked again) or rejects it. Every ``block`` issue on
   what is signed needs the practitioner's recorded reason; a ``stop`` issue cannot be
   signed. The sign-off names the session file's digest, so a changed session no longer
   verifies.

``session.json`` holds the intake, every intermediate result, the knowledge pack's
version, digest and review status, and the code digest; ``verify`` re-checks it all.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .differentiate import RULE, Differentiation, differentiate
from .intake import Intake, parse_intake
from .pack import ClinicPack, load_pack
from .prescribe import Prescription, check, draft
from .redflags import RedFlag, screen

__all__ = ["Session", "assess", "sign", "verify", "DECISIONS", "SignOffError"]

DECISIONS = ("accept", "modify", "reject")
_SEVERITY_ZH = {"stop": "停止（不可签署）", "block": "须医师说明理由", "warn": "警示",
                "info": "提示"}
_STATUS_ZH = {"refer": "转诊：存在未排除的红旗征或急危重证",
              "needs_information": "信息不足：尚无证候符合诊断标准",
              "no_formula": "已辨证，知识包无基础方：由医师选方",
              "blocked": "草案含不可签署的问题", "draft": "草案：待执业中医师审核签署"}


class SignOffError(ValueError):
    """A sign-off that cannot be recorded."""


@dataclass
class Session:
    out_dir: Path
    status: str
    intake: Intake
    red_flags: list[RedFlag]
    differentiation: Differentiation | None
    prescription: Prescription | None
    warnings: list[str]
    manifest: dict[str, Any] = field(default_factory=dict)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _code_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def assess(intake: str | Path | Mapping[str, Any], out_dir: str | Path, *,
           pack: ClinicPack | None = None, kb: Any = None,
           modifications: Sequence[Mapping[str, Any]] = (), apply_textbook: bool = False,
           days: int = 7) -> Session:
    pack = pack or load_pack()
    if isinstance(intake, (str, Path)):
        raw = Path(intake).read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    else:
        doc = dict(intake)
        raw = json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()
    parsed = parse_intake(doc, pack)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = _now()
    flags = screen(parsed, pack)
    open_flags = [f for f in flags if not f.cleared]
    warnings = list(parsed.warnings)
    diff: Differentiation | None = None
    rx: Prescription | None = None
    if open_flags:
        status = "refer"
    else:
        diff = differentiate(parsed, pack)
        lead = diff.leading
        if lead and lead.syndrome in pack.emergency_syndromes and lead.status in ("meets",
                                                                                 "leans"):
            status = "refer"
            rx = draft(parsed, lead.syndrome, pack, kb=kb)
        elif diff.top is None:
            status = "needs_information"
        else:
            rx = draft(parsed, diff.top.syndrome, pack, kb=kb, modifications=modifications,
                       apply_textbook=apply_textbook, days=days)
            status = {"refer": "refer", "no_formula": "no_formula",
                      "blocked": "blocked"}.get(rx.status, "draft")
    if not pack.reviewed:
        warnings.append(f"知识包 {pack.version} 未经执业中医师审核（{pack.review_status}）")
    session = Session(out_dir=out, status=status, intake=parsed, red_flags=flags,
                      differentiation=diff, prescription=rx, warnings=warnings)
    _write(session, pack, kb, raw, started, modifications, apply_textbook)
    return session


def _diff_dict(d: Differentiation | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {"status": d.status, "rule": RULE, "notes": d.notes,
            "candidates": [{"syndrome": c.syndrome, "status": c.status, "score": c.score,
                            "main": c.main, "secondary": c.secondary, "tongue": c.tongue,
                            "pulse": c.pulse, "contradictions": list(c.contradictions),
                            "principle": c.principle, "formula": c.formula,
                            "parent": c.parent, "covered_by": c.covered_by,
                            "hits": [h.__dict__ for h in c.hits],
                            "unasked": list(c.unasked)} for c in d.candidates],
            "questions": [q.__dict__ for q in d.questions]}


def _write(s: Session, pack: ClinicPack, kb: Any, raw: bytes, started: str,
           modifications: Sequence[Mapping[str, Any]], apply_textbook: bool) -> None:
    from ..tcm.knowledge import default_knowledge
    kb = kb if kb is not None else default_knowledge()
    md = _markdown(s, pack)
    from ..omics.rnaseq import _html
    (s.out_dir / "report.md").write_text(md, encoding="utf-8")
    page = _html(md, {}).replace('<html lang="en">', '<html lang="zh-CN">')
    (s.out_dir / "report.html").write_text(page, encoding="utf-8")
    outputs = {name: _sha((s.out_dir / name).read_bytes())
               for name in ("report.md", "report.html")}
    s.manifest = {
        "pipeline": "bioagent.clinic", "code_digest": _code_digest(), "started": started,
        "finished": _now(), "python": platform.python_version(),
        "pack": {"version": pack.version, "sha256": pack.digest,
                 "review_status": pack.review_status, "reviewed": pack.reviewed},
        "knowledge_base": kb.stats(), "intake_sha256": _sha(raw),
        "options": {"modifications": [dict(m) for m in modifications],
                    "apply_textbook": apply_textbook},
        "outputs": outputs}
    doc = {"status": s.status, "status_zh": _STATUS_ZH[s.status], "manifest": s.manifest,
           "intake": s.intake.raw, "warnings": s.warnings,
           "red_flags": [f.__dict__ for f in s.red_flags],
           "differentiation": _diff_dict(s.differentiation),
           "prescription": s.prescription.as_dict() if s.prescription else None}
    (s.out_dir / "session.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                            encoding="utf-8")


def _markdown(s: Session, pack: ClinicPack) -> str:
    p = s.intake.patient
    lines = ["# 中医辨证与处方草案", "",
             "**本文件是供执业中医师审核的草案，未经医师签署不是处方。**", "",
             f"状态：**{_STATUS_ZH[s.status]}**", ""]
    if s.warnings:
        lines += ["## 提醒", ""] + [f"- {w}" for w in s.warnings] + [""]
    preg = {True: "妊娠", False: "未妊娠", None: "妊娠状态未记录"}[p.pregnant]
    sex = {"female": "女", "male": "男", "other": "其他", "unknown": "未记录"}[p.sex]
    lines += ["## 患者与四诊", "",
              f"- 患者：{p.id or '—'}，{p.age:g} 岁，{sex}"
              + (f"，{preg}" if p.sex != "male" else ""),
              f"- 主诉：{s.intake.chief_complaint or '—'}"
              + (f"（{s.intake.duration}）" if s.intake.duration else "")]
    names = {"inspection": "望诊", "listening_smelling": "闻诊", "inquiry": "问诊",
             "palpation": "切诊"}
    for key, zh in names.items():
        exam = s.intake.by_exam.get(key, {})
        pres, neg = exam.get("present", ()), exam.get("absent", ())
        if pres or neg:
            lines.append(f"- {zh}：" + ("、".join(pres) or "—")
                         + (f"；无：{'、'.join(neg)}" if neg else ""))
    lines.append(f"- 舌象：{'、'.join(s.intake.tongue) or '未记录'}；"
                 f"脉象：{'、'.join(s.intake.pulse) or '未记录'}")
    if s.intake.vitals:
        v = dict(s.intake.vitals)
        shown = [f"{label} {v.pop(key):g} {unit}".rstrip()
                 for key, label, unit in (("temperature_c", "体温", "°C"),
                                          ("heart_rate", "心率", "次/分"),
                                          ("resp_rate", "呼吸", "次/分"), ("spo2", "血氧", "%"))
                 if key in v]
        if "systolic" in v or "diastolic" in v:
            sys_, dia = v.pop("systolic", None), v.pop("diastolic", None)
            pair = "/".join("—" if x is None else f"{x:g}" for x in (sys_, dia))
            shown.append(f"血压 {pair} mmHg")
        lines.append("- 生命体征：" + "，".join(shown))
    for label, value in (("目前用药", s.intake.medications), ("过敏史", s.intake.allergies),
                         ("既往病史", s.intake.conditions)):
        lines.append(f"- {label}：" + ("未询问" if value is None else ("、".join(value) or "无")))
    lines.append("")
    if s.red_flags:
        lines += ["## 红旗征", "", "| 红旗征 | 依据 | 原因 | 处置 | 排除记录 |",
                  "|---|---|---|---|---|"]
        for f in s.red_flags:
            lines.append(f"| {f.id} | {f.evidence} | {f.why} | {f.action} | "
                         + (f"{f.cleared_by}：{f.cleared_how}" if f.cleared else "**未排除**")
                         + " |")
        lines.append("")
        if s.status == "refer" and s.differentiation is None:
            lines += ["存在未排除的红旗征：本次不作辨证、不出处方。排除后在 intake 的 "
                      "`red_flags_cleared` 中记录排除者与依据，再重新评估。", ""]
    d = s.differentiation
    if d is not None:
        lines += ["## 辨证", "", f"诊断标准：{RULE}。", "",
                  "| 证候 | 判断 | 评分 | 主症 | 次症 | 舌 | 脉 | 治法 | 基础方 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        verdict = {"meets": "符合", "leans": "倾向", "weak": "证据不足"}
        for c in d.candidates[:6]:
            v = verdict[c.status] + (f"（被{c.covered_by}涵盖）" if c.covered_by else "")
            lines.append(f"| {c.syndrome} | {v} | {c.score:.2f} | {c.main:g} | {c.secondary:g} | "
                         f"{c.tongue:g} | {c.pulse:g} | {c.principle} | {c.formula or '—'} |")
        lines.append("")
        lead = d.leading
        if lead is not None:
            lines.append(f"**{lead.syndrome}** 的依据：")
            via = {"exact": "", "synonym": "（同义）", "contains": "", "partial": "（部分）",
                   "features": ""}
            cat = {"main": "主症", "secondary": "次症", "tongue": "舌", "pulse": "脉"}
            for h in lead.hits:
                lines.append(f"- {cat[h.category]}：{h.term} ← 记录“{h.entry}”{via.get(h.via, '')}")
            for c in lead.contradictions:
                lines.append(f"- 相悖：{c}")
            lines.append("")
        for n in d.notes:
            lines.append(f"- {n}")
        if d.notes:
            lines.append("")
        if d.questions:
            lines += ["## 建议补充询问", "", "| 项目 | 类别 | 目的 |", "|---|---|---|"]
            lines += [f"| {q.finding} | {q.group} | {q.why} |" for q in d.questions]
            lines.append("")
    rx = s.prescription
    if rx is not None and rx.lines:
        lines += ["## 处方草案", "",
                  f"{rx.syndrome} · 治法：{rx.principle} · 基础方：**{rx.formula}**（{rx.source}）"
                  + (f" · 年龄折算：{rx.fraction_note}" if rx.fraction < 1 else ""), ""]
        if rx.formula_note:
            lines += [f"方注：{rx.formula_note}", ""]
        lines += ["| 药味 | 剂量 (g) | 依据 | 《方剂学》剂量 | 《药典》范围 | 备注 |",
                  "|---|---|---|---|---|---|"]
        basis = {"textbook": "教材剂量", "practitioner": "医师调整",
                 "textbook, capped at the Pharmacopoeia upper limit": "按药典上限",
                 "added at the Pharmacopoeia lower limit": "加味，药典下限起",
                 "added": "加味"}
        for ln in rx.lines:
            rng = f"{ln.range_g[0]:g}–{ln.range_g[1]:g}" if ln.range_g else "—"
            note = "；".join([x for x in (ln.toxicity, *ln.notes) if x]) or ""
            lines.append(f"| {ln.herb} | {ln.dose_g:g} | {basis.get(ln.basis, ln.basis)} | "
                         f"{'' if ln.textbook_g is None else f'{ln.textbook_g:g}'} | {rng} | "
                         f"{note} |")
        lines += ["", f"每剂合计 {rx.total_g:g} g。", ""]
        lines += [f"- {i}" for i in rx.instructions] + [""]
        if rx.applied:
            lines += ["已应用的加减：" + "；".join(rx.applied), ""]
        if rx.suggested:
            lines += ["《方剂学》加减参考（按本次记录的症状匹配；未应用，由医师决定）：", ""]
            for sug in rx.suggested:
                ops = "，".join(
                    (f"加{m['add']}" if "add" in m else
                     f"去{m['remove']}" if "remove" in m else f"{m['replace']}易为{m['with']}")
                    for m in sug["modifications"]) or "（已在方中）"
                tail = f" —— **未应用：{sug['skipped']}**" if sug.get("skipped") else ""
                lines.append(f"- 见“{sug['matched']}”（{sug['when']}）：{ops} —— {sug['why']}"
                             + tail)
            lines.append("")
    if rx is not None and rx.issues:
        lines += ["## 安全核查", "", "| 级别 | 编号 | 内容 | 来源 |", "|---|---|---|---|"]
        for i in rx.issues:
            lines.append(f"| {_SEVERITY_ZH[i.severity]} | `{i.id}` | {i.message} | "
                         f"{i.source or '—'} |")
        lines.append("")
    if s.status == "no_formula":
        lines += ["## 下一步", "",
                  f"本知识包未收录{rx.syndrome if rx else ''}的基础方，故无草案可签。"
                  "治法见上，由医师选方；拟定处方后可用", "",
                  "```",
                  "bioagent clinic check <本次 intake> --herbs 药味:克数,药味:克数,...",
                  "```", "",
                  "按同样的规则核查十八反/十九畏、妊娠、过敏、合用西药、病证禁忌与剂量。", ""]
    elif rx is not None and rx.lines:
        need = [i.id for i in rx.issues if i.severity == "block"]
        lines += ["## 签署", "",
                  "由执业中医师审核后签署：接受（accept）、修改（modify，修改内容会重新核查）或"
                  "拒绝（reject）。"
                  + ("以下编号须逐条说明理由：" + "、".join(f"`{x}`" for x in need) + "。"
                     if need else ""), "",
                  "```",
                  "bioagent clinic sign <本目录> --practitioner 姓名 --licence 执业证号 "
                  "--decision accept|modify|reject [--reason 编号=理由 ...]",
                  "```", ""]
    lines += ["## 这是什么", "",
              "按教材诊断标准对本次记录的四诊信息计分得到的辨证建议，以及据《方剂学》基础方、"
              "《药典》剂量范围和安全规则生成的处方草案。它依赖记录是否完整准确，不能替代医师"
              "的四诊合参与临证判断；任何处方都须由执业中医师审核签署后方可使用。辨证一致性和"
              "疗效均未经临床验证。", "",
              "## 依据", ""]
    lines += [f"- {k}：{v}" for k, v in pack.reference.items()]
    lines += [f"- 知识包：{pack.version}，{pack.review_status}", "",
              "## 复现", "",
              "`session.json` 记录了 intake、各步结果、知识包摘要与代码摘要；"
              "`bioagent clinic verify <本目录>` 可复核。", ""]
    return "\n".join(lines)


def verify(out_dir: str | Path) -> tuple[bool, list[str]]:
    out = Path(out_dir)
    problems: list[str] = []
    try:
        doc = json.loads((out / "session.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"session.json cannot be read: {exc}"]
    for name, digest in doc["manifest"]["outputs"].items():
        p = out / name
        if not p.is_file():
            problems.append(f"{name} is missing")
        elif _sha(p.read_bytes()) != digest:
            problems.append(f"{name} has changed")
    so = out / "signoff.json"
    if so.is_file():
        sign_doc = json.loads(so.read_text(encoding="utf-8"))
        if sign_doc.get("session_sha256") != _sha((out / "session.json").read_bytes()):
            problems.append("session.json has changed since it was signed")
        signed = out / "prescription_signed.md"
        if signed.is_file() and sign_doc.get("document_sha256") != _sha(signed.read_bytes()):
            problems.append("prescription_signed.md has changed since it was signed")
    return not problems, problems


def sign(out_dir: str | Path, *, practitioner: str, licence: str, decision: str,
         reasons: Mapping[str, str] | None = None,
         modifications: Sequence[Mapping[str, Any]] = (), note: str = "",
         pack: ClinicPack | None = None, kb: Any = None) -> dict[str, Any]:
    out = Path(out_dir)
    ok, problems = verify(out)
    if not ok:
        raise SignOffError("the session does not verify: " + "; ".join(problems))
    if (out / "signoff.json").is_file():
        raise SignOffError("this session is already signed; assess again for a new decision")
    if not practitioner.strip() or not licence.strip():
        raise SignOffError("a sign-off names the practitioner and their licence number")
    if decision not in DECISIONS:
        raise SignOffError(f"decision is one of {', '.join(DECISIONS)}")
    doc = json.loads((out / "session.json").read_text(encoding="utf-8"))
    status = doc["status"]
    pack = pack or load_pack()
    if doc["manifest"]["pack"]["sha256"] != pack.digest:
        raise SignOffError("the knowledge pack has changed since the session was assessed; "
                           "assess again")
    reasons = {k: v for k, v in (reasons or {}).items() if str(v).strip()}
    final: dict[str, Any] | None = None
    issues: list[dict[str, Any]] = []
    if decision == "reject":
        if not note.strip():
            raise SignOffError("a rejection records why (note)")
    else:
        if status == "refer":
            raise SignOffError("the session ends in referral; clear the red flags in the "
                               "intake and assess again before anything is signed")
        if status == "needs_information" or doc["prescription"] is None:
            raise SignOffError("no syndrome met the criteria, so there is no draft to sign; "
                               "complete the intake and assess again")
        if status == "no_formula":
            raise SignOffError(
                f"the knowledge pack holds no base formula for "
                f"{doc['prescription']['syndrome']}, so there is nothing to sign or modify; "
                f"the 治法 is {doc['prescription']['principle']} — write the prescription and "
                "check it with `bioagent clinic check`")
        if decision == "accept" and modifications:
            raise SignOffError("an accepted draft is signed as it stands; use modify")
        if decision == "accept" and status != "draft":
            raise SignOffError(f"the draft is {status}; it can be modified or rejected")
        from .prescribe import Line, _apply, age_fraction, instructions_for
        intake = parse_intake(doc["intake"], pack)
        lines = [Line(herb=x["herb"], dose_g=x["dose_g"], basis=x["basis"],
                      textbook_g=x["textbook_g"],
                      range_g=tuple(x["range_g"]) if x["range_g"] else None,
                      notes=tuple(x["notes"]), toxicity=x["toxicity"])
                 for x in doc["prescription"]["lines"]]
        if decision == "modify":
            if not modifications:
                raise SignOffError("modify needs the modifications")
            fraction, _ = age_fraction(pack, intake.patient.age)
            try:
                lines, _, _ = _apply(pack, lines, modifications, fraction)
            except ValueError as exc:
                raise SignOffError(str(exc)) from exc
        if not lines:
            raise SignOffError("the prescription to sign has no herbs")
        found = check(lines, intake, pack, kb=kb)
        stops = [i for i in found if i.severity == "stop"]
        if stops:
            raise SignOffError("cannot be signed: " + "; ".join(i.message for i in stops))
        missing = [i.id for i in found if i.severity == "block" and i.id not in reasons]
        if missing:
            raise SignOffError("each of these needs the practitioner's reason (--reason "
                               "ID=…): " + ", ".join(missing))
        issues = [i.__dict__ for i in found]
        final = {"lines": [{"herb": ln.herb, "dose_g": ln.dose_g} for ln in lines],
                 "total_g": round(sum(ln.dose_g for ln in lines), 1),
                 "days": doc["prescription"]["days"],
                 "instructions": instructions_for(lines, doc["prescription"]["days"]),
                 "formula": doc["prescription"]["formula"],
                 "syndrome": doc["prescription"]["syndrome"]}
    signed_at = _now()
    record: dict[str, Any] = {
        "session_sha256": _sha((out / "session.json").read_bytes()),
        "practitioner": practitioner.strip(), "licence": licence.strip(),
        "decision": decision, "signed_at": signed_at, "note": note,
        "modifications": [dict(m) for m in modifications],
        "acknowledged": {k: reasons[k] for k in reasons
                         if any(i["id"] == k for i in issues)},
        "issues": issues, "final_prescription": final}
    if final is not None:
        text = _signed_document(final, record, doc)
        (out / "prescription_signed.md").write_text(text, encoding="utf-8")
        record["document_sha256"] = _sha(text.encode("utf-8"))
    (out / "signoff.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    return record


def _signed_document(final: Mapping[str, Any], record: Mapping[str, Any],
                     doc: Mapping[str, Any]) -> str:
    p = doc["intake"].get("patient", {})
    lines = ["# 中药处方", "",
             f"患者：{p.get('id') or '—'}，{p.get('age')} 岁 · 辨证：{final['syndrome']} · "
             f"基础方：{final['formula'] or '—'}", "",
             "| 药味 | 剂量 (g) |", "|---|---|"]
    lines += [f"| {x['herb']} | {x['dose_g']:g} |" for x in final["lines"]]
    lines += ["", f"每剂 {final['total_g']:g} g，共 {final['days']} 剂。", ""]
    lines += [f"- {i}" for i in final["instructions"]]
    if record["acknowledged"]:
        lines += ["", "医师对核查问题的说明："]
        lines += [f"- `{k}`：{v}" for k, v in record["acknowledged"].items()]
    lines += ["", f"医师：{record['practitioner']}（执业证号 {record['licence']}） · "
              f"决定：{record['decision']} · 签署时间：{record['signed_at']}",
              f"会话摘要：{record['session_sha256']}", ""]
    return "\n".join(lines)
