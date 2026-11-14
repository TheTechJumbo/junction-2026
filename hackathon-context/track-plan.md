# Track plan: which challenges to enter, and how

Written Sat 14 Nov 2026, early afternoon. Facts come from `challenges.md` (the official list) and
from the git branches. Everything else is judgement.

Labels used below:
- **[File]** a fact from the challenges file.
- **[Repo]** a fact from the code or docs on a branch (branch named).
- **[J]** my judgement. Hour estimates are judgement too.

## 1. What is actually built (the base for every decision)

| Part | State | Where |
| --- | --- | --- |
| Glasses display page: arrow, distance, mini map, questions, reminders, lost items | Works in a browser; **not run on glasses** | `ml/recon_change/live/hud.html` on `jun-22-hud-waypoints` (PR #24, open to `main`) |
| Position fix and directions (VGGT on a GPU) | Works live through the relay on Iridis: about 4 s on an L4, about 40 s on 32 CPU cores | `locate.py`, `live/relay.py`, `live/gateway.py`, `live/worker.py` |
| 3D change check, Gaussian splat view | Works on test clips; not tested on a real room of ours | `recon_change.py`, `splat.py`, `live/viewer.html` |
| Lost items (OWLv2) | Deployed but weak: keys found 7 of 56, no false answers on 324 empty photos | `items.py`, `eval/ITEMS_RESULTS.md` |
| Rust speed-up of the change check | Merged into the vision branch | `ml/recon_change/rust/` (PR #20, #27) |
| Event API, observation-only answers, medical refusal, family dashboard | Works locally, 467 tests pass | `backend/`, `dashboard/` (merged to `main` in PR #13, #16) |
| ElevenLabs `/speak` | Code and mocked tests only; **no live call made, no key set** | `backend/speech/elevenlabs.py` |
| Gemini | Adapter for "open" questions only, **off by default**, gets only the question text, **never called live** | `backend/answer_engine/adapters.py`, `docs/GEMINI_SETUP.md` |
| PostgreSQL replica | Code tested on a throwaway PostgreSQL; **never connected to Aiven** | `backend/storage/sqlite.py`, `deploy/README.md` |
| Face blur | **Local** (YuNet on CPU). No cloud service. "Image bytes never leave SQLite." | `backend/privacy/` |
| Android app with Meta Device Access Toolkit 1.0.1 (camera, display, speech) | Builds, 22 tests pass, ran on an emulator; **not run on glasses** | `android/` (merged to `main` in PR #21) |
| UNO Q household store | Runs on the board; no outbound network calls | `unoq/store.py` (merged to `main` in PR #6) |
| INT8 VGGT on the UNO Q | Runs, slowly: 2 frames in 271 s, 3 frames in 400 s, 9 frames timed out | `unoq/quant/` on `jun-13-quantized-vggt` (PR #9, open) |
| Pill box and door sensors | **Not built.** No MCU firmware. Ayman's Rust simulator sends fake openings | PR #26 `jun-13-rust-sensor-simulator`, open, stacked on PR #9 |

[Repo] Bartek decided on observation-only answers: "The pill box was opened at 08:00. I cannot
confirm that the pills were taken." (`docs/DECISIONS.md`). [J] The README's demo line "You already
took your morning pills at 08:00" no longer matches the product. Fix the pitch wording.

## 2. Verdict for every challenge

"Fit" = we use it now. "Cheap" = under about 3 hours on top of what exists. Verdicts are [J].

| # | Challenge | Verdict | One-line reason |
| --- | --- | --- | --- |
| 12 | **Meta: AI Glasses for Everyone, Everyday** | **Already a fit (main)** | The whole product is a first-person, hands-free use case |
| 28 | MLH: Best Use of ElevenLabs | Cheap (code exists) | Set a key and a voice, test on the phone |
| 36 | OpenAI: Best Use of Codex | Probably already a fit | Bartek's `codex/` branches and `AGENTS.md` suggest Codex built the backend and Android app; confirm |
| 38 | Verda: Verda Compute Track | Cheap | Move the GPU worker from Iridis to a Verda GPU machine |
| 21 | Arduino: Best use of Arduino UNO Q | Cheap, but only with a real sensor | Store and INT8 VGGT already run on the board; the pill-box sensor is missing |
| 26 | Google DeepMind: Best Use of Gemini API | Cheap to make genuine | Adapter exists; give Gemini a real job |
| 20 | Aiven: Best Use of Aiven | Cheap | Replica code exists; point it at an Aiven PostgreSQL |
| 25 | CRACI: Best use of CRACI | Cheap to qualify, unlikely to place | CI workflows exist; one-line runner change |
| 22 | Atech Real World Challenge | Cheap, as a fallback sensor | An Atech IMU or distance module on the pill-box lid |
| 27 | Google DeepMind: Best Use of Gemma | Possible but costly | Gemma on the UNO Q writing the daily note; speed unknown |
| 34 | Modal: Best use of Modal | Possible but costly | Same job as Verda; pick one |
| 35 | NVIDIA: Best Use of Nemotron | Possible but costly | Would need to be central; overlaps Gemini |
| 32 | MLH: Best Use of Tiger Data | Possible but costly | Same job as Aiven (PostgreSQL); pick one |
| 11 | Arduino: The Green European Renaissance | Does not fit (it is a main challenge) | Theme fits ("supporting elderly people's independence"), but our main is Meta |
| 14 | Aalto: Beyond the Clinic | Does not fit | Depression after TMS treatment, different users |
| 23 | Cloudinary: Best Use of Cloudinary | Does not fit | Our design keeps photos local and blurs them locally |
| 33 | MLH: Best Use of Vultr | Does not fit | Verda does the same job with a bigger prize |
| 24 | Context.dev: Fully Structured Web | Does not fit | No live web data in our core flow |
| 37 | Oxylabs Web API | Does not fit | Same; also needs a $500 plan (discounted) |
| 29 | MLH: Best Use of Entire | Does not fit | We coordinate with Linear and git; switching mid-event costs too much |
| 30 | MLH: Best Use of MongoDB Atlas | Does not fit | We already have SQLite plus PostgreSQL |
| 31 | MLH: Best Use of Solana | Does not fit | Nothing to put on a chain |
| 1 | Valio Aimo: Trusted Trade Agents | Does not fit | Wholesale purchasing agents |
| 2 | AfterQuery: Break the Agent | Does not fit | RL environment |
| 3 | Exa: Internet Butler | Does not fit | Web research agent (main challenge) |
| 4 | Hostinger: Decode the Noise | Does not fit | Propaganda and bot detection |
| 5 | Tieto: Earned Trust in AI Code | Does not fit | Code review concept |
| 6 | Bahn Express: Spread | Does not fit | Dispatch simulation |
| 7 | Cala: Make Unicorns Real | Does not fit | Founder and investor data |
| 8 | Ignite Next x Intel: Physical AI | Does not fit | Robot arm brick sorting |
| 9 | Rokoko: Robot Teleoperation | Does not fit | Motion-capture robotics |
| 10 | Aalto: Aaltonomous Humanoid | Does not fit | Humanoid block sorting |
| 13 | AstraZeneca: Equal Care Across Finland | Does not fit | Cancer care data analysis |
| 15 | Stoa: Make Blueprints Talk | Does not fit | Construction drawings |
| 16 | Context.dev: Real-Time Database | Does not fit | Live web data product |
| 17 | City of Espoo: Rethinking Recruitment | Does not fit | Recruitment matchmaking |
| 18 | MariaDB: Wikipedia Through Time | Does not fit | Wikipedia tools |
| 19 | S-Pankki: Personal Economic Trainer | Does not fit | Banking coach |

## 3. Plans for the tracks that fit or are cheap

### 3.1 Meta: AI Glasses for Everyone, Everyday (main). Owner: Ed, with Seb

**What the judge sees.** Anna, 78, early dementia, lives alone. Wearing the glasses she asks "Did I
take my pills?" and hears the pill-box observation, with its time. The display arrow points her to
the pill box when no opening is recorded. A reminder comes up when due and ends when the box is
opened. "Where are my keys?" gives where and when they were last seen, with an arrow. Her daughter's
dashboard shows the day. This is Seb's chosen demo: directions and pills, reminders, lost keys.

**Code.** `live/hud.html` (display), `live/relay.py` (questions, reminders, lost items),
`android/.../meta/MetaGlassesAdapter.kt` (DAT camera, display, speech), `backend/` (answers).

**Work.**
- Run it on real glasses. Book a Ray-Ban Display at the Meta booth [File: 10 shared]. Ask the web-app
  mentors whether `hud.html` can run as a glasses web app, or show it through the DAT app. 2-3 h, Ed.
- Accessibility pass on `hud.html` and the Android app: voice first, no typing needed, large
  high-contrast text, short answers, slower speech. 1-2 h, Ed.
- The deck [File: required]: problem, audience, "path to adoption or a pilot" (for example a home-care
  or memory-clinic pilot), consent, data flow. 2 h, Ed.
- One real room of ours: record the baseline, name the pill box and the key bowl. 1 h, Seb.

**Against Meta's criteria [File] (scoring is [J]).**
- Creativity and use case 25%: strong. A first-person position fix that points to the pill box or
  the keys is not phone mirroring.
- Technical execution 25%: strong if it runs on glasses; weak if only in a browser. The brief asks
  for handling of "permissions, errors, performance trade-offs, connectivity, and device state
  changes". We can show: DAT permission flow, "Iridis unreachable" recovery, GPU vs CPU timings,
  speech fallback when `/speak` fails.
- Real-world value 25%: good. Use the medication-problem figures already in the README, and the
  carer quote (JUN-16).
- User experience, accessibility and trust 25%: observation-only answers, refusal of dosing
  questions, and "the finder says nothing rather than guess" are strong trust points. Accessibility
  needs the pass above.

**Risk and fallback.** Risk: no glasses slot, or the display path fails. Fallback: phone on a stand
with `hud.html`, plus a recorded video on glasses. Risk: Iridis VPN drops (it dropped twice on
Friday night) and only Seb's laptop can reach it. Fallback: move the worker to Verda (3.4), and use
`/hud?demo=1`.

### 3.2 MLH: Best Use of ElevenLabs. Owner: Bartek

**What the judge sees.** Every answer and reminder is spoken in one calm, slow voice on the glasses.

**Code.** `backend/speech/elevenlabs.py` exists; the relay already passes `/speak` on.

**Work.** Claim the MLH code [File: mlh.link/junction-26]. Set `ELEVENLABS_API_KEY` and
`ELEVENLABS_VOICE_ID`. Pick a calm voice. Check it plays on the phone within the 800 ms limit. 0.5-1 h.

**Scoring.** [File] No criteria listed; the brief values "natural, human-sounding" and "emotionally
expressive" voices. [J] Low competition for an earbuds prize. It also helps Meta's UX score.

**Risk and fallback.** Latency over 800 ms gives a 503; the phone voice takes over (already built).

### 3.3 OpenAI: Best Use of Codex. Owner: Bartek

**What the judge sees.** A short note and a slide: Codex agents in isolated worktrees built the event
API, the answer engine, the dashboard and the Android DAT app, with 467 passing tests.

**Work.** Bartek confirms he used Codex (the `codex/` branch names and `AGENTS.md` suggest it, but
`AGENTS.md` also mentions Claude Code). Write the note. 1 h.

**Scoring.** [File] No criteria; "demonstrating how you've meaningfully used Codex". [J] Good value for
an hour: $10 000 API credits for 1st.

**Risk.** If most of it was not Codex, do not claim it.

### 3.4 Verda: Verda Compute Track. Owner: Seb

**What the judge sees.** The position fix, change check and item finder run on a Verda GPU in
Europe. The slide says why: the 5 GB model does not fit on our laptop [Repo: Seb's laptop has
2.4 GB free], and on the UNO Q two frames take 271 s.

**Code.** `live/worker.py` and `live/gateway.py` already run as plain Python; Slurm only starts them
(`iridis/worker.slurm`). `iridis/setup_env.sh` and `setup_items.sh` build the environment.

**Work.** Start a Verda GPU instance. Run the setup scripts there. Start `worker.py` and `gateway.py`
on the same machine. Point the relay's SSH connection at it. Keep Iridis as the second lane. Write
up the process (the HANDOFF already documents most of it) and post the link in Verda's Discord
[File: required]. 2-4 h.

**Against Verda's criteria [File] (scoring [J]).**
- Ambition 25%: strong. A 3D rebuild of the room for every question.
- Fit 25%: good. A GPU instance for a model kept loaded. Mention auto-scaling containers for many
  homes and batch jobs for overnight baseline rebuilds.
- Working prototype 25% ("is the process documented?"): strong; the benchmarks and HANDOFF exist.
- Path to scale 25%: needs one slide (one container per household, scale to zero at night).

**Extra benefit [J].** It removes the biggest demo risks: the university VPN, the 2-hour job cap and
"only Seb's laptop can reach the cluster". It also gives an honest privacy line: photos go to a GPU
in Europe that we control. Verda's own list includes "Keep sensitive data in Europe" [File].

**Risk and fallback.** GPU stock or setup time. Fallback: stay on Iridis and do not claim Verda.

### 3.5 Arduino: Best use of Arduino UNO Q. Owner: Ayman

**What the judge sees.** Anna opens the real pill box. A sensor on the lid tells the UNO Q. The
reminder on her glasses ends, and Sara's dashboard shows the opening. If the box is opened again
too soon, the Modulino Buzzer sounds [J: a cheap "react" moment]. The UNO Q also keeps the photos
and 3D data at home, and can run the 3D model itself (slowly).

**Code.** `unoq/store.py` (runs on the board), `backend/integrations/unoq_export.py` (board to central
API), `unoq/quant/` (INT8 VGGT), PR #26 (simulator; useful for rehearsal).

**Work.**
- A real lid sensor: a Modulino on the lid (ask the Arduino lab which one fits; the file lists Buzzer,
  Knob and Thermo for this track [File]), a small MCU sketch in App Lab, the Linux side posting
  `pillbox_open` to the store. 2-3 h.
- Set the board clock (it reads April 2026 [Repo]), or "today" answers break. 15 min.
- Publish on Arduino Project Hub with tag "Junction Finland 2026" [File: required]. 1 h.

**Against the criteria [File] (scoring [J]).** Use of Arduino hardware and Edge AI 50%: medium. A real
sensor plus INT8 VGGT on the board is a fair story; VGGT is too slow to use live, so say so.
Working prototype 30%: only good with a real sensor. Creativity 20%: medium.

**Risk and fallback.** Risk: the sensor is not done in time. Fallback: the Rust simulator for the
demo, and **do not claim this track**. A simulator is not "use of Arduino hardware".

### 3.6 Google DeepMind: Best Use of Gemini API. Owner: Bartek (Seb for the stretch)

**What it is today [Repo].** Off by default. Only "open" questions go to it, with no events or photos.
It has never been called live. [J] That is not a genuine use yet, and it is "simply adding an LLM",
which the brief says to go beyond [File].

**What the judge would see.** (a) Open questions like "What did I do this morning?" answered from the
day's events (text only), with the observation-only rules kept. (b) Sara's daily note written by
Gemini from the day's events, with the rules summary as fallback. Stretch (c): when OWLv2 does not
see the keys, Gemini looks at the same photo as a second opinion (keys are found 7 of 56 times now).

**Work.** (a) and (b): 2-3 h, Bartek, in `backend/answer_engine/adapters.py` and the `/summary` route
in `backend/app.py`. (c): 2-3 h, Seb, in `items.py` or the relay; it sends a photo to Google, so
say so in the consent slide.

**Scoring.** [File] No criteria listed; the submission must explain why Gemini matters. [J] Biggest
cash prize among the tech tracks (4000€) and probably the most entries. Only (c) is distinctive.

**Risk and fallback.** Latency and wrong output. Fallback: the rules answers (already built). Note:
participants must be 18 or older [File].

### 3.7 Aiven: Best Use of Aiven. Owner: Bartek

**What the judge sees.** Event metadata (never photos) is copied to Aiven PostgreSQL. Pull the
network: the home keeps working and the queue drains when Aiven is back.

**Work.** Create an Aiven PostgreSQL service ($50 free credits [File]). Set `DATABASE_URL`. Run the
existing PostgreSQL integration test against it. 1 h. Stronger version [J]: the family dashboard
reads from Aiven, so Sara sees the day without reaching the home. 2-3 h more.

**Scoring [File criteria, J scores].** Meaningful use 40%: modest for a replica, better if the
dashboard reads from it. Technical implementation 30%: good (outbox, idempotent replay, outage
handling). Impact 20%: modest. Creativity 10%: low.

**Risk.** Low. If it is not connected by the demo, do not claim it.

### 3.8 CRACI (optional). Owner: whoever has 1 h

Switch `.github/workflows/bartek-ci.yml` and `android-ci.yml` to `runs-on: craci`. [File] "Do not
just add runs-on: Craci ... and stop there"; scoring is technical depth 50% and creative use 50%.
[J] One idea: show the SBOM of the Android app and the UNO Q service in the trust slide ("what
software runs in Anna's home"). Low chance to place. Do it only if someone is idle.

### 3.9 Atech (fallback for the sensor). Owner: Ayman

If no Modulino fits the lid, an Atech IMU or distance module on the lid can send the opening over
Wi-Fi to the event API. [File] Atech lists "smart reminders for people living alone" as inspiration;
needs the hardware in the demo, a Discord post, a public repo and a short video. Kits are 2 to 4 per
team, first come. 2-3 h. [J] Doing this weakens the Arduino claim, so it is plan B, not both.

### 3.10 Gemma (only if Ayman has time after the sensor)

[J] A small Gemma model on the UNO Q writes Sara's daily note from the local event log, so the log
never leaves the home. It stacks with Arduino's Edge AI criterion. 3-4 h; speed on the board is
unmeasured. If done, give Gemini and Gemma different jobs (Gemma: daily note on the box; Gemini:
open questions), or claim only one.

## 4. Ranked shortlist (best value for effort first) [J]

| Rank | Track | Effort | Owner | Why here |
| --- | --- | --- | --- | --- |
| 1 | Meta (main) | 6-8 h | Ed (+ Seb 1 h) | Our main track; glasses on a real device and the deck decide it |
| 2 | ElevenLabs | 0.5-1 h | Bartek | Code done; also helps Meta's UX score |
| 3 | Codex | 1 h | Bartek | Probably already true; $10 000 credits for a write-up |
| 4 | Verda | 2-4 h | Seb | $10 000 credits, and it removes the VPN and single-laptop risk |
| 5 | Arduino UNO Q | 3-4 h | Ayman | Needed for the "real sensor" story anyway; Project Hub page required |
| 6 | Gemini | 2-3 h | Bartek | Biggest cash prize, but needs a real job first |
| 7 | Aiven | 1 h | Bartek | Cheap; modest score as a replica only |
| 8 | CRACI | 1 h | anyone | Qualifying is cheap; placing is unlikely |
| 9 | Gemma | 3-4 h | Ayman | Only after the sensor works |
| 10 | Atech | 2-3 h | Ayman | Only if the Arduino sensor fails |

Do not do both Verda and Modal, or both Aiven and Tiger Data: one would not be a genuine use.

## 5. Changes to the README's "Challenges we're targeting" table

| Row today | Change | Proposed wording (add only once it runs in the demo) |
| --- | --- | --- |
| Meta (main) | Keep, reword | **Meta: AI Glasses for Everyone, Everyday** (main): Ask "Did I take my pills?" or "Where are my keys?" hands-free. The glasses answer from what the home recorded, and the display points the way to the pill box or the keys. |
| Gemini | Keep only after 3.6 is built; reword | Google DeepMind: Best Use of Gemini API: Answers open questions about the day from the event log and writes the family's daily note (text only, no photos). |
| Arduino UNO Q | Keep only with a real sensor; reword | Arduino: Best Use of Arduino UNO Q: The pill-box sensor and the household store run on the UNO Q; photos and 3D data stay on it. The 3D model also runs on the board, slowly. |
| ElevenLabs | Keep, reword | MLH: Best Use of ElevenLabs: Every answer and reminder is spoken in one calm voice, with the phone's voice as fallback. |
| Aiven | Keep only once connected; reword | Aiven: Best Use of Aiven: Event records (never photos) are copied to Aiven PostgreSQL, and the home keeps working when the link drops. |
| Cloudinary | **Drop** | Not used. Faces are blurred locally and photos stay local by design. |
| (new) | Add after 3.4 | Verda: Verda Compute Track: The 3D model behind directions and lost items runs on a Verda GPU in Europe; it does not fit on a laptop. |
| (new) | Add if Bartek confirms | OpenAI: Best Use of Codex: Codex agents built the event API, answer engine, dashboard and Android glasses app. |

Keep the rule under the table: "Tech challenges stack on top of the main challenge. Only claim the
ones we genuinely use." Add: "The Arduino tracks also need a post on Arduino Project Hub."

## 6. Tracks we claim now but do not genuinely use

| Claimed | The truth today | Action |
| --- | --- | --- |
| Gemini: "spots key actions in glasses frames ... writes the daily summary" | Not built. Frames go to VGGT and OWLv2. The summary is rules-based. Gemini only gets open questions, is off by default, and has never been called. | Build 3.6 or drop |
| Arduino: "sensors are the ground truth; the debouncing runs on the device" | No sensor and no MCU code. The store and INT8 VGGT do run on the board. | Build 3.5 or drop |
| Aiven: "events, timeline and profiles in Aiven PostgreSQL" | Replica code only, never connected to Aiven. There are no profiles (JUN-19 dropped them). | Connect (1 h) or drop |
| ElevenLabs | Code only, no key, no live call. | Set a key (0.5 h) |
| Cloudinary: "blurring faces" | Not used at all. Blurring is local (YuNet). | Drop |

## 7. Where the challenges file disagrees with the plan or the brainstorm

- **No deadline in the file.** It gives no overall submission time. The README's M3 note ("check the
  official submission deadline") still stands.
- **Extra submissions.** Arduino needs a Project Hub post or there is no prize. Verda needs a Discord
  post and a documented process. Cloudinary needs a survey form. The README mentions none of these.
- **Meta asks for a deck** and for a prototype that handles permissions, errors, connectivity and
  device state. Accessibility is part of a 25% criterion.
- **Gemini: 18 or older** to take part.
- **One main challenge** is implied, not stated. Arduino's Green Renaissance fits our theme but is a
  main challenge, so we cannot also enter it next to Meta. Confirm with the organisers if wanted.
- **Brainstorm vs file.** The brainstorm says partners pick winners, then peer review picks a top 5,
  then a grand jury. The file only says peer review and judging happen on the Junction platform.
  The brainstorm's glasses limits (720p, 8 kHz audio, 2-minute Gemini Live, 30-60 min battery) are
  not in the file. It says Display glasses are hard to get in Finland; the file says Meta brings 10
  Ray-Ban Display to share. Its quotes for Gemma and Verda match the file.
- **README vs the product.** The README still shows condition profiles, a Parkinson's switch, "No
  cameras in the home" and "You already took your pills". JUN-19 dropped profiles. Answers are
  observation-only. Photos go to the cluster, so the pitch cannot say "nothing leaves the home".
- **Note on branches.** The local `origin/main` is behind GitHub. PRs #6, #13, #16 and #21 are
  merged on GitHub. PR #24 (the whole vision stage) is still open to `main`.
