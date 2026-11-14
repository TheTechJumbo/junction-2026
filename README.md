# Current implementation entry point

JUN-19 supersedes the historical product plan below. For Bartek’s implemented local application,
start with [docs/TEAM_INTEGRATION.md](docs/TEAM_INTEGRATION.md) for team wiring and CI,
[docs/HANDOFF.md](docs/HANDOFF.md) for the initial verification record,
[contracts/API.md](contracts/API.md) for interfaces and [docs/DECISIONS.md](docs/DECISIONS.md)
for unresolved team decisions. The historical plan below is retained for reference;
profiles and confirmed ingestion/locking claims are not implemented.

# junction-2026

**A safety net for people with memory problems, and for the families who look out for them.**

Meta is building memory into AI glasses. We built the safety net around it. The person wears the
glasses and gets help in the moment: "You already took your morning pills at 08:00." A few cheap
home sensors make sure the important things are never missed. Their family gets a calm daily
summary, and an alert only when something is really off. Each condition has its own **profile**
(dementia, Parkinson's, after a stroke), so the same system follows the rules that matter for it.
It supports people; it does not diagnose them.

## The problem

- **Medication goes wrong at home.** In one study of 615 older people living at home who were at
  risk of moving into care, 49% had at least one medication problem
  ([summary](https://jhm.qa00.mdedge.com/psychiatry/article/21697/geriatrics/nearly-half-elderly-may-have-prescription-errors)).
  With memory problems, the classic risks are a forgotten dose and a double dose.
- **Some conditions need precise timing.** Parkinson's UK advises taking Parkinson's medication
  within 30 minutes of the prescribed time. Late or missed doses can worsen walking, talking and
  swallowing ([Parkinson's UK](https://www.parkinsons.org.uk/news/2025/campaign-success-new-nhs-england-programme-launches-to-get-medication-delivered-time)).
- **Families worry from a distance.** Did Mum take her pills? Did she lock the door? Why was the
  front door open at 3 a.m.? Today they find out by phoning, guessing, or too late.
- **Meta is exploring memory help, but not the care side.** Meta's own work with veterans with memory loss found that
  glasses could help remember tasks and where things were left, and one participant mentioned
  forgetting medication ([Meta, Feb 2026](https://about.fb.com/news/2026/02/helping-people-with-memory-loss-with-ai-glasses/)).
  That work is about the wearer. Nobody connects it to the family, the medication rules or the
  home.

## What it does

| Side | What it is | Built with |
| --- | --- | --- |
| **The person** | Glasses that answer "Did I lock the door?" or "Did I take my pills?" with the moment as evidence, and step in at the right time: "You already took them at 08:00", "It's 3 a.m., do you want to go back to bed?" | Meta glasses app, phone as fallback |
| **The home** | Sensors as ground truth, because a camera can miss things: pill-box lid, front door, motion. Stretch: a dispenser that releases only the scheduled dose | Arduino UNO Q + Modulino |
| **The family / carer** | Today against a normal day, a plain-language daily summary, and alerts only when something's off | Web dashboard |

**Condition profiles** set the rules for each person:

| Profile | What it watches for |
| --- | --- |
| **Everyone** | Door, hob, keys, vitamins: the autopilot moments you forget |
| **Dementia** (main demo) | Double or missed doses, the front door opening at night, a missing morning routine; daily summary to family |
| **Parkinson's** (demo switch) | Doses on time: a green/red due/late prompt and an alert after 30 minutes |
| Stroke, insulin diabetes (slides) | Blood-thinner doses and rehab check-ins; "Did I inject?" with a double-dose warning |

## What makes it different

| Already exists | This project |
| --- | --- |
| Meta's memory research and reported always-on tests: help for the wearer only | A safety net that connects the wearer, the home and the family |
| Smart pill dispensers: reminders, but no idea what's going on around them | Medication rules per condition, plus context from the glasses and the home |
| Camera monitoring: records everything, feels like surveillance | No cameras in the home: sensors, plus only key moments from the wearer's own glasses, with faces blurred and deleted after a day |

## Challenges we're targeting

| Challenge | Fit | Claim it when |
| --- | --- | --- |
| **Meta: AI Glasses for Everyone, Everyday** (main) | Ask "Did I take my pills?" or "Where are my keys?" hands-free. The glasses answer from what the home recorded, and the display points the way to the pill box or the keys. | Now. Meta also asks for a deck. |
| MLH: Best Use of ElevenLabs | Every answer and reminder is spoken in one calm voice, with the phone's voice as fallback. | Once a live call with a real key works. |
| Google DeepMind: Best Use of Gemini API | Answers open questions about the day from the event log and writes the family's daily note (text only, no photos). | Once Gemini does that in the demo. Participants must be 18 or older. |
| Arduino: Best Use of Arduino UNO Q | The pill-box sensor and the household store run on the UNO Q, and photos and 3D data stay on it. The 3D model also runs on the board, slowly. | Once a real pill-box sensor works (the simulator alone does not count). |
| Aiven: Best Use of Aiven | Event records (never photos) are copied to Aiven PostgreSQL, and the home keeps working when the link drops. | Once it is connected. |
| Verda: Verda Compute Track | The 3D model behind directions and lost items runs on a Verda GPU in Europe; it does not fit on a laptop. | Once the GPU worker runs on Verda. |
| OpenAI: Best Use of Codex | Codex agents built the event API, answer engine, dashboard and Android glasses app. | Once Bartek confirms. |

Dropped: Cloudinary. We don't use it: faces are blurred locally and photos stay local by design.

Tech challenges stack on top of the main challenge. Only claim the ones we genuinely use.
The Arduino tracks also need a post on Arduino Project Hub with the tag "Junction Finland 2026".
Facts per challenge: `hackathon-context/challenges.md`. Plan per track: `hackathon-context/track-plan.md`.
Full list: <https://junction2026.com/challenges>.

## How it works

1. **Moments.** While the glasses are worn, they grab a frame every few seconds. Gemini keeps only
   key actions (pills taken, door locked, keys put down, hob turned off) as events with a photo.
   No video is stored.
2. **Sensors.** The UNO Q posts pill-box, door and motion events.
3. **Rules.** The person's profile turns events into prompts ("already taken"), timing checks
   ("due" or "late") and alerts ("door open at night").
4. **Normal day.** A baseline per time of day is learned from past days (simulated for the demo),
   so the system can flag "no kitchen activity by 10:30".
5. **Ask.** "Did I…?" and "Where's my…?" questions are answered from the log, with the evidence
   moment.
6. **Family.** A dashboard with the timeline, alerts, a daily summary from Gemini, and the profile
   switch.

### Shared formats (agree these first, in JUN-6)

```jsonc
// Event: from the glasses, the UNO Q, or the rules engine
{ "id": "e17", "ts": "2026-11-14T08:00:00Z", "kind": "action" | "sensor" | "alert",
  "label": "pills_taken" | "door_locked" | "keys_down" | "pillbox_open" | "door_open" | "double_dose_prevented",
  "source": "glasses" | "uno-q" | "rules", "evidence": { "imageUrl": "…" } | null }

// Profile: one per person, picked from a template
{ "id": "dementia", "person": "Anna",
  "meds": [{ "name": "morning pills", "times": ["08:00", "20:00"], "windowMin": 30, "minGapH": 6, "maxPerDay": 2 }],
  "alerts": [{ "rule": "door_open_between", "from": "23:00", "to": "06:00", "notify": ["Sara (daughter)"] }],
  "summaryTo": ["Sara (daughter)"] }

// POST /events     Event (from the UNO Q or the glasses)
// POST /ask        { "question": "Did I lock the door?" } -> { "answer": "Yes, at 08:14.", "evidence": [Event] }
// GET  /timeline   ?day=… -> { "events": [Event], "baseline": …, "alerts": [Event] }
// GET  /summary    ?day=… -> { "text": "Anna had a normal morning…" }
// POST /speak      { "text": "…" } -> audio
```

### Workstreams (Linear team JUN, labelled per `coord/PROTOCOL.md`)

| Owner | Issue | Done when |
| --- | --- | --- |
| all | JUN-6 Contracts: agree the formats above | Everyone has commented "ok" or proposed a change |
| Ed (`human-needed`) | JUN-7 Which Meta glasses we get, and SDK access | We know whether we demo on real glasses or a phone stand-in |
| Seb | JUN-8 Moments and answers: key actions from frames (Gemini), "did I…?" with evidence | "Did I take my pills?" returns the 08:00 moment |
| Seb | New: Normal day, alerts and daily summary, all driven by the profile | The simulated week gives a baseline; double dose, night door and late Parkinson's dose all alert |
| Seb | JUN-10 Voice questions: transcribe short spoken questions | "Did I lock the door?" comes through reliably |
| Seb (stretch) | JUN-9 Nod to confirm a prompt | Only once everything else works |
| Bartek | JUN-11 API: events, profiles, timeline, ask, summary on one HTTPS URL (Aiven PostgreSQL) | The glasses app, the UNO Q and the dashboard can all reach it |
| Bartek | JUN-12 `/speak` with ElevenLabs | Prompts play within about 1 s |
| Bartek | New: Privacy: blur faces in stored moments (Cloudinary), delete moments after 24 h | No unblurred bystander faces are stored |
| Ayman | JUN-13 UNO Q sensors: pill-box lid and front door (motion is a stretch) | Opening the pill box or the door shows up on the timeline |
| Ed | JUN-14 Glasses app: capture moments, ask questions, play prompts (phone fallback) | The demo's person-side steps all work |
| Ed | JUN-15 Family dashboard with profile switch, pitch and demo | A rehearsed demo plus a backup video |
| Ed (`human-needed`) | JUN-16 One quote from a carer, nurse, or dementia or Parkinson's organisation | The quote is in the pitch |

### Milestones

- **Fri night (M0):** formats agreed, glasses access known, skeletons running with dummy data.
- **Sat 12:00 (M1):** opening the pill box is logged; reaching for it again gets the "already
  taken" prompt; it shows on the dashboard.
- **Sat 20:00 (M2):** "Did I…?" answers with evidence, the night-door alert, the normal-day
  baseline, the daily summary, and the Parkinson's switch.
- **Sun morning (M3):** polish, rehearse, record a backup video. Check the official submission deadline.

### Demo script (about 2 minutes)

1. "Meet Anna, 78, with early dementia, who lives alone. Her daughter Sara lives an hour away."
2. Anna takes her pills at 08:00, logged by the pill box and the glasses.
3. At 08:40 she reaches for them again. The glasses say: "You already took your morning pills at
   08:00." Sara's dashboard shows "double dose prevented".
4. Anna asks "Did I lock the door?" and hears "Yes, at 08:14", with the moment as evidence.
5. A simulated 03:10: the front door opens. Sara gets an alert, and Anna's glasses ask whether
   she wants to go back to bed.
6. Switch the profile to Parkinson's. The same pill box now shows due and late, and alerts after
   30 minutes.
7. The daily summary reads like a note from a nurse.
8. "It supports, it doesn't diagnose. There are no cameras in the home: just sensors and key moments
   from her own glasses, with faces blurred and deleted after a day."

## Privacy and consent

- Consent from the person, or their legal representative, and from whoever receives the alerts.
- No cameras in the home. Glasses capture only while worn, only key moments are kept, faces are
  blurred, and moments are deleted after 24 hours.
- Alerts go only to people the person has chosen.
- Framed as support, not diagnosis: medical-purpose software can be a regulated medical device in
  the EU, so this demo avoids clinical claims.

## What exists already

Day-1 prototypes are on branch `feat/rescue-ping-hud` (not merged): live Gaussian-splat scanning
on a phone (`demo/live-splat.html`) and a glasses-style pointer display (`demo/index.html`). They're
not part of this plan, but the splat scan could later show where in the home an event happened.

## Team coordination

`coord/` holds the Linear coordination kit. Start with `coord/BOOTSTRAP.md`.
