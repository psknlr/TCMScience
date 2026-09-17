"""Clinical calculators. Deterministic, offline, and the formula is named on each one.

These run on patient data by design, which is the case the label model exists for: a
calculator is a ``LOCAL_COMPUTE`` component with the local ceiling, so a PHI payload
reaches it in process and the same payload is refused at any public connector. Every
function validates its inputs and refuses out-of-range values with a reason; none of
them is a substitute for clinical judgement, and each returns the interpretation bands
its source publishes rather than a recommendation.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["bmi", "body_surface_area", "ideal_body_weight", "egfr_ckd_epi_2021",
           "creatinine_clearance_cockcroft_gault", "corrected_calcium", "anion_gap",
           "corrected_sodium", "qtc", "mean_arterial_pressure", "cha2ds2_vasc", "has_bled",
           "wells_dvt", "wells_pe", "curb65", "meld_na", "child_pugh", "news2",
           "glasgow_coma_scale", "qsofa", "fractional_excretion_sodium",
           "henderson_hasselbalch", "alveolar_gas", "friedewald_ldl", "hba1c_to_eag",
           "basal_metabolic_rate", "parkland_formula", "weight_based_dose",
           "convert_units", "tidal_volume", "UNIT_FACTORS"]


def _num(value: Any, name: str, lo: float | None = None, hi: float | None = None,
         *, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    v = float(value)
    if math.isnan(v) or math.isinf(v):
        raise ValueError(f"{name} must be finite")
    if lo is not None and v < lo or hi is not None and v > hi:
        raise ValueError(f"{name}={v} is outside the plausible range [{lo}, {hi}]")
    if not allow_zero and v == 0:
        raise ValueError(f"{name} must not be zero")
    return v


def _sex(value: Any) -> str:
    s = str(value).strip().lower()
    if s in ("f", "female", "woman", "w"):
        return "female"
    if s in ("m", "male", "man"):
        return "male"
    raise ValueError("sex must be 'female' or 'male'")


def _flag(value: Any, name: str) -> int:
    if isinstance(value, bool) or value in (0, 1):
        return int(bool(value))
    raise ValueError(f"{name} must be true/false")


# --------------------------------------------------------------- anthropometry

def bmi(weight_kg: float, height_cm: float) -> dict[str, Any]:
    """Body mass index (kg/m²) with WHO adult categories."""
    w = _num(weight_kg, "weight_kg", 1, 500)
    h = _num(height_cm, "height_cm", 30, 272) / 100.0
    value = w / (h * h)
    band = ("underweight" if value < 18.5 else "normal" if value < 25 else
            "overweight" if value < 30 else "obese")
    return {"bmi": round(value, 2), "category": band}


def body_surface_area(weight_kg: float, height_cm: float) -> dict[str, Any]:
    """BSA (m²) by Mosteller and by Du Bois & Du Bois."""
    w = _num(weight_kg, "weight_kg", 1, 500)
    h = _num(height_cm, "height_cm", 30, 272)
    return {"mosteller_m2": round(math.sqrt(h * w / 3600.0), 3),
            "dubois_m2": round(0.007184 * (w ** 0.425) * (h ** 0.725), 3)}


def ideal_body_weight(height_cm: float, sex: str, actual_weight_kg: float | None = None
                      ) -> dict[str, Any]:
    """Devine ideal body weight; adjusted body weight when an actual weight is given."""
    h_in = _num(height_cm, "height_cm", 100, 272) / 2.54
    s = _sex(sex)
    base = 50.0 if s == "male" else 45.5
    ibw = base + 2.3 * max(0.0, h_in - 60.0)
    out: dict[str, Any] = {"ideal_body_weight_kg": round(ibw, 1), "sex": s, "method": "Devine"}
    if actual_weight_kg is not None:
        actual = _num(actual_weight_kg, "actual_weight_kg", 1, 500)
        out["adjusted_body_weight_kg"] = round(ibw + 0.4 * (actual - ibw), 1)
        out["percent_of_ideal"] = round(100.0 * actual / ibw, 1)
    return out


# --------------------------------------------------------------------- renal

def egfr_ckd_epi_2021(creatinine_mg_dl: float, age_years: float, sex: str) -> dict[str, Any]:
    """CKD-EPI 2021 creatinine equation (race-free), mL/min/1.73 m², with KDIGO G stage."""
    scr = _num(creatinine_mg_dl, "creatinine_mg_dl", 0.1, 30)
    age = _num(age_years, "age_years", 18, 120)
    s = _sex(sex)
    kappa, alpha = (0.7, -0.241) if s == "female" else (0.9, -0.302)
    ratio = scr / kappa
    egfr = 142 * min(ratio, 1.0) ** alpha * max(ratio, 1.0) ** -1.200 * 0.9938 ** age
    if s == "female":
        egfr *= 1.012
    stage = ("G1" if egfr >= 90 else "G2" if egfr >= 60 else "G3a" if egfr >= 45 else
             "G3b" if egfr >= 30 else "G4" if egfr >= 15 else "G5")
    return {"egfr_ml_min_1_73m2": round(egfr, 1), "kdigo_stage": stage,
            "equation": "CKD-EPI 2021 creatinine"}


def creatinine_clearance_cockcroft_gault(creatinine_mg_dl: float, age_years: float,
                                         weight_kg: float, sex: str) -> dict[str, Any]:
    """Cockcroft–Gault creatinine clearance (mL/min)."""
    scr = _num(creatinine_mg_dl, "creatinine_mg_dl", 0.1, 30)
    age = _num(age_years, "age_years", 18, 120)
    w = _num(weight_kg, "weight_kg", 1, 500)
    s = _sex(sex)
    crcl = (140 - age) * w / (72 * scr) * (0.85 if s == "female" else 1.0)
    return {"creatinine_clearance_ml_min": round(crcl, 1), "equation": "Cockcroft-Gault"}


def fractional_excretion_sodium(urine_sodium: float, plasma_sodium: float,
                                urine_creatinine: float, plasma_creatinine: float
                                ) -> dict[str, Any]:
    """FENa (%) = (UNa × PCr) / (PNa × UCr) × 100; same units within each pair."""
    una = _num(urine_sodium, "urine_sodium", 0, 500)
    pna = _num(plasma_sodium, "plasma_sodium", 100, 200)
    ucr = _num(urine_creatinine, "urine_creatinine", 0.1, 1000, allow_zero=False)
    pcr = _num(plasma_creatinine, "plasma_creatinine", 0.1, 30)
    fena = (una * pcr) / (pna * ucr) * 100.0
    return {"fena_percent": round(fena, 2),
            "interpretation": "prerenal pattern (<1%)" if fena < 1 else
            "intrinsic pattern (>2%)" if fena > 2 else "indeterminate (1-2%)"}


# ---------------------------------------------------------------- chemistry

def corrected_calcium(calcium_mg_dl: float, albumin_g_dl: float) -> dict[str, Any]:
    """Albumin-corrected calcium: Ca + 0.8 × (4.0 − albumin)."""
    ca = _num(calcium_mg_dl, "calcium_mg_dl", 2, 20)
    alb = _num(albumin_g_dl, "albumin_g_dl", 0.5, 7)
    return {"corrected_calcium_mg_dl": round(ca + 0.8 * (4.0 - alb), 2)}


def anion_gap(sodium: float, chloride: float, bicarbonate: float, potassium: float | None = None,
              albumin_g_dl: float | None = None) -> dict[str, Any]:
    """Anion gap (mmol/L), optionally with potassium and albumin correction."""
    na = _num(sodium, "sodium", 100, 200)
    cl = _num(chloride, "chloride", 50, 150)
    hco3 = _num(bicarbonate, "bicarbonate", 2, 60)
    gap = na - (cl + hco3)
    out: dict[str, Any] = {"anion_gap": round(gap, 1), "includes_potassium": False}
    if potassium is not None:
        gap += _num(potassium, "potassium", 1, 10)
        out.update({"anion_gap": round(gap, 1), "includes_potassium": True})
    if albumin_g_dl is not None:
        alb = _num(albumin_g_dl, "albumin_g_dl", 0.5, 7)
        out["albumin_corrected_gap"] = round(gap + 2.5 * (4.0 - alb), 1)
    return out


def corrected_sodium(sodium: float, glucose_mg_dl: float, method: str = "katz") -> dict[str, Any]:
    """Sodium corrected for hyperglycaemia: Katz 1.6 or Hillier 2.4 mmol/L per 100 mg/dL."""
    na = _num(sodium, "sodium", 100, 200)
    glu = _num(glucose_mg_dl, "glucose_mg_dl", 10, 3000)
    factor = {"katz": 1.6, "hillier": 2.4}.get(method.lower())
    if factor is None:
        raise ValueError("method must be 'katz' or 'hillier'")
    return {"corrected_sodium": round(na + factor * (glu - 100.0) / 100.0, 1), "method": method}


def henderson_hasselbalch(bicarbonate: float, pco2_mmHg: float) -> dict[str, Any]:
    """pH = 6.1 + log10(HCO3⁻ / (0.03 × pCO2))."""
    hco3 = _num(bicarbonate, "bicarbonate", 2, 60)
    pco2 = _num(pco2_mmHg, "pco2_mmHg", 5, 200)
    return {"ph": round(6.1 + math.log10(hco3 / (0.03 * pco2)), 3)}


def alveolar_gas(fio2: float, paco2_mmHg: float, pao2_mmHg: float | None = None,
                 age_years: float | None = None, patm_mmHg: float = 760.0,
                 respiratory_quotient: float = 0.8) -> dict[str, Any]:
    """Alveolar gas equation PAO2 = FiO2 × (Patm − 47) − PaCO2 / RQ, and the A–a gradient."""
    f = _num(fio2, "fio2", 0.21, 1.0)
    paco2 = _num(paco2_mmHg, "paco2_mmHg", 5, 200)
    patm = _num(patm_mmHg, "patm_mmHg", 300, 800)
    rq = _num(respiratory_quotient, "respiratory_quotient", 0.5, 1.2)
    pao2_alv = f * (patm - 47.0) - paco2 / rq
    out: dict[str, Any] = {"alveolar_po2_mmHg": round(pao2_alv, 1)}
    if pao2_mmHg is not None:
        arterial = _num(pao2_mmHg, "pao2_mmHg", 10, 700)
        out["aa_gradient_mmHg"] = round(pao2_alv - arterial, 1)
        if age_years is not None:
            out["expected_gradient_for_age"] = round(_num(age_years, "age_years", 0, 120) / 4 + 4, 1)
    return out


# ------------------------------------------------------------ cardiovascular

def qtc(qt_ms: float, heart_rate_bpm: float) -> dict[str, Any]:
    """QT corrected by Bazett, Fridericia, Framingham and Hodges (ms)."""
    qt = _num(qt_ms, "qt_ms", 100, 800)
    hr = _num(heart_rate_bpm, "heart_rate_bpm", 20, 300)
    rr = 60.0 / hr
    return {"rr_s": round(rr, 3),
            "bazett_ms": round(qt / math.sqrt(rr), 1),
            "fridericia_ms": round(qt / rr ** (1.0 / 3.0), 1),
            "framingham_ms": round(qt + 154.0 * (1.0 - rr), 1),
            "hodges_ms": round(qt + 1.75 * (hr - 60.0), 1)}


def mean_arterial_pressure(systolic: float, diastolic: float) -> dict[str, Any]:
    """MAP = DBP + (SBP − DBP) / 3."""
    sbp = _num(systolic, "systolic", 30, 300)
    dbp = _num(diastolic, "diastolic", 10, 200)
    if dbp > sbp:
        raise ValueError("diastolic cannot exceed systolic")
    return {"map_mmHg": round(dbp + (sbp - dbp) / 3.0, 1), "pulse_pressure": round(sbp - dbp, 1)}


def cha2ds2_vasc(age_years: float, sex: str, heart_failure: bool = False, hypertension: bool = False,
                 diabetes: bool = False, stroke_or_tia: bool = False,
                 vascular_disease: bool = False) -> dict[str, Any]:
    """CHA₂DS₂-VASc stroke risk score for atrial fibrillation."""
    age = _num(age_years, "age_years", 0, 120)
    s = _sex(sex)
    score = (_flag(heart_failure, "heart_failure") + _flag(hypertension, "hypertension")
             + (2 if age >= 75 else 1 if age >= 65 else 0) + _flag(diabetes, "diabetes")
             + 2 * _flag(stroke_or_tia, "stroke_or_tia")
             + _flag(vascular_disease, "vascular_disease") + (1 if s == "female" else 0))
    return {"score": score, "max": 9,
            "risk": "low" if score == 0 or (score == 1 and s == "female") else
            "intermediate" if score == 1 else "high"}


def has_bled(hypertension_uncontrolled: bool = False, abnormal_renal: bool = False,
             abnormal_liver: bool = False, stroke: bool = False, bleeding_history: bool = False,
             labile_inr: bool = False, age_over_65: bool = False, drugs: bool = False,
             alcohol: bool = False) -> dict[str, Any]:
    """HAS-BLED bleeding risk score (0–9)."""
    items = {k: _flag(v, k) for k, v in locals().items()}
    score = sum(items.values())
    return {"score": score, "max": 9, "risk": "high (≥3)" if score >= 3 else "moderate" if score == 2
            else "low", "items": items}


def wells_dvt(active_cancer: bool = False, paralysis_or_immobilisation: bool = False,
              bedridden_or_surgery: bool = False, localised_tenderness: bool = False,
              entire_leg_swollen: bool = False, calf_swelling_over_3cm: bool = False,
              pitting_oedema: bool = False, collateral_veins: bool = False,
              previous_dvt: bool = False, alternative_diagnosis_likely: bool = False
              ) -> dict[str, Any]:
    """Wells score for deep-vein thrombosis (alternative diagnosis subtracts 2)."""
    items = {k: _flag(v, k) for k, v in locals().items()}
    score = sum(v for k, v in items.items() if k != "alternative_diagnosis_likely")
    score -= 2 * items["alternative_diagnosis_likely"]
    return {"score": score, "three_tier": "high" if score >= 3 else "moderate" if score >= 1 else "low",
            "two_tier": "DVT likely" if score >= 2 else "DVT unlikely"}


def wells_pe(clinical_signs_of_dvt: bool = False, pe_most_likely: bool = False,
             heart_rate_over_100: bool = False, immobilisation_or_surgery: bool = False,
             previous_dvt_or_pe: bool = False, haemoptysis: bool = False,
             malignancy: bool = False) -> dict[str, Any]:
    """Wells score for pulmonary embolism."""
    weights = {"clinical_signs_of_dvt": 3.0, "pe_most_likely": 3.0, "heart_rate_over_100": 1.5,
               "immobilisation_or_surgery": 1.5, "previous_dvt_or_pe": 1.5, "haemoptysis": 1.0,
               "malignancy": 1.0}
    values = locals()
    score = sum(w * _flag(values[k], k) for k, w in weights.items())
    return {"score": score, "three_tier": "high" if score > 6 else "moderate" if score >= 2 else "low",
            "two_tier": "PE likely" if score > 4 else "PE unlikely"}


# ------------------------------------------------------------------ severity

def curb65(confusion: bool, urea_mmol_l: float, respiratory_rate: float, systolic: float,
           diastolic: float, age_years: float) -> dict[str, Any]:
    """CURB-65 pneumonia severity (urea > 7 mmol/L, RR ≥ 30, SBP < 90 or DBP ≤ 60, age ≥ 65)."""
    score = (_flag(confusion, "confusion")
             + (1 if _num(urea_mmol_l, "urea_mmol_l", 0, 100) > 7 else 0)
             + (1 if _num(respiratory_rate, "respiratory_rate", 0, 80) >= 30 else 0)
             + (1 if (_num(systolic, "systolic", 30, 300) < 90
                      or _num(diastolic, "diastolic", 10, 200) <= 60) else 0)
             + (1 if _num(age_years, "age_years", 0, 120) >= 65 else 0))
    return {"score": score, "risk": "low (0-1)" if score <= 1 else "moderate (2)" if score == 2
            else "high (3-5)"}


def meld_na(bilirubin_mg_dl: float, inr: float, creatinine_mg_dl: float, sodium: float,
            dialysis_twice_past_week: bool = False) -> dict[str, Any]:
    """MELD-Na as adopted by UNOS/OPTN in 2016 (values floored at 1, creatinine capped at 4,
    sodium bounded to 125–137, score capped at 40)."""
    bili = max(1.0, _num(bilirubin_mg_dl, "bilirubin_mg_dl", 0, 100))
    inr_v = max(1.0, _num(inr, "inr", 0.5, 20))
    cr = max(1.0, min(4.0, _num(creatinine_mg_dl, "creatinine_mg_dl", 0.1, 30)))
    if _flag(dialysis_twice_past_week, "dialysis_twice_past_week"):
        cr = 4.0
    na = min(137.0, max(125.0, _num(sodium, "sodium", 100, 200)))
    meld_i = round(10 * (0.957 * math.log(cr) + 0.378 * math.log(bili) + 1.120 * math.log(inr_v)
                         + 0.643))
    score = meld_i
    if meld_i > 11:
        score = round(meld_i + 1.32 * (137 - na) - 0.033 * meld_i * (137 - na))
    score = min(40, max(6, score))
    return {"meld_na": score, "meld_initial": meld_i,
            "mortality_90d_band": "<6%" if score < 10 else "6-20%" if score < 20 else
            "20-50%" if score < 30 else ">50%"}


def child_pugh(bilirubin_mg_dl: float, albumin_g_dl: float, inr: float, ascites: str = "none",
               encephalopathy: str = "none") -> dict[str, Any]:
    """Child–Pugh class for cirrhosis. ascites: none|mild|moderate; encephalopathy: none|grade1-2|grade3-4."""
    bili = _num(bilirubin_mg_dl, "bilirubin_mg_dl", 0, 100)
    alb = _num(albumin_g_dl, "albumin_g_dl", 0.5, 7)
    inr_v = _num(inr, "inr", 0.5, 20)
    asc = {"none": 1, "mild": 2, "moderate": 3, "severe": 3}.get(str(ascites).lower())
    enc = {"none": 1, "grade1-2": 2, "grade 1-2": 2, "grade3-4": 3, "grade 3-4": 3}.get(
        str(encephalopathy).lower())
    if asc is None or enc is None:
        raise ValueError("ascites must be none|mild|moderate and encephalopathy none|grade1-2|grade3-4")
    points = ((1 if bili < 2 else 2 if bili <= 3 else 3) + (1 if alb > 3.5 else 2 if alb >= 2.8 else 3)
              + (1 if inr_v < 1.7 else 2 if inr_v <= 2.3 else 3) + asc + enc)
    return {"points": points, "class": "A" if points <= 6 else "B" if points <= 9 else "C"}


def news2(respiratory_rate: float, spo2_percent: float, supplemental_oxygen: bool, systolic: float,
          heart_rate: float, alert: bool, temperature_c: float) -> dict[str, Any]:
    """National Early Warning Score 2 (SpO₂ scale 1)."""
    rr = _num(respiratory_rate, "respiratory_rate", 0, 80)
    spo2 = _num(spo2_percent, "spo2_percent", 40, 100)
    sbp = _num(systolic, "systolic", 30, 300)
    hr = _num(heart_rate, "heart_rate", 20, 300)
    temp = _num(temperature_c, "temperature_c", 25, 45)
    parts = {
        "respiratory_rate": 3 if rr <= 8 else 1 if rr <= 11 else 0 if rr <= 20 else 2 if rr <= 24 else 3,
        "spo2": 3 if spo2 <= 91 else 2 if spo2 <= 93 else 1 if spo2 <= 95 else 0,
        "oxygen": 2 * _flag(supplemental_oxygen, "supplemental_oxygen"),
        "systolic": 3 if sbp <= 90 else 2 if sbp <= 100 else 1 if sbp <= 110 else 0 if sbp <= 219 else 3,
        "heart_rate": 3 if hr <= 40 else 1 if hr <= 50 else 0 if hr <= 90 else 1 if hr <= 110
        else 2 if hr <= 130 else 3,
        "consciousness": 0 if _flag(alert, "alert") else 3,
        "temperature": 3 if temp <= 35.0 else 1 if temp <= 36.0 else 0 if temp <= 38.0
        else 1 if temp <= 39.0 else 2,
    }
    total = sum(parts.values())
    single_three = any(v == 3 for k, v in parts.items() if k != "oxygen")
    risk = ("high" if total >= 7 else "medium" if total >= 5 or single_three else "low")
    return {"score": total, "components": parts, "risk": risk}


def glasgow_coma_scale(eyes: int, verbal: int, motor: int) -> dict[str, Any]:
    """GCS total (3–15) and the conventional severity bands."""
    for name, v, hi in (("eyes", eyes, 4), ("verbal", verbal, 5), ("motor", motor, 6)):
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= hi:
            raise ValueError(f"{name} must be an integer from 1 to {hi}")
    total = eyes + verbal + motor
    return {"total": total, "severity": "severe (3-8)" if total <= 8 else "moderate (9-12)"
            if total <= 12 else "mild (13-15)"}


def qsofa(respiratory_rate: float, systolic: float, altered_mentation: bool) -> dict[str, Any]:
    """Quick SOFA: RR ≥ 22, SBP ≤ 100, altered mentation."""
    score = ((1 if _num(respiratory_rate, "respiratory_rate", 0, 80) >= 22 else 0)
             + (1 if _num(systolic, "systolic", 30, 300) <= 100 else 0)
             + _flag(altered_mentation, "altered_mentation"))
    return {"score": score, "positive": score >= 2}


# ------------------------------------------------------------------ metabolic

def friedewald_ldl(total_cholesterol_mg_dl: float, hdl_mg_dl: float, triglycerides_mg_dl: float
                   ) -> dict[str, Any]:
    """LDL = TC − HDL − TG/5 (mg/dL); not valid above 400 mg/dL triglycerides."""
    tc = _num(total_cholesterol_mg_dl, "total_cholesterol_mg_dl", 50, 1000)
    hdl = _num(hdl_mg_dl, "hdl_mg_dl", 5, 200)
    tg = _num(triglycerides_mg_dl, "triglycerides_mg_dl", 10, 5000)
    if tg > 400:
        raise ValueError("Friedewald is not valid when triglycerides exceed 400 mg/dL")
    return {"ldl_mg_dl": round(tc - hdl - tg / 5.0, 1), "non_hdl_mg_dl": round(tc - hdl, 1)}


def hba1c_to_eag(hba1c_percent: float) -> dict[str, Any]:
    """Estimated average glucose: eAG (mg/dL) = 28.7 × HbA1c − 46.7 (ADAG study)."""
    a1c = _num(hba1c_percent, "hba1c_percent", 3, 20)
    eag = 28.7 * a1c - 46.7
    return {"eag_mg_dl": round(eag, 1), "eag_mmol_l": round(eag / 18.016, 2),
            "hba1c_mmol_mol": round((a1c - 2.15) * 10.929, 1)}


def basal_metabolic_rate(weight_kg: float, height_cm: float, age_years: float, sex: str,
                         activity_factor: float = 1.2) -> dict[str, Any]:
    """Mifflin–St Jeor BMR (kcal/day) and total energy at an activity factor."""
    w = _num(weight_kg, "weight_kg", 1, 500)
    h = _num(height_cm, "height_cm", 30, 272)
    age = _num(age_years, "age_years", 0, 120)
    s = _sex(sex)
    bmr = 10 * w + 6.25 * h - 5 * age + (5 if s == "male" else -161)
    factor = _num(activity_factor, "activity_factor", 1.0, 2.5)
    return {"bmr_kcal_day": round(bmr), "tdee_kcal_day": round(bmr * factor), "equation": "Mifflin-St Jeor"}


# ------------------------------------------------------- dosing and fluids

def parkland_formula(weight_kg: float, tbsa_burned_percent: float) -> dict[str, Any]:
    """Parkland: 4 mL × kg × %TBSA of crystalloid over 24 h, half in the first 8 h."""
    w = _num(weight_kg, "weight_kg", 1, 500)
    tbsa = _num(tbsa_burned_percent, "tbsa_burned_percent", 0, 100)
    total = 4.0 * w * tbsa
    return {"total_24h_ml": round(total), "first_8h_ml": round(total / 2),
            "first_8h_rate_ml_h": round(total / 16), "next_16h_rate_ml_h": round(total / 32)}


def weight_based_dose(dose_mg_per_kg: float, weight_kg: float, max_dose_mg: float | None = None,
                      doses_per_day: int = 1) -> dict[str, Any]:
    """Weight-based dose with an optional per-dose cap; flags when the cap applied."""
    per_kg = _num(dose_mg_per_kg, "dose_mg_per_kg", 0, 10000)
    w = _num(weight_kg, "weight_kg", 0.3, 500)
    if not isinstance(doses_per_day, int) or doses_per_day < 1:
        raise ValueError("doses_per_day must be a positive integer")
    dose = per_kg * w
    capped = False
    if max_dose_mg is not None:
        cap = _num(max_dose_mg, "max_dose_mg", 0, 100000)
        if dose > cap:
            dose, capped = cap, True
    return {"dose_mg": round(dose, 2), "daily_total_mg": round(dose * doses_per_day, 2),
            "capped_at_max": capped}


def tidal_volume(height_cm: float, sex: str, ml_per_kg: float = 6.0) -> dict[str, Any]:
    """Lung-protective tidal volume from predicted (ideal) body weight."""
    ibw = ideal_body_weight(height_cm, sex)["ideal_body_weight_kg"]
    per_kg = _num(ml_per_kg, "ml_per_kg", 4, 10)
    return {"predicted_body_weight_kg": ibw, "tidal_volume_ml": round(ibw * per_kg),
            "range_6_to_8_ml_kg": [round(ibw * 6), round(ibw * 8)]}


#: Conventional → SI factors (multiply). Keys are (analyte, from_unit, to_unit).
UNIT_FACTORS: dict[tuple[str, str, str], float] = {
    ("glucose", "mg/dL", "mmol/L"): 0.0555, ("creatinine", "mg/dL", "umol/L"): 88.4,
    ("cholesterol", "mg/dL", "mmol/L"): 0.02586, ("triglycerides", "mg/dL", "mmol/L"): 0.01129,
    ("bilirubin", "mg/dL", "umol/L"): 17.1, ("urea", "mg/dL", "mmol/L"): 0.357,
    ("bun", "mg/dL", "mmol/L"): 0.357, ("calcium", "mg/dL", "mmol/L"): 0.2495,
    ("hemoglobin", "g/dL", "g/L"): 10.0, ("albumin", "g/dL", "g/L"): 10.0,
    ("uric_acid", "mg/dL", "umol/L"): 59.48, ("lactate", "mg/dL", "mmol/L"): 0.111,
    ("magnesium", "mg/dL", "mmol/L"): 0.4114, ("phosphate", "mg/dL", "mmol/L"): 0.3229,
    ("iron", "ug/dL", "umol/L"): 0.179, ("vitamin_d", "ng/mL", "nmol/L"): 2.496,
    ("temperature", "F", "C"): None,  # handled specially
    ("weight", "lb", "kg"): 0.45359237, ("height", "in", "cm"): 2.54,
}


def convert_units(analyte: str, value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
    """Convert a laboratory or anthropometric value between conventional and SI units."""
    name = str(analyte).strip().lower().replace(" ", "_")
    v = _num(value, "value")
    key = (name, from_unit, to_unit)
    reverse = (name, to_unit, from_unit)
    if name == "temperature":
        if (from_unit, to_unit) == ("F", "C"):
            return {"value": round((v - 32) * 5 / 9, 2), "unit": "C", "analyte": name}
        if (from_unit, to_unit) == ("C", "F"):
            return {"value": round(v * 9 / 5 + 32, 2), "unit": "F", "analyte": name}
        raise ValueError("temperature converts between C and F")
    if key in UNIT_FACTORS and UNIT_FACTORS[key]:
        return {"value": round(v * UNIT_FACTORS[key], 4), "unit": to_unit, "analyte": name}
    if reverse in UNIT_FACTORS and UNIT_FACTORS[reverse]:
        return {"value": round(v / UNIT_FACTORS[reverse], 4), "unit": to_unit, "analyte": name}
    known = sorted({f"{a}: {f} <-> {t}" for a, f, t in UNIT_FACTORS if a != "temperature"})
    raise ValueError(f"no conversion for {name} {from_unit} -> {to_unit}; known: {known}")
