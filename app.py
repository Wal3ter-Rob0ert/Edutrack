"""
EduTrack — Flask backend.

Single source of truth for ALL data and ALL calculations.
Frontend (templates/index.html) is pure presentation: it fetches one
endpoint (/api/analytics) and renders. Frontend computes nothing.

Data sources, in priority order:
    1. EduPage live data       (when the user is logged in)
    2. demo_data.json baseline (fallback when EduPage is unreachable)
"""

import csv
import json
import os
import time

from flask import Flask, jsonify, render_template, send_file, request, session, redirect, url_for
from functools import wraps

from edupage_loader import (
    is_edupage_configured,
    load_data_from_edupage,
    merge_with_baseline,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

DEMO_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "demo_data.json")
# CSV export goes to /tmp so deployments with read-only app dirs still work.
EXPORT_PATH = os.path.join("/tmp" if os.path.isdir("/tmp") else os.path.dirname(__file__),
                           "student_analytics_export.csv")


# ─── Authentication ────────────────────────────────────────────────────────────

def login_required(f):
    """Decorator: redirect to login if not authenticated."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def get_session_credentials() -> tuple[str, str, str]:
    """Get credentials from session, fallback to .env for single-user mode."""
    if "user" in session:
        return (session["user"]["username"], session["user"]["password"], session["user"]["subdomain"])
    # Fallback to .env (for single-user/demo mode)
    return (
        os.environ.get("EDUPAGE_USERNAME", ""),
        os.environ.get("EDUPAGE_PASSWORD", ""),
        os.environ.get("EDUPAGE_SUBDOMAIN", ""),
    )


# ─── Grading scale (Quantum STEM School) ────────────────────────────────────
LETTER_TO_POINT = {
    "A+": 4.0, "A":  4.0, "A-": 3.7,
    "B+": 3.3, "B":  3.0, "B-": 2.7,
    "C+": 2.3, "C":  2.0, "C-": 1.7,
    "D+": 1.3, "D":  1.0,
    "F":  0.0,
}
NON_GPA_GRADES = {"Pass", "P", "Fail"}


# ─── Data loaders ───────────────────────────────────────────────────────────

def load_demo_data() -> dict:
    """Read the canonical JSON baseline. Always works."""
    with open(DEMO_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── EduPage cache (per-user, to avoid re-login on every request) ────────────
_edupage_cache: dict = {}
EDUPAGE_CACHE_SECONDS = 300


def _get_cache_key() -> str:
    """Create cache key from current session user."""
    if "user" in session:
        return f"user:{session['user']['username']}"
    return "default"


def _get_edupage_cached():
    """Fetch EduPage with a 5-minute cache. Returns None if disabled/failed."""
    username, password, subdomain = get_session_credentials()
    if not (username and password and subdomain):
        return None

    cache_key = _get_cache_key()
    now = time.time()

    if cache_key not in _edupage_cache:
        _edupage_cache[cache_key] = {"data": None, "fetched_at": 0.0, "tried": False}

    cache = _edupage_cache[cache_key]
    age = now - cache["fetched_at"]

    if cache["data"] is not None and age < EDUPAGE_CACHE_SECONDS:
        return cache["data"]
    if cache["tried"] and age < 60:
        return None

    print(f"[edupage] Fetching fresh data for {username}...")
    data = load_data_from_edupage(username, password, subdomain)
    cache["data"] = data
    cache["fetched_at"] = now
    cache["tried"] = True
    return data


def get_data() -> tuple[dict, str]:
    """Return (raw_data_dict, source_label). Source is one of: edupage, json."""
    baseline = load_demo_data()
    edupage = _get_edupage_cached()
    if edupage is not None:
        return merge_with_baseline(edupage, baseline), "edupage"
    return baseline, "json"


# ─── Per-subject calculations ───────────────────────────────────────────────

# EduPage / Quantum STEM weighted formula by assessment category.
# Group grades by category, average each, then weight: FA=25, SA=25, Midterm=50.
_TYPE_TO_CATEGORY = {
    "FA":  "FA",          # Формативное оценивание
    "SAQ": "FA",          # legacy demo type → treat as formative
    "SA":  "SA_SECTION",  # Суммативное за раздел (СОР / БЖБ)
    "Midterm":     "SA_SEMESTER",  # Суммативное за семестр (СОЧ / ТЖБ)
    "SA_SEMESTER": "SA_SEMESTER",
}
_CATEGORY_WEIGHTS = {"FA": 25, "SA_SECTION": 25, "SA_SEMESTER": 50}


def calculate_category_breakdown(subject: dict) -> list[dict]:
    """EduPage-style per-category breakdown: avg + weight + the scores that fed it.
    Mirrors the mobile app's 'Среднее по категориям' panel."""
    by_cat: dict[str, list[float]] = {}
    for a in subject.get("assessments", []):
        cat = _TYPE_TO_CATEGORY.get(a.get("type", "FA"), "FA")
        by_cat.setdefault(cat, []).append(float(a.get("score", 0)))

    labels = {"FA": "Formative (ФО)", "SA_SECTION": "Summative — Section (СОР)",
              "SA_SEMESTER": "Summative — Semester (СОЧ / Midterm)"}
    out = []
    for cat in ("FA", "SA_SECTION", "SA_SEMESTER"):
        scores = by_cat.get(cat, [])
        if not scores:
            continue
        out.append({
            "category": cat,
            "label":    labels[cat],
            "weight":   _CATEGORY_WEIGHTS[cat],
            "average":  round(sum(scores) / len(scores), 1),
            "count":    len(scores),
            "scores":   [round(s, 1) for s in scores],
        })
    return out


