# EduTrack — Student Analytics Dashboard
### Hackathon MVP · Python / Flask / Chart.js

---

## What is EduTrack?

EduTrack pulls your raw EduPage data and transforms it into:
- 📊 Grade trend charts across all subjects
- 🚨 Automatic risk detection (Low / Medium / High)
- 💡 Human-readable insights ("Math grades are declining")
- 📅 Attendance tracking and absence correlation
- 🎯 Radar + distribution + trend line charts

---

## Quick Start (5 minutes)

```bash
# 1. Clone / unzip the project
cd edutrack

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
python app.py

# 5. Open browser
# http://localhost:5000
# → Click "Try demo first" to see it immediately
# → Or enter your EduPage school credentials
```

---

## Project Structure

```
edutrack/
├── app.py                  ← Flask backend + risk analysis engine
├── requirements.txt
├── data/
│   └── demo_data.json      ← Realistic demo dataset (fallback)
└── templates/
    ├── index.html          ← Login / landing page
    └── dashboard.html      ← Main analytics dashboard
```

---

## How the Risk Engine Works

Each subject gets a **risk score (0–100)**:

| Factor | Points |
|--------|--------|
| Average grade (1→0pts, 5→80pts) | up to 80 |
| High absences (2 pts each, max 15) | up to 15 |
| Behaviour marks (5 pts each) | variable |
| Declining recent trend | +10 |
| Improving recent trend | −5 |

**Risk levels:**
- 🟢 **Low** — score < 30
- 🟡 **Medium** — score 30–54
- 🔴 **High** — score ≥ 55

---

## EduPage Integration Notes

The app uses the open-source `edupage-api` Python library:
- No Selenium required — pure HTTP requests
- Login with school subdomain + username + password
- Pulls grades and absences automatically
- Falls back to demo data if connection fails

**Grade scale:** 1 (excellent) → 5 (fail) — Slovak/Czech system

---

## 5-Day Hackathon Battle Plan

### Day 1 — Foundation
- [ ] Set up Python venv, install Flask + edupage-api
- [ ] Confirm demo data loads at `localhost:5000`
- [ ] Test EduPage login with your own credentials
- [ ] Understand the risk scoring formula in `app.py`

### Day 2 — Customise & Polish
- [ ] Edit `demo_data.json` to match your school's real subjects
- [ ] Adjust the risk thresholds in `analyze_subject()` to fit your grade scale
- [ ] Add your school name and colours to the UI
- [ ] Take 10–15 screenshots for your presentation

### Day 3 — Presentation Content
- [ ] Create a Google Form survey (questions below)
- [ ] Share survey with 15–20 classmates
- [ ] Write the problem statement slide
- [ ] Prepare the "before EduTrack / after EduTrack" comparison

### Day 4 — Slides & Story
- [ ] Build presentation deck (structure below)
- [ ] Add real screenshot evidence
- [ ] Prepare live demo walkthrough (3–4 minutes)
- [ ] Rehearse once with a friend

### Day 5 — Final Polish
- [ ] Fix any bugs found in rehearsal
- [ ] Record a 1-minute backup video demo
- [ ] Submit and present!

---

## Survey Questions (Google Forms)

**Title:** "How do you track your school performance?"

1. How often do you check your grades on EduPage? *(1–2× per day / Weekly / Rarely / Never)*
2. Do you know which of your subjects is currently at risk of a bad final grade? *(Yes, clearly / Somewhat / No)*
3. Have you ever been surprised by a low grade you didn't see coming? *(Yes / No)*
4. Do you find EduPage's gradebook easy to interpret trends from? *(1 = Very hard, 5 = Very easy)*
5. Would a dashboard showing risk warnings and grade trends help you study better? *(Definitely / Probably / Not sure / No)*
6. What information do you wish EduPage showed more clearly? *(Open text)*

**Share with:** 20 classmates → aim for 15+ responses

---

## Presentation Structure (10–12 slides)

**Slide 1 — Title**
"EduTrack: See your academic risk before it's too late"

**Slide 2 — The Problem**
"EduPage shows you raw grades. But it doesn't tell you when you're in danger."
→ Show your survey results (% who didn't know their at-risk subjects)

**Slide 3 — The Pain Points**
- Students can't spot trends from a table of numbers
- Parents see grades only at report card time
- Teachers only notice when it's too late

**Slide 4 — Our Solution**
"EduTrack: Connected directly to EduPage, it transforms your data into clear visual warnings."

**Slide 5 — Live Demo** (switch to browser)
Walk through: login → overview → click a high-risk subject → show insights

**Slide 6 — How It Works (Technical)**
Architecture diagram: EduPage → edupage-api → Flask → risk engine → Chart.js dashboard

**Slide 7 — Risk Detection Algorithm**
Show the scoring table. Explain: "Not just average grade — we factor in attendance and trend."

**Slide 8 — Key Features**
- Grade trend lines across time
- Risk scoring (Low/Medium/High)
- Natural language insights
- Attendance correlation
- Works with real EduPage data

**Slide 9 — Survey Evidence**
"We asked 20 students:" → bar charts of key responses

**Slide 10 — Impact & Future**
- Next: teacher dashboard, push notifications, parent portal
- Could be adopted school-wide at no cost (open source)

**Slide 11 — The Team**
Names, roles

**Slide 12 — Try It**
QR code to localhost demo / GitHub link / "Thank you"

---

## Tips for Judges

- **Lead with the problem, not the tech.** Judges care about impact.
- **Survey data is gold.** Even 15 responses with 80% saying "I didn't know my at-risk subjects" is compelling.
- **Show, don't tell.** The live demo should take 3–4 minutes of your presentation.
- **The risk engine is your differentiator.** Emphasise that EduTrack *analyses* data, not just displays it.
- **Mention the fallback.** "Even without EduPage login, the demo works immediately" shows robustness.

---

## Customisation Cheatsheet

| What to change | Where |
|----------------|-------|
| Grade scale (1–5 vs 1–10) | `analyze_subject()` in `app.py` |
| Risk thresholds | `score >= 55` / `score >= 30` lines in `app.py` |
| Subject colours | `demo_data.json` → each subject's `"color"` |
| Demo student name | `demo_data.json` → `"student"` block |
| Monthly attendance data | `demo_data.json` → `"attendance.monthly"` |
| Dashboard accent colour | `--accent: #7c6af7` in `dashboard.html` CSS |
