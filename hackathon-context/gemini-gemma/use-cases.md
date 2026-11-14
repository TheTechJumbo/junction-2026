# Gemini and Gemma: use cases for our project

Brainstorm, Sat 14 Nov 2026, from Ed's Claude. Builds on `../track-plan.md` (sections 3.6 and 3.10)
and `../challenges.md` (challenges 26 and 27). Marked **[F]** where it is a fact from the challenge
list or our code, **[J]** where it is judgement. Hours are rough estimates.

## What the two tracks ask for [F]

| | Gemini API (challenge 26) | Gemma (challenge 27) |
|---|---|---|
| Ask | "a meaningful, innovative, and technically impressive use of Gemini", "beyond simply adding an LLM to an application" | "the most innovative and impactful use of Google's Gemma open models" |
| Submission | explain the problem, how Gemini is integrated, what is innovative, why Gemini matters | not stated |
| Prizes | 1st 4000 € + 1,000 $ Google Cloud credits per member, 2nd 2000 €, 3rd 1000 € | 1,000 € for each of 3 teams |
| Rules | participants 18 or older | models via Featherless, Modal, Verda, Google AI Studio, Hugging Face |

## Where we are today [F]

- `backend/answer_engine/adapters.py` (`GeminiOpenAdapter`): Gemini answers open questions only, with
  the question text and nothing else (no events, photos or history), within 0.8 s, and must never
  claim a medicine was taken or a door locked. Off unless `GEMINI_API_KEY` and `GEMINI_MODEL` are set.
- `android/.../MetaGlassesAdapter.kt`: the phone app streams the glasses camera (medium quality,
  15 fps) and can take a photo (`capturePhoto`). Nothing on the phone decides which pictures matter.
- The vision stage sends every photo to the university GPU cluster. Seb's note on JUN-15: the pitch
  can say "processed on a private server, no third-party cloud", not "nothing leaves the home".
- Gemma is not used anywhere.

## The idea that ties them together [J]

**Pictures stay on our own hardware; only text goes to Google.**

- **Gemma** (open weights, runs where we choose) is the model that looks at pictures: on the phone,
  or on our own GPU next to VGGT.
- **Gemini** (Google's cloud) only ever sees text records and what the carer types, never a photo.

This keeps Seb's privacy wording true, gives the two models different jobs (so we can claim both
honestly), and is a clear story for the Meta judges' "appropriate consent and safe data practices".
It also matches the "perception cascade" from the earlier brainstorm: a cheap model watches all the
time and the bigger ones only run when something happens.

## Gemma use cases

| # | Use case | Where it runs | What the judge sees | Owner | Effort | Verdict |
|---|---|---|---|---|---|---|
| M1 | **Camera gate**: for each glasses frame, a small multimodal Gemma decides "is this a key moment?" (pills in hand, door being locked, keys put down). Only those frames are sent on. This is JUN-21. | Android phone | A split screen: the frames the gate saw, and the few it let through. "38 of 40 frames never left the phone." | Ed | 4-6 h | **Best fit**, if it is fast enough on the phone |
| M2 | **Same gate, on our GPU** if M1 is too slow on the phone: Gemma vision runs on the cluster or a Verda GPU next to VGGT and labels each photo before it is stored. | Iridis or Verda | Photos are labelled by a model on our own server; no third-party cloud. Also stacks with Verda. | Seb | 2-3 h | **Fallback for M1** |
| M3 | **Daily note on the box**: a small text Gemma writes Sara's evening note from the local event log, so the log never leaves the home. | UNO Q | "The note was written on this little board." | Ayman | 3-4 h | Only after the real sensor works; speed unmeasured |
| M4 | **Offline phrasing**: when the link drops, the phone answers from its cached events with a small Gemma rewording the rule-based answer. | Android phone | Airplane mode on, still answers. | Ed | 3 h | Weak: the rules already give the answer; Gemma only rewords it |