def calculate_subject_average(subject: dict) -> float:
    """Weighted average matching EduPage's display:
       Σ(weight × category_avg) / Σ(weight)."""
    items = subject.get("assessments", [])
    if not items:
        return 0.0

    by_cat: dict[str, list[float]] = {}
    for a in items:
        cat = _TYPE_TO_CATEGORY.get(a.get("type", "FA"), "FA")
        by_cat.setdefault(cat, []).append(float(a.get("score", 0)))

    weighted_sum, weight_sum = 0.0, 0.0
    for cat, scores in by_cat.items():
        w = _CATEGORY_WEIGHTS.get(cat, 0)
        if w == 0 or not scores:
            continue
        weighted_sum += w * (sum(scores) / len(scores))
        weight_sum += w

    return round(weighted_sum / weight_sum, 1) if weight_sum else 0.0


def assign_risk_level(avg: float, absences: int, trend: str) -> str:
    """High if very low avg or many absences, or both declining and below 75."""
    if avg < 65 or absences >= 5 or (avg < 75 and trend == "Declining"):
        return "High"
    if avg < 75 or absences >= 3:
        return "Medium"
    return "Low"


def get_trend_label(monthly_trend: list[dict]) -> str:
    if len(monthly_trend) < 2:
        return "Stable"
    delta = monthly_trend[-1].get("average", 0) - monthly_trend[0].get("average", 0)
    if delta >= 3:  return "Improving"
    if delta <= -3: return "Declining"
    return "Stable"


def calculate_gpa(subjects: list[dict]) -> float:
    """GPA = Σ(grade_point × lessons_per_week) / Σ(lessons_per_week). 4.0 scale.
    Subjects with non-letter grades (Pass/Fail) are excluded."""
    total_points, total_lessons = 0.0, 0.0
    for s in subjects:
        fg = s.get("final_grade")
        if not fg or fg in NON_GPA_GRADES:
            continue
        point = LETTER_TO_POINT.get(fg)
        lessons = s.get("lessons_per_week") or 0
        if point is None or lessons <= 0:
            continue
        total_points += point * lessons
        total_lessons += lessons
    return round(total_points / total_lessons, 2) if total_lessons else 0.0


# ─── Insights & risk flags (was duplicated in JS — now backend-only) ────────

def build_risk_flags_and_insights(subjects: list[dict], attendance: dict) -> tuple[list, list]:
    """Generate human-readable risk flags and insights for the dashboard.
    Each subject already has subject_average, risk_level, trend filled in."""
    risk_flags, insights = [], []

    for s in subjects:
        name, avg, absences = s["name"], s["subject_average"], s.get("absences", 0)
        risk, trend = s["risk_level"], s["trend"]
        monthly = [t.get("average", 0) for t in s.get("monthly_trend", [])]
        lo = monthly[0] if monthly else 0
        hi = monthly[-1] if monthly else 0

        if risk == "High":
            risk_flags.append({
                "subject": name, "level": "High",
                "reason": f"Average {avg}, {absences} absence{'s' if absences != 1 else ''} this semester.",
            })
            insights.append({
                "cls": "danger",
                "txt": f"{name} is flagged HIGH RISK — average {avg}, {absences} absence{'s' if absences != 1 else ''} this semester.",
            })
        elif trend == "Declining" and absences >= 2:
            risk_flags.append({
                "subject": name, "level": "Medium",
                "reason": f"Declining trend correlates with {absences} absences.",
            })
            insights.append({
                "cls": "warning",
                "txt": f"{name}: declining trend ({lo}→{hi}) correlates with {absences} absences.",
            })
        elif trend == "Declining":
            insights.append({"cls": "warning",
                             "txt": f"{name}: consistent downward trend — average has dropped to {avg}."})
        elif trend == "Improving":
            insights.append({"cls": "success",
                             "txt": f"{name}: positive trend — average improved from {lo} to {hi}."})

    unexcused_total = sum(1 for log_entry in attendance.get("log", [])
                          if log_entry.get("type") == "Unexcused")
    if unexcused_total >= 2:
        risk_flags.append({
            "subject": "Attendance", "level": "High",
            "reason": f"{unexcused_total} unexcused absences this semester.",
        })

    return risk_flags, insights


