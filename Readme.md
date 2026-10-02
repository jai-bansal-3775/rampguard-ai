# RampGuard AI: Phase 1 (Local Setup)

Context-aware ground-handling safety intelligence for airport ramp operations.

RampGuard AI looks at a ramp image, uses **Google Gemini** to describe what is visible (vehicles, people, obstructions, estimated distances), then applies **deterministic safety rules** that take the current turnaround phase and stand clearance into account. When a rule fires, it creates an explainable safety event that a supervisor can review, investigate, and resolve, with an AI **Safety Copilot** to help.

> **Important:** RampGuard AI is decision support only. It does not control equipment and does not replace trained personnel. Final decisions stay with qualified humans.

This README covers **Phase 1, running locally on your computer**. Cloud deployment is not covered here.

---

## 1. What Phase 1 includes

| Area | What you get |
|---|---|
| Operations dashboard | 5 seeded aircraft turnarounds, open-event metrics, high-priority panel, severity chart |
| Analyze evidence | Upload a ramp image; Gemini returns structured observations; rules decide if an event is raised |
| Rules engine | SE-001 to SE-004, phase-aware severity, confidence handling |
| Explainable events | Every event stores the triggered rule, evidence, a "why flagged" summary, and suggested checks |
| Safety Copilot | 4 quick questions plus a free-text question per event |
| Event lifecycle | Detected, Under Review, Acknowledged, Investigating, Resolved or Dismissed, with full history |
| Analytics | Events by phase and stand, average acknowledgement time, recurring-risk alerts |
| Fallbacks | If Gemini fails or no image is uploaded, preset scenarios and template explanations keep the app working |

**Not in Phase 1:** rules SE-005 to SE-007, real database hosting, user login, and cloud deployment.

---

## 2. Project structure

```
rampguard/
├── app.py             # Streamlit user interface (4 tabs)
├── rules.py           # Safety rules engine (no AI, fully deterministic)
├── ai.py              # Gemini calls: image analysis, explanations, Copilot, fallbacks
├── db.py              # SQLite database, seed data, event lifecycle
├── requirements.txt   # Python packages
├── Dockerfile         # For cloud deployment later (not used locally)
├── README.md          # This file
└── data/              # Created automatically on first run
    ├── rampguard.db   # SQLite database
    └── evidence/      # Uploaded images saved with events
```

---

## 3. Prerequisites

1. **Python 3.10 or newer** (3.12 recommended). Check with:
   ```bash
   python --version
   ```
   On some systems use `python3 --version` or `py --version`. If Python is missing, install it from https://www.python.org/downloads/. On Windows, tick **"Add Python to PATH"** during installation.
2. **A Gemini API key** (free to create): see step 5 below.
3. **A terminal**: Terminal on Mac/Linux, PowerShell or Command Prompt on Windows.
4. **A ramp image to test with** (JPG or PNG). Use a royalty-free photo of an aircraft on a stand with ground vehicles near it, for example from Pexels or Unsplash. You can also test with no image at all (see section 7).

---

## 4. Setup

### Step 1. Put the files in one folder
Create a folder named `rampguard` and place `app.py`, `rules.py`, `ai.py`, `db.py`, `requirements.txt`, and `Dockerfile` directly inside it (no subfolders).

### Step 2. Open a terminal in that folder
```bash
cd path/to/rampguard
```

### Step 3. Create a virtual environment
A virtual environment keeps this project's packages separate from the rest of your computer.
```bash
python -m venv venv
```

### Step 4. Activate it
```bash
# Mac / Linux
source venv/bin/activate

# Windows PowerShell
venv\Scripts\Activate.ps1

# Windows Command Prompt
venv\Scripts\activate.bat
```
You should now see `(venv)` at the start of your prompt.

> **Windows PowerShell error about "running scripts is disabled"?** Run this once, then activate again:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### Step 5. Install the packages
```bash
pip install -r requirements.txt
```
This installs Streamlit, google-genai, pandas, plotly, and pillow.

### Step 6. Get a Gemini API key
1. Go to https://aistudio.google.com and sign in with a Google account.
2. Click **Get API key**, then **Create API key**.
3. Copy the key.

### Step 7. Set the key in your terminal
Do this in the **same terminal window** where you will run the app.
```bash
# Mac / Linux
export GEMINI_API_KEY="paste_your_key_here"

# Windows PowerShell
$env:GEMINI_API_KEY="paste_your_key_here"

# Windows Command Prompt
set GEMINI_API_KEY=paste_your_key_here
```
The key lasts only for that terminal session. If you open a new terminal, set it again.

> **Never** paste the key into the code files or commit it to Git.

### Step 8. Start the app
```bash
streamlit run app.py
```
Your browser opens at **http://localhost:8501**. If it does not, open that address yourself. Look at the left sidebar: it should show `AI: API key · gemini-2.5-flash`.

To stop the app, press **Ctrl+C** in the terminal.

---

## 5. Configuration (optional environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | none | Your AI Studio key. Without it, the app uses fallbacks only |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Change this if your key has no access to that model |
| `RG_DB` | `data/rampguard.db` | Path of the SQLite database file |

---

## 6. How to use the app

The app has four tabs.

### Tab 1: Operations
Shows the number of active turnarounds, open events, High/Critical open events, and total events. Below that are the turnaround table (with open events per stand), a High-priority panel, and a severity chart.

### Tab 2: Analyze evidence
1. Choose an **aircraft / stand**. The phase and clearance fill in automatically.
2. Confirm or change the **turnaround phase**. This matters because the same scene can be safe in one phase and dangerous in another.
3. Confirm or change the **configured clearance (m)**.
4. Choose a **fallback / demo scenario**. It is used only if you upload no image or Gemini fails.
5. Optionally upload a **ramp image** (JPG or PNG).
6. Click **Analyze**.

