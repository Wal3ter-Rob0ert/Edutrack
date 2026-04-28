"""
EduTrack — EduPage data source.

Loads real student data from EduPage using the unofficial `edupage-api`
Python library. Returns a dict shaped like demo_data.json so the rest
of the app (build_analytics, /api/analytics, frontend) doesn't change.

This module is OPTIONAL: if env vars are missing or EduPage is unreachable,
load_data_from_edupage() returns None and the caller falls back to SQLite/JSON.

Configuration via environment variables (.env file is auto-loaded):
    EDUPAGE_USERNAME       your school login
    EDUPAGE_PASSWORD       your password
    EDUPAGE_SUBDOMAIN      e.g. "quantum" for quantum.edupage.org
    EDUPAGE_STUDENT_NAME   optional display name override
"""

import os
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# Quantum STEM School subject map.
# EduPage returns short codes; we expand them to full names + lessons/week
# from the school's official transcript (Year 11, 2025-2026).
# Subjects not in this map keep their EduPage name and get lessons_per_week=0
# (which means they're excluded from GPA — useful for clubs/extracurriculars).
SUBJECT_MAP: dict[str, tuple[str, int]] = {
    "math":    ("Mathematics",                       8),
    "cs":      ("Computer Science",                  6),
    "phy":     ("Physics",                           3),
    "gp":      ("Global Perspectives & Research",    4),
    "engr":    ("Engineering",                       2),
    "rus":     ("Russian Language",                  1),
    "rulit":   ("Russian Literature",                2),
    "it":      ("IT",                                2),
    "kaz l&l": ("Kazakh Language and Literature",    3),
    "ap phy":  ("AP Physics",                        3),
    "ap cs":   ("AP Computer Science",               4),
    "esl":     ("English as a Second Language",      5),
    "kaz hist":("Kazakh History",                    2),
    "pe":      ("Physical Education",                2),  # Pass — excluded
}


def expand_subject(short_name: str) -> tuple[str, int]:
    """Return (full_name, lessons_per_week) for an EduPage short code."""
    key = short_name.strip().lower()
    if key in SUBJECT_MAP:
        return SUBJECT_MAP[key]
    return short_name, 0


# Quantum STEM School percentage → letter grade mapping.
# Used to derive final_grade from EduPage averages.
def percent_to_letter(pct: float) -> str:
    if pct >= 95: return "A+"
    if pct >= 90: return "A"
    if pct >= 85: return "A-"
    if pct >= 80: return "B+"
    if pct >= 70: return "B"
    if pct >= 65: return "B-"
    if pct >= 60: return "C+"
    if pct >= 50: return "C"
    if pct >= 40: return "C-"
    if pct >= 35: return "D+"
    if pct >= 30: return "D"
    return "F"


def is_edupage_configured() -> bool:
    """True if all required env vars are set."""
    return all(os.environ.get(k) for k in
               ("EDUPAGE_USERNAME", "EDUPAGE_PASSWORD", "EDUPAGE_SUBDOMAIN"))