How M1 could work [J]: take a frame every 1-2 s from the stream the app already opens, scale it
down, and ask Gemma one fixed yes/no question with a short list of moment types. Answer as JSON
(`{"moment": "pills_in_hand", "sure": 0.8}`). Only a "yes" calls `capturePhoto()` and sends the
photo. Show a counter of frames seen and frames sent.

To check before building M1 (not verified here): which Gemma model runs on Android with image input
(the Gemma 3n family is meant for phones), through which runtime (Google AI Edge / LiteRT or the
MediaPipe LLM Inference API), and how many frames per second it manages on our phone. **Spend the
first hour measuring that on the real phone.** Below about one frame every 2 s, switch to M2.

## Gemini use cases

| # | Use case | What Gemini gets | What the judge sees | Owner | Effort | Verdict |
|---|---|---|---|---|---|---|
| G1 | **Answers with receipts**: open questions ("When did I last go out?") answered from the event records. Every sentence must cite event IDs, and the backend drops any sentence without a valid citation before it is shown or spoken. | Text event records (type, time, place), never photos | The answer, with the photo evidence for each cited event on the dashboard | Bartek | 2-3 h | **Best fit**: it is the app's own idea ("Memory with receipts") |
| G2 | **The family's daily note**: one short note for Sara each evening, built the same way (cited event IDs, no medical claims, "the pill box was opened at 08:00", never "Anna took her pills"). | Text event records for the day | The note on the dashboard, each line linked to its moment | Bartek | 1-2 h on top of G1 | **Strong**, same code as G1 |
| G3 | **Setting up by talking**: the carer says or types the routine ("pills at 8 and 8, front door locked at night, keys usually on the hall table"). Gemini turns it into structured calls (function calling) that create reminders and named places through the relay's existing calls (`POST /reminder`, `POST /waypoint`). | The carer's words and the list of allowed actions | Setup in one sentence instead of a form | Bartek or Ed | 3 h | **Good**: clearly "beyond adding an LLM" |
| G4 | **Naming places in the baseline photos**: after the room is recorded, Gemini marks where the pill box, front door and keys spot are (image points), which become the named places that the position fix points to. | Baseline photos of the room | The places appear by themselves on `places.html` | Seb | 2-3 h | **Conflicts with the split above**: photos would go to Google. Only with carer consent, or do it with Gemma (M2) |
| G5 | **Talking with Gemini Live**: a spoken conversation through the glasses instead of one question at a time. | Live audio | A natural back-and-forth | Ed | 6 h+ | Risky: real-time audio on the phone, and video sessions are reported to end after about 2 minutes (from the brainstorm, not checked) |

Rules that stay for every Gemini use [F from Bartek's adapter, J for keeping them]: no diagnosis,
treatment or dosing advice; never claim an action happened, only what was observed; a short deadline
and a plain fallback answer when Gemini is slow or fails.

## Recommendation [J]

1. **Gemma M1, the camera gate (Ed, JUN-21)**, with M2 on the GPU as the fallback. This is what makes
   the privacy story true, and it is the most visible use of an open model at the edge.
2. **Gemini G1 + G2, answers and the daily note with receipts (Bartek)**. One piece of work, two
   visible results, and the citation check is the "technically impressive" part.
3. **Gemini G3, setup by talking**, if there is time after 1 and 2.
4. Skip G4 with Gemini (photos to Google), M4 (the rules do it already) and G5 (too risky now).

In the submission: "Gemma sees pictures on our own hardware; Gemini reads only text records, and
must cite the record for every claim." One sentence, two tracks, no double claim.

## Decisions needed from the team

- **Bartek:** G1 and G2 mean sending event records (text only) to Gemini, which the current adapter
  forbids by design. Is that acceptable, with the citation check and no photos?
- **Seb:** is a Gemma vision model on the GPU worker (M2) fine next to VGGT, memory-wise (VGGT
  peaks at about 9.9 GB on the GPU)?
- **Everyone:** which phone do we demo on? M1 depends on it.
- **Everyone:** whoever submits for Gemini must be 18 or older.
