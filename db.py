"""
EduTrack — SQLite database layer.

This module is intentionally small and beginner-friendly.
It does three things:
  1. Defines the database schema (SCHEMA_SQL).
  2. init_db()              — creates the tables if they don't exist.
  3. seed_db_from_json()    — fills the tables from data/demo_data.json
                              (only when the database is empty).

Nothing in this file imports Flask or app.py, so adding it cannot
break the existing project. Wiring it into app.py is the NEXT step.

Usage from a terminal:
    python db.py            # creates data/edutrack.db and seeds it
"""

import json
import os
import sqlite3

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "data", "edutrack.db")
JSON_PATH = os.path.join(BASE_DIR, "data", "demo_data.json")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS students (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    class_name   TEXT,
    school       TEXT,
    school_year  TEXT,
    semester     TEXT
);

CREATE TABLE IF NOT EXISTS subjects (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id       INTEGER NOT NULL,
    name             TEXT NOT NULL,
    teacher          TEXT,
    final_grade      TEXT,
    lessons_per_week INTEGER DEFAULT 0,
    absences         INTEGER DEFAULT 0,
    behaviour        INTEGER DEFAULT 0,
    color            TEXT,
    FOREIGN KEY (student_id) REFERENCES students(id)
);

CREATE TABLE IF NOT EXISTS assessments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id  INTEGER NOT NULL,
    type        TEXT,
    title       TEXT,
    score       REAL,
    month       TEXT,
    FOREIGN KEY (subject_id) REFERENCES subjects(id)
);

CREATE TABLE IF NOT EXISTS monthly_trends (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id  INTEGER NOT NULL,
    month       TEXT,
    average     REAL,
    FOREIGN KEY (subject_id) REFERENCES subjects(id)
);

CREATE TABLE IF NOT EXISTS attendance_monthly (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id    INTEGER NOT NULL,
    month         TEXT,
    present       INTEGER DEFAULT 0,
    absent        INTEGER DEFAULT 0,
    excused       INTEGER DEFAULT 0,
    unexcused     INTEGER DEFAULT 0,
    late_arrivals INTEGER DEFAULT 0,
    FOREIGN KEY (student_id) REFERENCES students(id)
);
"""


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row access by column name."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    """Create all tables if they don't exist yet."""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def _database_is_empty(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) AS n FROM students").fetchone()
    return row["n"] == 0


def seed_db_from_json(json_path: str = JSON_PATH) -> None:
    """
    Load demo_data.json and insert it into the database.

    Safe to call repeatedly: if the database already has a student row,
    this function does nothing.
    """
    if not os.path.exists(json_path):
        print(f"[seed] JSON file not found: {json_path} — skipping.")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    conn = get_connection()
    try:
        if not _database_is_empty(conn):
            print("[seed] Database already has data — skipping seed.")
            return

        student = data.get("student", {})
        cur = conn.execute(
            """
            INSERT INTO students (name, class_name, school, school_year, semester)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                student.get("name"),
                student.get("class"),
                student.get("school"),
                student.get("school_year"),
                student.get("semester"),
            ),
        )
        student_id = cur.lastrowid

        for subject in data.get("subjects", []):
            cur = conn.execute(
                """
                INSERT INTO subjects
                    (student_id, name, teacher, final_grade,
                     lessons_per_week, absences, behaviour, color)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    student_id,
                    subject.get("name"),
                    subject.get("teacher"),
                    subject.get("final_grade"),
                    subject.get("lessons_per_week", 0),
                    subject.get("absences", 0),
                    subject.get("behaviour", 0),
                    subject.get("color"),
                ),
            )
            subject_id = cur.lastrowid

            for item in subject.get("assessments", []):
                conn.execute(
                    """
                    INSERT INTO assessments (subject_id, type, title, score, month)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        subject_id,
                        item.get("type"),
                        item.get("title"),
                        item.get("score"),
                        item.get("month"),
                    ),
                )

            for item in subject.get("monthly_trend", []):
                conn.execute(
                    """
                    INSERT INTO monthly_trends (subject_id, month, average)
                    VALUES (?, ?, ?)
                    """,
                    (subject_id, item.get("month"), item.get("average")),
                )

        for item in data.get("attendance", {}).get("monthly", []):
            conn.execute(
                """
                INSERT INTO attendance_monthly
                    (student_id, month, present, absent,
                     excused, unexcused, late_arrivals)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    student_id,
                    item.get("month"),
                    item.get("present", 0),
                    item.get("absent", 0),
                    item.get("excused", 0),
                    item.get("unexcused", 0),
                    item.get("late_arrivals", 0),
                ),
            )

        conn.commit()
        print(f"[seed] Seeded database from {json_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    seed_db_from_json()
    print(f"[done] Database ready at {DB_PATH}")
