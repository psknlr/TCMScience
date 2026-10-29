"""四诊 intake: the record a differentiation starts from.

The intake keeps the four examinations apart — 望 (inspection, with the tongue), 闻
(listening and smelling), 问 (inquiry) and 切 (palpation, with the pulse) — because the
record should say how each finding was obtained, and pools their findings for scoring.
Each examination lists what is ``present`` and, as pertinent negatives, what was asked
about and is ``absent``; a negated entry (无自汗) is read as absent as well.

``template`` writes a blank intake whose form lists every finding the criteria score,
grouped by the 十问 order, with the tongue and pulse terms as references, so the
collector can see what the differentiation will look for. ``None`` and ``[]`` differ:
``"medications": null`` is *not asked* and ``[]`` is *none*; interactions are checked only
when the medications were asked. A blank field ("", ["  "], ["。。。"]) records neither and
is read as not asked; so is an allergy field that holds only its label or a form's labels,
or says it was not asked or is not known (过敏史：, Allergies:, Allergy hx:, Drug allergies:,
过敏史：____, 药物：____ 食物：____, 过敏史：□无 □有, 待询问, 患者不能配合, 不详, 暂不详, 未详, 不太清楚,
记不清楚, 不记得, 患者过敏史不详, 拒答, unknown, unknown to patient, not obtained), though what
it holds is still scanned. A not-asked remark after an answer leaves the answer (无药物过敏史，
未查过敏原; NKDA (see chart)). A negative in brackets is an answer, with or without a label
(过敏史：（-）, 过敏史（-）, （-）). Medications, allergies and conditions are text: a number or an
object there is refused, not turned into text.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .findings import TermMatcher, normalise, split_entries
from .pack import ClinicPack

__all__ = ["Patient", "Intake", "IntakeError", "read_intake", "parse_intake", "template",
           "SCHEMA", "SHIWEN"]

SCHEMA = "bioagent.clinic.intake/1"
SHIWEN = ("一问寒热二问汗，三问头身四问便，五问饮食六问胸，七聋八渴俱当辨，九问旧病十问因，"
          "再兼服药参机变，妇女尤必问经期，迟速闭崩皆可见，再添片语告儿科，天花麻疹全占验。"
          "（陈修园《医学实在易》十问歌）")
_SEXES = ("female", "male", "other", "unknown")
_VITALS = ("temperature_c", "heart_rate", "resp_rate", "systolic", "diastolic", "spo2")
_EXAMS = {"inspection": "望诊", "listening_smelling": "闻诊", "inquiry": "问诊",
          "palpation": "切诊"}


class IntakeError(ValueError):
    """The intake cannot be used: a required field is missing or malformed."""


@dataclass(frozen=True)
class Patient:
    id: str
    age: float
    sex: str
    pregnant: bool | None = None
    breastfeeding: bool | None = None
    weight_kg: float | None = None

    @property
    def childbearing_age(self) -> bool:
        return self.sex in ("female", "other", "unknown") and 12 <= self.age <= 55


@dataclass(frozen=True)
class Intake:
    patient: Patient
    chief_complaint: str
    duration: str
    present: tuple[str, ...]
    absent: tuple[str, ...]
    tongue: tuple[str, ...]
    pulse: tuple[str, ...]
    by_exam: Mapping[str, Mapping[str, tuple[str, ...]]]
    vitals: Mapping[str, float]
    medications: tuple[str, ...] | None
    allergies: tuple[str, ...] | None
    conditions: tuple[str, ...] | None
    red_flags_cleared: tuple[Mapping[str, str], ...]
    collected_by: str
    collected_at: str
    warnings: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)
    #: the allergy entries as written, unsplit and unnormalised, which the allergy check
    #: scans whole (``allergies`` holds them split and normalised, for the report); None
    #: when allergies were not asked
    allergy_records: tuple[str, ...] | None = None

    @property
    def all_text(self) -> list[str]:
        """Every recorded entry, for screening (red flags read everything)."""
        return [self.chief_complaint and normalise(self.chief_complaint), *self.present,
                *self.tongue, *self.pulse]


def _num(v: Any, name: str, *, required: bool = False) -> float | None:
    if v is None or v == "":
        if required:
            raise IntakeError(f"{name} is required")
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        raise IntakeError(f"{name} must be a number, not {v!r}") from None
    if out != out:                                   # NaN
        raise IntakeError(f"{name} must be a number")
    return out


def _bool(v: Any, name: str) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "yes", "y", "1", "是", "有"):
        return True
    if s in ("false", "no", "n", "0", "否", "无"):
        return False
    raise IntakeError(f"{name} must be true, false or null, not {v!r}")


def _items(v: Any, name: str) -> list[str] | None:
    """The written entries of a list field (medications, allergies, conditions), or None
    when it was not asked.

    ``null`` is *not asked* and ``[]`` is *none*. A blank — "", spaces, entries without a
    letter or a digit ("   ", [""], ["。。。"], ["///"]) — records neither, so it reads as
    not asked, never as "asked, none". Anything but text (a number, an object, true, a nested
    list) is refused with the message the studio's argument check gives, rather than
    turned into text or left to fail later."""
    if v is None:
        return None
    items = [v] if isinstance(v, str) else v
    if not isinstance(items, (list, tuple)) or not all(isinstance(x, str) for x in items):
        raise IntakeError(f"{name} must be a list of strings or null")
    if not items:
        return []
    written = [x for x in items if any(unicodedata.category(c)[0] in "LN" for c in x)
               or _BRACKETED_NEGATIVE.search(unicodedata.normalize("NFKC", x))]
    return written or None


#: The negative mark in brackets, (-) or （-）: written, it is an answer, though it holds no
#: letter (a bare "-" may only start a form's line).
_BRACKETED_NEGATIVE = re.compile(r"[(（][^\S\n]*[-－−][^\S\n]*[)）]")


#: The labels of an allergy field, and the words of a form around them, which answer nothing
#: on their own: 过敏史：, 药物过敏史：, 过敏原：, 过敏史（药物/食物）：, 过敏史及不良反应史：, 药物：
#: 食物：, Allergies:, Drug allergies:, Known allergies:, Allergies (drug/food):, Allergies &
#: ADRs:.
_ALLERGY_LABELS = re.compile(
    r"(?:既往|已知|药物|食物|中药|西药|接触物|其他|其它)?(?:过敏史|过敏原|过敏源|过敏物质|过敏物|过敏药物"
    r"|过敏药品|过敏食物|过敏情况|过敏反应|过敏及不良反应|过敏|药敏史|药敏|致敏原|变应原)"
    r"|(?:药物)?不良反应史?|既往史|药物|食物|中药|西药|接触物|其他|其它|有无|及|和|与"
    r"|\b(?:allerg(?:ies|y|ens?|ic)?|known|drugs?|foods?|medications?|medicines?|meds?"
    r"|environmental|other|history|hx|status|intolerances?|reactions?|adrs?|and|or|list)\b",
    re.IGNORECASE)
#: Words that say the allergies are not known, or that the answer was not given: 不详, 未详,
#: 待查, 记不清楚, 不太清楚, 不记得, 拒答, unknown, unsure, TBD, null, not known, not recorded.
#: The longer of two that start alike comes first (记不清楚 before 记不清), so nothing of one
#: is left over to read as an answer.
_UNKNOWN = re.compile(
    r"记不清楚|说不清楚|搞不清楚|不太清楚|不大清楚|不清楚|记不清|说不清|不清|不太明确|不明确|不明|不详|未详|未知"
    r"|不知道|不知|不记得|记不得|记不起来?|想不起来?|忘记了?|忘了|不确定|无法确定|不能确定"
    r"|待查|待补充|待补|待完善|待追问|需追问|追问|待定"
    r"|拒答|拒绝回答|拒绝提供|不能提供|无法提供|未提供|未记录|无记录|无法获取|未能获取|未获取|无法获得"
    r"|\b(?:unknown|unk|unsure|uncertain|tbd|tba|null|n/k|nk|nr|blank|not\s+known|not\s+sure"
    r"|not\s+recorded|not\s+documented|not\s+obtained|not\s+elicited|not\s+assessed"
    r"|unable\s+to\s+(?:obtain|assess|state|confirm|elicit)|cannot\s+(?:recall|remember)"
    r"|can't\s+(?:recall|remember)|does\s+not\s+(?:recall|remember|know)|doesn't\s+(?:recall|remember|know))\b",
    re.IGNORECASE)
#: Who speaks, what an unknown is said of, and the small words around it, which answer
#: nothing either: 患者过敏史不详, 家属诉过敏史不详, 过敏史：患者不清楚, 尚不清楚, 具体不详, 暂不详,
#: 目前不详, 家属亦不清楚, Unknown to patient.
_SPEAKERS = re.compile(
    r"患者|病人|本人|家属|自诉|代诉|诉|述|尚|具体|暂时|暂|目前|现|亦|也|均|都|仍"
    r"|\b(?:patient|family|pt|to|the|by|per|is|are)\b", re.IGNORECASE)
#: Words that say they were not asked or could not be: 未询问, 待询问, 患者昏迷，未能询问, 患者不能
#: 配合. Written after an answer, they are a remark on it, and the answer stands (无药物过敏史，未查
#: 过敏原; NKDA (see chart); 否认药物过敏，食物过敏未提及).
_NOT_ASKED = re.compile(r"未询问|未能询问|无法询问|待询问|未及询问|未问|待问|未采集|不能配合|无法配合|不能沟通"
                        r"|无法沟通|未查(?![到出见])|未提及|未涉及|详见病历|见病历"
                        r"|\b(?:not\s+asked|deferred|see\s+(?:chart|notes|ehr|record))\b",
                        re.IGNORECASE)
#: What makes an entry an answer beside a not-asked remark: a none (无, 否认, 没有, 阴性, NKDA,
#: none, no, nil, denies) or an allergy recorded (青霉素过敏，其他未询问).
_ANSWER_WORDS = re.compile(r"无|否认|没有|阴性|未发现|未见|过敏|反应|皮疹"
                           r"|\b(?:nkda|nka|nkfa|none|no|nil|negative|denies|denied|allerg[a-z]*"
                           r"|reactions?|rash)\b", re.IGNORECASE)
#: A form's options left unticked (□无 □有, ○无 ○有) or printed with no choice made (无/有),
#: and placeholders (XX, ___). No branch starts with an optional part before a run of
#: whitespace, so a long run is read once (linear time).
_UNFILLED = re.compile(
    r"[□☐○◯]\s*(?:无|有|是|否|yes|no)|(?:无|有|是|否|yes|no)\s*[□☐○◯]"
    r"|[(（]\s*(?:无\s*[/／]\s*有|有\s*[/／]\s*无)\s*[)）]?"
    r"|(?:无\s*[/／]\s*有|有\s*[/／]\s*无)\s*[)）]?|\b[xX]{2,}\b", re.IGNORECASE)
#: A negative written after a label, or alone: 过敏史：（-）, 过敏史：-, 过敏史（-）, （-）, -.
#: Written so that a long run of whitespace is read once.
_LABELLED_NEGATIVE = re.compile(
    r"(?:^|[:：∶])\s*(?:[(（]\s*)?[-－−]\s*(?:[)）]\s*)?(?:$|[,，;；。\n])"
    r"|(?:^|过敏史|过敏|\ballerg[a-z]*)\s*[(（]\s*[-－−]\s*[)）]\s*(?:$|[,，;；。\n])",
    re.IGNORECASE)


def _answers(entry: str) -> bool:
    """An allergy entry that answers the question, with a name or a none: not one that holds
    only its label or a form's labels (过敏史：, 过敏史：____, Allergies:, Drug allergies:,
    过敏史（药物/食物）：, 药物：____ 食物：____, 过敏史：□无 □有), says it was not asked (待询问,
    未能询问, 患者不能配合) or that the allergies are not known (不详, 暂不详, 未详, 不明确,
    患者过敏史不详, 记不清楚, 不太清楚, 不记得, 拒答, unknown, unsure, unknown to patient, not
    recorded, not obtained). A negative mark after a label (过敏史：（-）, 过敏史：-, 过敏史（-）) or
    in brackets alone (（-）) is an answer; a dash that starts a line of a form (- Drug:) is not.
    A not-asked remark after an answer leaves the answer (无药物过敏史，未查过敏原; 过敏史：无，详见
    病历; NKDA (see chart)): such an entry answers when, without the remark, it still holds a none
    or an allergy (``_ANSWER_WORDS``) and reads as an answer; 患者昏迷，未能询问 does not.
    Every pattern here reads a run of whitespace once, so a long field takes linear time."""
    text = unicodedata.normalize("NFKC", entry)
    if _NOT_ASKED.search(text):
        text = _NOT_ASKED.sub(" ", text)
        if not _ANSWER_WORDS.search(_UNKNOWN.sub(" ", text)):
            return False
    if _LABELLED_NEGATIVE.search(text.strip()):
        return True
    rest = _UNFILLED.sub("", text)
    rest = _SPEAKERS.sub("", _UNKNOWN.sub("", _ALLERGY_LABELS.sub("", _UNKNOWN.sub("", rest))))
    return any(unicodedata.category(c)[0] in "LN" for c in rest)


def _list(v: Any, name: str) -> tuple[str, ...] | None:
    items = _items(v, name)
    return None if items is None else tuple(split_entries(items))


def parse_intake(doc: Mapping[str, Any], pack: ClinicPack) -> Intake:
    if not isinstance(doc, Mapping):
        raise IntakeError("an intake is a JSON object")
    p = doc.get("patient") or {}
    age = _num(p.get("age"), "patient.age", required=True)
    if not 0 <= age <= 130:
        raise IntakeError(f"patient.age {age} is not an age in years")
    sex = str(p.get("sex") or "unknown").strip().lower()
    sex = {"女": "female", "男": "male", "f": "female", "m": "male"}.get(sex, sex)
    if sex not in _SEXES:
        raise IntakeError(f"patient.sex is one of {', '.join(_SEXES)}, not {sex!r}")
    patient = Patient(id=str(p.get("id") or ""), age=age, sex=sex,
                      pregnant=_bool(p.get("pregnant"), "patient.pregnant"),
                      breastfeeding=_bool(p.get("breastfeeding"), "patient.breastfeeding"),
                      weight_kg=_num(p.get("weight_kg"), "patient.weight_kg"))
    if patient.sex == "male" and patient.pregnant:
        raise IntakeError("patient.pregnant is true for a male patient")

    matcher = TermMatcher(pack.finding_terms(), pack.synonyms)
    present: list[str] = []
    absent: list[str] = []
    by_exam: dict[str, dict[str, tuple[str, ...]]] = {}
    tongue: list[str] = []
    pulse: list[str] = []
    for key in _EXAMS:
        exam = doc.get(key) or {}
        if not isinstance(exam, Mapping):
            raise IntakeError(f"{key} is an object with present / absent lists")
        pres, neg = matcher.classify(split_entries(exam.get("present")))
        neg += split_entries(exam.get("absent"))
        entry = {"present": tuple(pres), "absent": tuple(neg)}
        if key == "inspection":
            tongue += split_entries(exam.get("tongue"))
            entry["tongue"] = tuple(split_entries(exam.get("tongue")))
        if key == "palpation":
            pulse += split_entries(exam.get("pulse"))
            entry["pulse"] = tuple(split_entries(exam.get("pulse")))
        by_exam[key] = entry
        present += [x for x in pres if x not in present]
        absent += [x for x in neg if x not in absent]

    vitals_doc = doc.get("vitals") or {}
    vitals = {k: v for k in _VITALS
              if (v := _num(vitals_doc.get(k), f"vitals.{k}")) is not None}

    warnings: list[str] = []
    if not present:
        warnings.append("no findings are recorded as present")
    if not tongue:
        warnings.append("舌象未记录：四诊合参需要舌象")
    if not pulse:
        warnings.append("脉象未记录：四诊合参需要脉象")
    if patient.childbearing_age and patient.pregnant is None:
        warnings.append("妊娠状态未记录：育龄期患者开方前须确认是否妊娠")
    meds = _list(doc.get("medications"), "medications")
    allergy_records = _items(doc.get("allergies"), "allergies")
    allergies = None
    if allergy_records is not None and (not allergy_records
                                        or any(_answers(a) for a in allergy_records)):
        allergies = tuple(split_entries(allergy_records))
    conditions = _list(doc.get("conditions"), "conditions")
    if meds is None:
        warnings.append("目前用药未询问：无法检查中西药相互作用")
    if allergies is None:
        warnings.append("过敏史未询问")
    if conditions is None:
        warnings.append("既往病史未询问：无法检查病证禁忌")
    cleared = []
    for c in doc.get("red_flags_cleared") or ():
        if not isinstance(c, Mapping) or not c.get("flag") or not c.get("by") or not c.get("how"):
            raise IntakeError("each red_flags_cleared entry needs flag, by and how")
        cleared.append({"flag": str(c["flag"]), "by": str(c["by"]), "how": str(c["how"])})
    return Intake(patient=patient, chief_complaint=str(doc.get("chief_complaint") or ""),
                  duration=str(doc.get("duration") or ""), present=tuple(present),
                  absent=tuple(absent), tongue=tuple(tongue), pulse=tuple(pulse),
                  by_exam=by_exam, vitals=vitals, medications=meds, allergies=allergies,
                  conditions=conditions, red_flags_cleared=tuple(cleared),
                  collected_by=str(doc.get("collected_by") or ""),
                  collected_at=str(doc.get("collected_at") or ""), warnings=tuple(warnings),
                  raw=dict(doc),
                  allergy_records=None if allergy_records is None else tuple(allergy_records))


def read_intake(path: str | Path, pack: ClinicPack) -> Intake:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntakeError(f"{path}: not a JSON intake ({exc})") from exc
    return parse_intake(doc, pack)


def template(pack: ClinicPack) -> dict[str, Any]:
    """A blank intake with the form: what to ask, grouped, and the references."""
    triggers = sorted({t for f in pack.formulas.values() for m in f.modifications
                       for t in m.when_any} - pack.finding_terms())
    return {
        "schema": SCHEMA,
        "patient": {"id": "", "age": None, "sex": "", "pregnant": None, "breastfeeding": None,
                    "weight_kg": None},
        "chief_complaint": "", "duration": "",
        "inspection": {"present": [], "absent": [], "tongue": []},
        "listening_smelling": {"present": [], "absent": []},
        "inquiry": {"present": [], "absent": []},
        "palpation": {"pulse": [], "present": [], "absent": []},
        "vitals": {k: None for k in _VITALS},
        "medications": None, "allergies": None, "conditions": None,
        "red_flags_cleared": [],
        "collected_by": "", "collected_at": "",
        "_form": {
            "how": ("把问到的症状写入对应四诊的 present（有）或 absent（无）；舌象写入 "
                    "inspection.tongue，脉象写入 palpation.pulse；medications / allergies / "
                    "conditions 未询问时保留 null，询问后无则写 []"),
            "十问歌": SHIWEN,
            "问诊要点（按十问分组，均为辨证计分所用）": dict(pack.inquiry_groups),
            "加减参考症状": triggers,
            "舌象参考": pack.tongue_terms(),
            "脉象参考": pack.pulse_terms(),
            "生命体征": "temperature_c 体温(°C)、heart_rate 心率、resp_rate 呼吸、systolic / "
                        "diastolic 血压(mmHg)、spo2 血氧(%)",
            "knowledge_pack": {"version": pack.version, "review_status": pack.review_status},
        },
    }