# ─── Top-level analytics builder ────────────────────────────────────────────

def build_analytics(data: dict) -> dict:
    """Return the full canonical API payload. Frontend just renders it."""
    months = data.get("months", [])
    attendance = data.get("attendance", {})

    enriched_subjects = []
    risk_counts = {"Low": 0, "Medium": 0, "High": 0}

    for s in data.get("subjects", []):
        avg = calculate_subject_average(s)
        trend = get_trend_label(s.get("monthly_trend", []))
        risk = assign_risk_level(avg, s.get("absences", 0), trend)
        risk_counts[risk] += 1
        enriched_subjects.append({
            **s,
            "subject_average":    avg,
            "risk_level":         risk,
            "trend":              trend,
            "category_breakdown": calculate_category_breakdown(s),
            # Flat list of monthly averages — convenient for Chart.js.
            "monthly":            [t.get("average", 0) for t in s.get("monthly_trend", [])],
        })

    risk_flags, insights = build_risk_flags_and_insights(enriched_subjects, attendance)

    return {
        "student":       data.get("student", {}),
        "months":        months,
        "gpa":           calculate_gpa(enriched_subjects),
        "subjects":      enriched_subjects,
        "attendance":    {
            "summary":   attendance.get("summary",   {"total_days": 0, "present_days": 0, "absent_days": 0}),
            "breakdown": attendance.get("breakdown", {"excused": 0, "unexcused": 0, "late_arrivals": 0}),
            "monthly":   attendance.get("monthly", []),
            "log":       attendance.get("log", []),
        },
        "behaviour":     data.get("behaviour", []),
        "teacher_notes": data.get("teacher_notes", []),
        "risk_levels":   risk_counts,
        "risk_flags":    risk_flags,
        "insights":      insights,
    }


# ─── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if "user" in session:
        return render_template("index.html")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = request.get_json()
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        subdomain = data.get("subdomain", "").strip()

        if not (username and password and subdomain):
            return jsonify({"error": "All fields are required"}), 400

        # Try to login with EduPage
        result = load_data_from_edupage(username, password, subdomain)
        if result is None:
            return jsonify({"error": "Invalid credentials or EduPage unreachable"}), 401

        # Store credentials in session
        session["user"] = {
            "username": username,
            "password": password,
            "subdomain": subdomain,
        }
        session.permanent = True
        app.permanent_session_lifetime = 86400 * 7  # 7 days

        return jsonify({"success": True}), 200

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True}), 200


@app.route("/api/analytics")
@login_required
def api_analytics():
    raw, source = get_data()
    payload = build_analytics(raw)
    payload["source"] = source
    return jsonify(payload)


@app.route("/api/source")
@login_required
def api_source():
    cache_key = _get_cache_key()
    cache = _edupage_cache.get(cache_key, {})
    fetched_at = cache.get("fetched_at", 0.0)
    age = (time.time() - fetched_at) if fetched_at else None
    _, source = get_data()
    return jsonify({
        "source":              source,
        "edupage_configured":  True,
        "edupage_last_ok":     cache.get("data") is not None,
        "edupage_cache_age_s": round(age, 1) if age else None,
    })


@app.route("/export/csv")
@login_required
def export_csv():
    raw, _ = get_data()
    payload = build_analytics(raw)
    student = payload["student"]
    with open(EXPORT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Student", "Class", "School", "Year", "Semester"])
        w.writerow([student.get("name"), student.get("class"),
                    student.get("school"), student.get("school_year"),
                    student.get("semester")])
        w.writerow([])
        w.writerow(["Subject", "Teacher", "Average %", "Final Grade", "Risk Level", "Trend"])
        for s in payload["subjects"]:
            w.writerow([s["name"], s.get("teacher", "—"), s["subject_average"],
                        s.get("final_grade", "—"), s["risk_level"], s["trend"]])
    return send_file(EXPORT_PATH, as_attachment=True, download_name="student_analytics_export.csv")


if __name__ == "__main__":
    app.run(debug=True, port=5001)