The app then shows the observation source (`gemini` in green, or `preset/fallback` in yellow), the structured observations as JSON, and any events created, each with an ID such as `EV-61F696`.

### Tab 3: Events & Copilot
1. Filter by status and severity, then select an event.
2. Read the rule triggered, why it was flagged, suggested checks, and the evidence.
3. Use the **Safety Copilot** buttons (Why was this flagged? / What should I check? / Generate incident summary / Why is this high priority? / Show similar events) or type your own question.
4. Move the event through its lifecycle with the **Apply** button (see section 8).

### Tab 4: Analytics
Events by phase and by stand, average acknowledgement time, and recurring-risk alerts.

---

## 7. Quick demo (no image needed)

Use this to confirm everything works before testing with real images.

1. Go to **Analyze evidence**. Leave the first aircraft (UA146, C22, Baggage Handling) selected.
2. Set the scenario to **GSE proximity** and upload nothing. Click **Analyze**.
3. You should see `preset/fallback`, a baggage vehicle at 2 m, and a new **SE-002 Unsafe GSE proximity, High** event.
4. Change the phase to **Fueling** and analyze again. The same scene is now **Critical**.
5. Open **Events & Copilot**, select the event, and try the Copilot buttons.
6. Open **Analytics**. You should see a recurring-risk alert for stand C22.

### The three preset scenarios

| Scenario | What it simulates | Raises an event when |
|---|---|---|
| GSE proximity | Baggage vehicle 2.0 m from aircraft | Clearance is greater than 2.0 m (any phase) |
| Restricted personnel | Person in a restricted zone | Always. High if Boarding, Critical if Fueling, otherwise Medium |
| Obstruction | Parked belt loader blocking a path | Phase is Turnaround Completion or Pushback Preparation only |

---

## 8. Event lifecycle and rules for status changes

```
Detected -> Under Review -> Acknowledged -> Investigating -> Resolved
     \            \               \              \
      +------------+---------------+--------------+--> Dismissed
```

- **Detected** can go to Under Review or Dismissed.
- **Under Review** can go to Acknowledged or Dismissed.
- **Acknowledged** can go to Investigating, Resolved, or Dismissed.
- **Investigating** can go to Resolved or Dismissed.
- **Resolved** and **Dismissed** are final.

Enforced rules:
- **Resolving** requires a resolution text.
- **Dismissing a High or Critical event** requires a note.
- Every change is logged with time, actor (the name in the sidebar), and note.

---

## 9. Safety rules in Phase 1

Gemini only **observes**. These rules **decide**:

| Rule | Trigger | Severity |
|---|---|---|
| SE-001 Safety-zone intrusion | Person in restricted zone during **Fueling** | Critical |
| SE-002 Unsafe GSE proximity | Ground equipment closer than the configured clearance | High; **Critical** if phase is Fueling or distance is under half the clearance |
| SE-003 Obstruction | Obstruction during Turnaround Completion or Pushback Preparation | Medium; High in Pushback Preparation |
| SE-004 Restricted-area personnel | Person in restricted zone in any phase except Fueling | Medium; High during Boarding |

Confidence handling:
- Observations below **50%** confidence are ignored.
- Observations from **50% to 65%** get their severity lowered by one level (a review-priority aid, not a safety judgement).

---

## 10. Seed data

On first run the app creates:
- 5 turnarounds: UA146 (C22), SQ322 (A05), AI101 (B14), EK510 (C07), LH760 (A11).
- 11 historical events, **7 of them SE-002 at stand C22 during Baggage Handling**. This is what triggers the recurring-risk alert, which needs 3 or more matching events.

Recurring-risk alerts are worded as observed historical patterns, not determined causes.

**Reset everything:** stop the app, delete the `data/` folder, and start again. The app reseeds itself.

---

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| Banner says `preset/fallback` even with an image | Read the text in `scene_summary`: it contains the start of the real error. See the rows below |
| `Cannot send a request, as the client has been closed` | Use the latest `ai.py`, where one Gemini client is created once and reused. Then fully restart Streamlit (Ctrl+C, run again) |
| `API key not valid` or `403` | The key is missing or wrong in this terminal. Set it again (step 7) and restart |
| `404 model not found` | Set `GEMINI_MODEL` to a model your key can use (check AI Studio) |
| `429` or quota errors | Free-tier rate limit. Wait a minute and retry |
| `streamlit: command not found` | The virtual environment is not active. Repeat step 4 |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` again with the environment active |
| Port 8501 already in use | `streamlit run app.py --server.port 8502` |
| Changes to code not showing | Stop with Ctrl+C and start again |
| Gemini distance estimates look off | Normal. Distances from a single photo are estimates; try a clearer photo or compare against the clearance setting |

---

## 12. Git safety

If you put this project in Git, create a `.gitignore` containing:
```
__pycache__/
*.py[cod]
venv/
.venv/
.env
.env.*
*.key
*.pem
service-account*.json
data/
.streamlit/secrets.toml
.vscode/
.idea/
.DS_Store
```
Never commit an API key. If you do by mistake, revoke it in AI Studio and create a new one.

---

## 13. Limitations

- Distances and zone detection come from a single 2D image and are estimates.
- Only SE-001 to SE-004 exist so far.
- No login: the supervisor name in the sidebar is free text.
- SQLite is a single local file, suitable for a prototype only.
- This is a prototype and has not been validated for operational use.

---

## 14. Coming next

- **Phase 2:** rules SE-005 to SE-007, sample image set, prompt tuning with real images.
- **Phase 3:** hosted database, improved interface.
- **Phase 4:** cloud deployment on Google Cloud, demo script, and submission write-up.