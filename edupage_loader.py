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
import json
from datetime import datetime
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


# EduPage assessment category weights (matches the school's display formula):
#   weighted_avg = Σ(weight × category_avg) / Σ(weight)
# where category_avg is the simple mean of percentages within that category.
CATEGORY_WEIGHTS = {
    "FA":          25,   # Формативное оценивание
    "SA_SECTION":  25,   # Суммативное за раздел  (СОР / БЖБ)
    "SA_SEMESTER": 50,   # Суммативное за семестр (СОЧ / ТЖБ / Midterm)
}

# Human-readable labels for the assessment["type"] field shown in the UI.
CATEGORY_LABEL = {
    "FA":          "FA",
    "SA_SECTION":  "SA",
    "SA_SEMESTER": "Midterm",
}


import re

_CAT_PATTERNS = [
    ("SA_SEMESTER", re.compile(r"^\s*(midterm|соч|toч|тжб|tжб|final\s*exam)", re.IGNORECASE)),
    ("SA_SECTION",  re.compile(r"^\s*(сор|бжб|sa\d*|sau\d*|summative)", re.IGNORECASE)),
    ("FA",          re.compile(r"^\s*(фо|fa\d*|formative)", re.IGNORECASE)),
]

# Comments that mark a grade as a pending placeholder rather than a real score.
# EduPage shows these as empty cells with a flag icon, not as a 0% grade — so
# they must NOT be averaged in. Match Russian/Kazakh teacher phrasings.
_PENDING_COMMENT_TOKENS = (
    "нужно сдать", "надо сдать", "сдать", "пересдать",
    "нужно отработать", "надо отработать", "отработать",
    "was absent", "missed", "не было", "отсутств",
)


def is_pending_placeholder(grade) -> bool:
    """True if this grade is a 'submit later / redo' placeholder, not a real score."""
    if grade.percent is None:
        return True
    comment = (getattr(grade, "comment", None) or "").lower()
    if not comment:
        return False
    return any(tok in comment for tok in _PENDING_COMMENT_TOKENS)


# ───── Direct EduPage event-category lookup ──────────────────────────────────
# The `edupage-api` library doesn't expose `KategoriaID`, but it's right there
# in the raw HTML payload of /znamky. Mapping observed at Quantum STEM School:
#   KategoriaID = "1"  → FA           (Формативное оценивание)
#   KategoriaID = "2"  → SA_SECTION   (Суммативное за раздел)
#   KategoriaID = "3"  → SA_SEMESTER  (Суммативное за семестр / Midterm)
KATEGORIA_TO_CATEGORY = {
    "1": "FA",
    "2": "SA_SECTION",
    "3": "SA_SEMESTER",
}


def fetch_znamky_payload(edu) -> Optional[dict]:
    """Pull the raw `znamkyStudentViewer({...})` JSON from EduPage's grades page.

    The official `edupage-api` library drops half the data we need (KategoriaID,
    student profile, class info), so we go to the source. Returns None on failure.
    """
    try:
        url = f"https://{edu.subdomain}.edupage.org/znamky"
        html = edu.session.get(url, timeout=15).text
        marker = ".znamkyStudentViewer("
        i = html.find(marker)
        if i < 0:
            return None
        start = i + len(marker)
        end = html.find(");\r\n\t\t});", start)
        if end < 0:
            return None
        return json.loads(html[start:end])
    except Exception as e:
        print(f"[edupage] fetch_znamky_payload failed: {e}")
        return None


def extract_event_categories(payload: dict) -> dict[int, str]:
    """Build {event_id → category_key} from the znamky payload."""
    if not payload:
        return {}
    events = (payload.get("vsetkyUdalosti") or {}).get("edupage") or {}
    out: dict[int, str] = {}
    for event_id_str, details in events.items():
        kat = (details or {}).get("KategoriaID")
        cat = KATEGORIA_TO_CATEGORY.get(str(kat))
        if cat:
            try:
                out[int(event_id_str)] = cat
            except ValueError:
                pass
    return out


def extract_student_profile(payload: dict) -> dict:
    """Pull real student name, class, semester from the znamky payload."""
    if not payload:
        return {}
    student = payload.get("student") or {}
    trieda = payload.get("trieda") or {}
    obdobia = payload.get("obdobia") or []
    polrok = payload.get("polrok")

    first = (student.get("p_meno") or "").strip()
    last = (student.get("p_priezvisko") or "").strip()
    full_name = f"{first} {last}".strip() or None

    semester_label = None
    for o in obdobia:
        if o.get("polrok") == polrok:
            semester_label = o.get("nazov")
            break

    return {
        "name":        full_name,
        "class":       trieda.get("Meno") or trieda.get("p_skratka"),
        "school_year": trieda.get("YearID"),
        "semester":    semester_label,
    }


