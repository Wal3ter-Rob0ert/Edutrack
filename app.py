from flask import Flask, render_template, jsonify, send_file
import json
import os

from db import DB_PATH, get_connection, init_db, seed_db_from_json

app = Flask(__name__)

DEMO_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "demo_data.json")


def load_demo_data() -> dict:
    with open(DEMO_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_data_from_db() -> dict | None:
    """
    Read everything for the first student in the database and return a dict
    shaped exactly like demo_data.json. Returns None if the DB is missing
    or empty so the caller can fall back to JSON.
    """
    if not os.path.exists(DB_PATH):
        return None

    conn = get_connection()
    try:
        student_row = conn.execute("SELECT * FROM students LIMIT 1").fetchone()
        if not student_row:
            return None

        student_id = student_row["id"]

        student = {
            "name": student_row["name"],
            "class": student_row["class_name"],
            "school": student_row["school"],
            "school_year": student_row["school_year"],
            "semester": student_row["semester"],
        }

        attendance_rows = conn.execute(
            """
            SELECT month, present, absent, excused, unexcused, late_arrivals
            FROM attendance_monthly
            WHERE student_id = ?
            ORDER BY id
            """,
            (student_id,),
        ).fetchall()

        attendance_monthly = [dict(row) for row in attendance_rows]
        months = [row["month"] for row in attendance_monthly]

        total_present = sum(row["present"] for row in attendance_monthly)
        total_absent = sum(row["absent"] for row in attendance_monthly)
        attendance = {
            "summary": {
                "total_days": total_present + total_absent,
                "present_days": total_present,
                "absent_days": total_absent,
            },
            "breakdown": {
                "excused": sum(row["excused"] for row in attendance_monthly),
                "unexcused": sum(row["unexcused"] for row in attendance_monthly),
                "late_arrivals": sum(row["late_arrivals"] for row in attendance_monthly),
            },
            "monthly": attendance_monthly,
        }

        subject_rows = conn.execute(
            "SELECT * FROM subjects WHERE student_id = ? ORDER BY id",
            (student_id,),
        ).fetchall()

        subjects = []
        for s in subject_rows:
            assessment_rows = conn.execute(
                "SELECT month, type, title, score FROM assessments WHERE subject_id = ? ORDER BY id",
                (s["id"],),
            ).fetchall()
            trend_rows = conn.execute(
                "SELECT month, average FROM monthly_trends WHERE subject_id = ? ORDER BY id",
                (s["id"],),
            ).fetchall()

            subjects.append({
                "id": str(s["id"]),
                "name": s["name"],
                "teacher": s["teacher"],
                "color": s["color"],
                "absences": s["absences"],
                "behaviour": s["behaviour"],
                "final_grade": s["final_grade"],
                "lessons_per_week": s["lessons_per_week"],
                "assessments": [dict(row) for row in assessment_rows],
                "monthly_trend": [dict(row) for row in trend_rows],
            })

        return {
            "student": student,
            "months": months,
            "attendance": attendance,
            "subjects": subjects,
        }
    finally:
        conn.close()


def get_data() -> dict:
    """Prefer SQLite; fall back to demo_data.json if the DB is empty/missing."""
    data = load_data_from_db()
    if data is None:
        return load_demo_data()
    return data


# Letter grade → grade point. Quantum STEM School official scale.
#   A+ 95-100 = 4.0   A  90-94 = 4.0   A- 85-89 = 3.7
#   B+ 80-84  = 3.3   B  70-79 = 3.0   B- 65-69 = 2.7
#   C+ 60-64  = 2.3   C  50-59 = 2.0   C- 40-49 = 1.7
#   D+ 35-39  = 1.3   D  30-34 = 1.0   F  <30   = 0.0
LETTER_TO_POINT = {
    "A+": 4.0, "A":  4.0, "A-": 3.7,
    "B+": 3.3, "B":  3.0, "B-": 2.7,
    "C+": 2.3, "C":  2.0, "C-": 1.7,
    "D+": 1.3, "D":  1.0,
    "F":  0.0,
}

# Subjects with these final grades are excluded from GPA (e.g. PE = Pass).
NON_GPA_GRADES = {"Pass", "P", "Fail"}


def calculate_subject_average(subject: dict) -> float:
    """Return the simple average score for one subject."""
    assessments = subject.get("assessments", [])
    if not assessments:
        return 0.0

    total_score = sum(item.get("score", 0) for item in assessments)
    average = total_score / len(assessments)
    return round(average, 2)


def calculate_gpa(subjects: list[dict]) -> float:
    """
    School GPA — unweighted, on a 4.0 scale.

        GPA = sum(grade_point * lessons_per_week) / sum(lessons_per_week)

    Subjects with a non-letter grade ("Pass", "Fail") are excluded entirely,
    so PE / Pass-Fail courses don't dilute the GPA.
    """
    weighted_points = 0.0
    total_lessons = 0.0

    for subject in subjects:
        final_grade = subject.get("final_grade")
        if not final_grade or final_grade in NON_GPA_GRADES:
            continue

        point = LETTER_TO_POINT.get(final_grade)
        if point is None:
            continue

        lessons = subject.get("lessons_per_week", 0) or 0
        if lessons <= 0:
            continue

        weighted_points += point * lessons
        total_lessons += lessons

    if total_lessons == 0:
        return 0.0

    return round(weighted_points / total_lessons, 2)


def assign_risk_level(subject_average: float, absences: int) -> str:
    """Assign a simple risk level based on average score and absences."""
    if subject_average < 60 or absences >= 5:
        return "High"
    if subject_average < 75 or absences >= 3:
        return "Medium"
    return "Low"


def get_grade_trend_label(monthly_trend: list[dict]) -> str:
    """Return a simple trend label from the first and last monthly averages."""
    if len(monthly_trend) < 2:
        return "Stable"

    first_average = monthly_trend[0].get("average", 0)
    last_average = monthly_trend[-1].get("average", 0)

    if last_average > first_average:
        return "Improving"
    if last_average < first_average:
        return "Declining"
    return "Stable"


def build_analytics(data: dict) -> dict:
    """Return a clean analytics payload ready for Chart.js."""
    months = data.get("months", [])
    attendance = data.get("attendance", {})
    subjects_with_analytics = []
    risk_counts = {"Low": 0, "Medium": 0, "High": 0}
    subject_average_map = {}
    grade_trends = []
    absence_by_subject = []

    for subject in data.get("subjects", []):
        subject_average = calculate_subject_average(subject)
        risk_level = assign_risk_level(subject_average, subject.get("absences", 0))
        trend_label = get_grade_trend_label(subject.get("monthly_trend", []))

        subject_row = {
            **subject,
            "subject_average": subject_average,
            "risk_level": risk_level,
            "trend": trend_label,
        }
        subjects_with_analytics.append(subject_row)
        risk_counts[risk_level] += 1
        subject_average_map[subject["name"]] = subject_average
        absence_by_subject.append({
            "subject": subject["name"],
            "absences": subject.get("absences", 0),
        })
        grade_trends.append({
            "subject": subject["name"],
            "color": subject.get("color"),
            "data": [item.get("average", 0) for item in subject.get("monthly_trend", [])],
        })

    return {
        "student": data.get("student", {}),
        "months": months,
        "gpa": calculate_gpa(data.get("subjects", [])),
        "attendance": {
            "summary": attendance.get("summary", {}),
            "breakdown": attendance.get("breakdown", {}),
            "monthly": attendance.get("monthly", []),
        },
        "subjects": subjects_with_analytics,
        "risk_levels": risk_counts,
        "charts": {
            "grade_trends": {
                "labels": months,
                "datasets": grade_trends,
            },
            "attendance": {
                "labels": [item.get("month") for item in attendance.get("monthly", [])],
                "present": [item.get("present", 0) for item in attendance.get("monthly", [])],
                "absent": [item.get("absent", 0) for item in attendance.get("monthly", [])],
                "excused": [item.get("excused", 0) for item in attendance.get("monthly", [])],
                "unexcused": [item.get("unexcused", 0) for item in attendance.get("monthly", [])],
                "late_arrivals": [item.get("late_arrivals", 0) for item in attendance.get("monthly", [])],
            },
            "subject_averages": subject_average_map,
            "absences_by_subject": absence_by_subject,
            "risk_levels": risk_counts,
        },
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/data")
def api_data():
    raw = get_data()
    enriched = build_analytics(raw)
    return jsonify(enriched)


@app.route("/api/analytics")
def api_analytics():
    """Return processed analytics from the local JSON dataset."""
    raw = get_data()
    enriched = build_analytics(raw)
    return jsonify(enriched)


@app.route("/export/csv")
def export_csv():
    raw = get_data()
    enriched = build_analytics(raw)
    export_path = os.path.join(os.path.dirname(__file__), "student_analytics_export.csv")

    import csv
    with open(export_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["Subject", "Average Grade", "Risk Level", "Risk Score", "Trend", "Absences", "Behaviour Marks"])

        for subject in enriched.get("subjects", []):
            analysis = subject.get("analysis", {})
            writer.writerow([
                subject.get("name", ""),
                analysis.get("avg", ""),
                analysis.get("risk", ""),
                analysis.get("score", ""),
                analysis.get("trend", ""),
                subject.get("absences", 0),
                subject.get("behaviour", 0)
            ])

    return send_file(export_path, as_attachment=True, download_name="student_analytics_export.csv")


@app.route("/api/demo")
def api_demo():
    """Return processed analytics from the local demo dataset."""
    raw = get_data()
    enriched = build_analytics(raw)
    return jsonify(enriched)


if __name__ == "__main__":
    init_db()
    seed_db_from_json()
    app.run(debug=True, port=5001)