def load_data_from_edupage() -> Optional[dict]:
    """
    Fetch data from EduPage and return a dict shaped like demo_data.json.
    Returns None if not configured or any step fails — caller should fall back.
    """
    if not is_edupage_configured():
        return None

    try:
        from edupage_api import Edupage
    except ImportError:
        print("[edupage] edupage-api package not installed.")
        return None

    username = os.environ["EDUPAGE_USERNAME"]
    password = os.environ["EDUPAGE_PASSWORD"]
    subdomain = os.environ["EDUPAGE_SUBDOMAIN"]

    edu = Edupage()
    try:
        edu.login(username, password, subdomain)
    except Exception as e:
        print(f"[edupage] Login failed for {subdomain}: {e}")
        return None

    try:
        grades = edu.get_grades() or []
    except Exception as e:
        print(f"[edupage] get_grades failed: {e}")
        return None

    if not grades:
        print("[edupage] No grades returned from EduPage.")
        return None

    # Group grades by subject_name. Skip verbal/non-numeric entries.
    by_subject: dict[str, list] = {}
    months_seen: dict[str, int] = {}  # month_name → month_number for ordering

    for g in grades:
        if not g.subject_name or g.percent is None:
            continue
        by_subject.setdefault(g.subject_name, []).append(g)
        month_name = g.date.strftime("%B")
        months_seen.setdefault(month_name, g.date.month)

    if not by_subject:
        print("[edupage] No usable numeric grades.")
        return None

    months = [name for name, _ in sorted(months_seen.items(), key=lambda kv: kv[1])]

    # GOV.UK-ish palette to colour subjects when EduPage doesn't supply one.
    palette = ["#d4351c", "#1d70b8", "#00703c", "#f47738", "#912b88",
               "#ffdd00", "#0ea5e9", "#b1b4b6"]

    subjects = []
    for i, (raw_name, grade_list) in enumerate(by_subject.items()):
        full_name, lessons = expand_subject(raw_name)
        assessments = []
        monthly_scores: dict[str, list[float]] = {}

        for g in grade_list:
            month_name = g.date.strftime("%B")
            assessments.append({
                "month": month_name,
                "type":  "FA",  # EduPage doesn't expose FA/SA/SAQ taxonomy
                "title": g.title or "Assessment",
                "score": round(float(g.percent), 1),
            })
            monthly_scores.setdefault(month_name, []).append(float(g.percent))

        monthly_trend = []
        for m in months:
            scores = monthly_scores.get(m)
            if scores:
                monthly_trend.append({"month": m, "average": round(sum(scores)/len(scores), 1)})

        overall_avg = sum(float(g.percent) for g in grade_list) / len(grade_list)
        teacher_name = (grade_list[0].teacher.name
                        if grade_list[0].teacher and hasattr(grade_list[0].teacher, "name")
                        else "—")

        # PE is Pass-graded → don't derive a letter grade for it.
        final_grade = "Pass" if full_name == "Physical Education" else percent_to_letter(overall_avg)

        subjects.append({
            "id":               full_name.lower().replace(" ", "_"),
            "name":             full_name,
            "teacher":          teacher_name,
            "color":            palette[i % len(palette)],
            "absences":         0,                       # EduPage doesn't expose this here
            "final_grade":      final_grade,
            "lessons_per_week": lessons,
            "monthly_trend":    monthly_trend,
            "assessments":      assessments,
        })

    # Try to get a school year string.
    try:
        sy = edu.get_school_year()
        school_year = str(sy) if sy else "—"
    except Exception:
        school_year = "—"

    return {
        "student": {
            "name":        os.environ.get("EDUPAGE_STUDENT_NAME", username),
            "class":       "—",
            "school":      subdomain.capitalize(),
            "school_year": school_year,
            "semester":    "—",
        },
        "months":     months,
        "attendance": None,   # not available via library — caller will use baseline
        "subjects":   subjects,
    }


def merge_with_baseline(edupage: dict, baseline: dict) -> dict:
    """
    Take EduPage data and fill in fields EduPage can't provide, from the
    local SQL/JSON baseline:
      - student.class, school, semester (if EduPage left "—")
      - attendance (always from baseline)
      - subjects[*].lessons_per_week and absences (matched by name)
    """
    merged = dict(edupage)

    # Student profile fill-ins.
    bs = baseline.get("student", {})
    es = merged.get("student", {})
    for k in ("class", "school", "school_year", "semester"):
        if not es.get(k) or es[k] == "—":
            es[k] = bs.get(k, es.get(k))
    merged["student"] = es

    # Attendance comes from the local source — EduPage library doesn't expose it.
    merged["attendance"] = baseline.get("attendance", {
        "summary": {"total_days": 0, "present_days": 0, "absent_days": 0},
        "breakdown": {"excused": 0, "unexcused": 0, "late_arrivals": 0},
        "monthly": [],
    })

    # Per-subject enrichment — match by lowercased name.
    baseline_by_name = {s["name"].lower(): s for s in baseline.get("subjects", [])}
    for s in merged.get("subjects", []):
        bsub = baseline_by_name.get(s["name"].lower())
        if not bsub:
            continue
        if not s.get("lessons_per_week"):
            s["lessons_per_week"] = bsub.get("lessons_per_week", 0)
        if not s.get("absences"):
            s["absences"] = bsub.get("absences", 0)
        if s.get("color") in (None, "") or s["color"].startswith("#") is False:
            s["color"] = bsub.get("color", s.get("color"))

    return merged
