from flask import Flask, render_template, jsonify, send_file
import json
import os

app = Flask(__name__)

DEMO_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "demo_data.json")


def load_demo_data() -> dict:
    with open(DEMO_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


ASSESSMENT_WEIGHTS = {
    "FA": 0.3,
    "SA": 0.5,
    "SAQ": 0.2,
}


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
    Calculate overall GPA using simple weighted assessment types.
    FA = 30%, SA = 50%, SAQ = 20%
    """
    weighted_total = 0.0
    total_weight = 0.0

    for subject in subjects:
        for item in subject.get("assessments", []):
            weight = ASSESSMENT_WEIGHTS.get(item.get("type"), 0)
            score = item.get("score", 0)
            weighted_total += score * weight
            total_weight += weight

    if total_weight == 0:
        return 0.0

    gpa = weighted_total / total_weight
    return round(gpa, 2)


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
    raw = load_demo_data()
    enriched = build_analytics(raw)
    return jsonify(enriched)


@app.route("/api/analytics")
def api_analytics():
    """Return processed analytics from the local JSON dataset."""
    raw = load_demo_data()
    enriched = build_analytics(raw)
    return jsonify(enriched)


@app.route("/export/csv")
def export_csv():
    raw = load_demo_data()
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
    raw = load_demo_data()
    enriched = build_analytics(raw)
    return jsonify(enriched)


if __name__ == "__main__":
    app.run(debug=True, port=5001)