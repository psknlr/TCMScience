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
when the medications were asked.
"""

from __future__ import annotations

import json
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


def _list(v: Any) -> tuple[str, ...] | None:
    if v is None:
        return None
    return tuple(split_entries(v))


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
    meds = _list(doc.get("medications"))
    allergies = _list(doc.get("allergies"))
    conditions = _list(doc.get("conditions"))
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
                  raw=dict(doc))


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