def detect_category(title: str) -> str:
    """Map an EduPage assessment title to FA / SA_SECTION / SA_SEMESTER.

    Defaults to FA (formative) when no category prefix is recognised, since
    unprefixed entries on EduPage are typically classroom formatives.
    """
    t = (title or "").strip()
    for cat, pat in _CAT_PATTERNS:
        if pat.search(t):
            return cat
    return "FA"


def calculate_weighted_average(grades: list[tuple[float, str]]) -> float:
    """EduPage formula: average within each category, then weight by CATEGORY_WEIGHTS."""
    by_cat: dict[str, list[float]] = {}
    for pct, cat in grades:
        by_cat.setdefault(cat, []).append(pct)

    weighted_sum, weight_sum = 0.0, 0.0
    for cat, scores in by_cat.items():
        w = CATEGORY_WEIGHTS.get(cat, 0)
        if w == 0 or not scores:
            continue
        cat_avg = sum(scores) / len(scores)
        weighted_sum += w * cat_avg
        weight_sum += w

    return round(weighted_sum / weight_sum, 1) if weight_sum else 0.0


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


def load_data_from_edupage(username: Optional[str] = None,
                           password: Optional[str] = None,
                           subdomain: Optional[str] = None) -> Optional[dict]:
    """
    Fetch data from EduPage for a specific user.

    Credentials can be passed explicitly (per-user login) or read from
    environment variables (single-user fallback for development).
    Returns None if credentials are missing/invalid or the API call fails.
    """
    if username is None or password is None or subdomain is None:
        if not is_edupage_configured():
            return None
        username = os.environ["EDUPAGE_USERNAME"]
        password = os.environ["EDUPAGE_PASSWORD"]
        subdomain = os.environ["EDUPAGE_SUBDOMAIN"]

    try:
        from edupage_api import Edupage
    except ImportError:
        print("[edupage] edupage-api package not installed.")
        return None

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

    # One fetch of the znamky page gives us KategoriaID per event AND the
    # logged-in student's real profile (name, class, semester).
    znamky = fetch_znamky_payload(edu)
    event_categories = extract_event_categories(znamky)
    edu_profile = extract_student_profile(znamky)

    # Group grades by subject_name. Skip verbal/non-numeric entries.
    by_subject: dict[str, list] = {}
    months_seen: dict[str, int] = {}  # month_name → month_number for ordering

    for g in grades:
        if not g.subject_name or g.percent is None:
            continue
        if is_pending_placeholder(g):
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
        cats: list[tuple[float, str]] = []
        monthly_by_cat: dict[str, list[tuple[float, str]]] = {}

        for g in grade_list:
            month_name = g.date.strftime("%B")
            # Prefer EduPage's own KategoriaID; fall back to title-regex.
            cat = event_categories.get(g.event_id) or detect_category(g.title or "")
            pct = float(g.percent)
            assessments.append({
                "month": month_name,
                "type":  CATEGORY_LABEL[cat],
                "title": g.title or "Assessment",
                "score": round(pct, 1),
            })
            cats.append((pct, cat))
            monthly_by_cat.setdefault(month_name, []).append((pct, cat))

        # Monthly trend uses the same weighted formula so the chart matches.
        monthly_trend = []
        for m in months:
            month_grades = monthly_by_cat.get(m)
            if month_grades:
                monthly_trend.append({"month": m, "average": calculate_weighted_average(month_grades)})

        overall_avg = calculate_weighted_average(cats)
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

    # School year — prefer EduPage profile, fall back to API method.
    school_year = edu_profile.get("school_year")
    if not school_year:
        try:
            sy = edu.get_school_year()
            school_year = str(sy) if sy else "—"
        except Exception:
            school_year = "—"
    # Make it a "2025/2026" range instead of just "2025" for clarity.
    if school_year and school_year.isdigit():
        school_year = f"{school_year}/{int(school_year) + 1}"

    return {
        "student": {
            "name":        edu_profile.get("name") or username,
            "class":       edu_profile.get("class") or "—",
            "school":      subdomain.capitalize(),
            "school_year": school_year or "—",
            "semester":    edu_profile.get("semester") or "—",
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

    # Don't carry over baseline.student personal fields (id/tutor/dob) — those
    # belong to the demo profile and would mislead other users.
    # Only fill in 'school' if EduPage didn't provide it.
    bs = baseline.get("student", {})
    es = merged.get("student", {})
    if not es.get("school") or es.get("school") == "—":
        es["school"] = bs.get("school", es.get("school"))
    merged["student"] = es

    # Attendance is not exposed by the public EduPage API. Show "no data" rather
    # than mixing in another student's demo attendance.
    merged["attendance"] = {
        "summary":   {"total_days": 0, "present_days": 0, "absent_days": 0},
        "breakdown": {"excused": 0, "unexcused": 0, "late_arrivals": 0},
        "monthly":   [],
        "log":       [],
    }
    merged["behaviour"]     = []
    merged["teacher_notes"] = []

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
