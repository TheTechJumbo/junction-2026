# junction-2026

**Look at it, nod, and say it.** AI glasses for anyone who knows what they want to say but can't
find the words. We start with aphasia after a stroke.

The wearer looks at something (their pill box, the kettle, a photo of their daughter) and, if they
can, adds a word or two: "morning…". The glasses see what they're looking at, and an AI makes its
best guesses at what they mean, using the time of day and what it knows about their life. Each
guess plays privately into the glasses' speakers: "Did I take my morning pills?" A **nod** means
yes, and the phone says the sentence out loud. A **head shake** brings the next guess. There is no
reading, typing or picture-board hunting, and nothing is ever said without that nod.

## The problem

**When someone with aphasia wants to say something, the people around them have to guess, one
yes/no question at a time. It's slow, it depends on who's in the room, and when nobody's there,
they can't say it at all.**

- **Who:** about a third of stroke survivors have aphasia: a median of 30% in acute care and 34% in
  rehabilitation ([Flowers et al., 2016](https://pubmed.ncbi.nlm.nih.gov/27063364/)). We focus on
  people who understand well but can't find words (non-fluent aphasia).
- **Today:** partners are taught to play twenty questions ("pizza, yes [nod] or no [shake]?") or to
  write options to point at ([aphasia.com](https://www.aphasia.com/?p=3850)). Picture boards and
  speech devices exist, but they're slow and generic.
- **Why it matters:** quality of life in aphasia is linked to depression, emotional distress and
  shrinking social networks ([review](https://openaccess.city.ac.uk/id/eprint/1095/1/APMR-D-10-01160%20Aphasia%20QOL%20Review%20R2%20CLEAN.pdf)).

**The insight:** the bottleneck is *guessing what they mean*, and yes/no is often the one thing
they can still do reliably. So the AI does the guessing, and the person only nods or shakes their
head.

## Why AI glasses (Meta challenge: "AI Glasses for Everyone, Everyday")

- **The first-person camera answers "what do you mean?"** Looking at the pill box is the pointing.
- **The motion sensors turn a nod into "yes".** That's the answer method aphasia therapists already
  teach.
- **The open-ear speakers make guesses private,** so only the chosen sentence is said aloud.
- **It's hands-free and always there,** unlike a tablet left on the table.
- **It's for everyone, every day.** We start with aphasia, but the same look-and-nod loop helps
  anyone who can't find the words at times: people with dementia, autistic people who sometimes
  can't speak, people after a tracheostomy, or a traveller stuck in a foreign language.

## What makes it different

| Today | This project |
| --- | --- |
| Partner guesses with yes/no questions | The AI guesses with the context of what you're looking at; you still just say yes/no |
| Picture boards: generic icons, many taps, reading | Your real objects, no reading or typing |
| AI keyword-to-sentence prototypes (research): type keywords | Look plus an optional word |
| Only works when someone is there to guess | Works alone, too |

## Challenges we're targeting

| Challenge | Fit |
| --- | --- |
| **Meta: AI Glasses for Everyone, Everyday** (main) | Look, then nod: the glasses' first-person view and motion sensors *are* the interface. |
| Google DeepMind: Best Use of Gemini API | Gemini turns photo + fragment + personal context into ranked guesses. |
| MLH: Best Use of ElevenLabs | Natural voice for the spoken sentence, or the person's own voice with consent. |
| Arduino: Best Use of Arduino UNO Q | Home sensors (pill-box lid, kettle) feed context: "the pill box was opened at 08:12". |
| Aiven: Best Use of Aiven | Home events and the carer log stored in Aiven PostgreSQL (Bartek). |
| Modal: Best Use of Modal | Serve the fragment-transcription model on Modal's GPUs. |
| Cloudinary (only if used for photo handling) | Image capture and processing in the pipeline. |

Tech challenges can be combined with the main challenge. Only claim the ones we genuinely use,
because their judges check. Full list: <https://junction2026.com/challenges>.

## How it works

1. **Look (and maybe say a word).** The glasses take a photo and record a few seconds of audio.
2. **Guess.** `/guess` sends the photo, the transcribed fragment, the time, recent home events and
   a small personal profile (names, medicines, routines) to Gemini, which returns three ranked,
   short, first-person sentences.
3. **Hear and nod.** Guesses play one at a time into the glasses' speakers. A nod means yes, a
   shake means next, and "none of these" asks for another word.
4. **Say it.** The confirmed sentence is spoken from the phone in a natural voice.
5. **Context and log.** Home sensors add context, and the carer sees a log of what was said and
   any needs flagged.

### Shared formats (agree these first, in the "Contracts" issue)

```jsonc
// POST /guess  -> 3 ranked guesses (the server never speaks)
{ "image": "<jpeg base64>", "fragments": ["morning"], "time": "08:40",
  "recentEvents": [{ "ts": "…", "label": "pillbox_open", "source": "uno-q" }],
  "profile": { "names": ["Anna (daughter)"], "meds": ["blood thinner, 8:00"], "places": ["kitchen"] },
  "rejected": ["Can I have coffee?"] }
-> { "guesses": [{ "text": "Did I take my morning pills?", "confidence": 0.72 }, …] }

// Yes/no from the glasses' motion sensor: { "answer": "yes" | "no", "confidence": 0.9 }
// POST /speak   { "text": "…" } -> audio
// POST /events  (UNO Q) { "label": "pillbox_open", "ts": "…", "source": "uno-q" }
// GET  /log     confirmed sentences and flagged needs, for the carer
```

### Workstreams (one Linear issue each, labelled per `coord/PROTOCOL.md`)

| Owner | Issue | Done when |
| --- | --- | --- |
| all | Contracts: agree the formats above | Everyone has commented "ok" or proposed a change |
| all (`human-needed`) | Read Meta's challenge page: which glasses we get, and SDK access | We know whether we demo on real glasses or a phone stand-in |
| Seb | Guesser: `/guess` prompt with Gemini (photo + fragment + context + rejected guesses) | 20 test scenes give the right sentence in the top 3 for most of them (we report the rate) |
| Seb | Nod/shake detector from the glasses' motion data | Nods and shakes are recognised reliably in a quick test with 3 people |
| Seb | Fragment transcription that copes with single words and hesitation | "coffee… no… pill… morning" comes through intact |
| Bartek | API: `/guess` proxy (keys on the server), `/events`, `/log`, one HTTPS URL | The phone app and the UNO Q can both reach it |
| Bartek | `/speak` with ElevenLabs | The confirmed sentence plays within ~1 s |
| Ayman | UNO Q home sensors (pill-box lid, kettle) → `POST /events`; stretch: big physical yes/no buttons | Opening the pill box shows up as context for the next guess |
| Ed | Glasses app: capture photo and audio, play guesses privately, nod/shake loop (phone camera fallback) | The demo happy path works end to end |
| Ed | Carer log view, pitch, demo script, picture-board race | A rehearsed 90-second demo plus a backup video |
| all (`human-needed`) | One quote from a speech-language therapist or a carer | The quote is in the pitch |

### Milestones

- **Fri night (M0):** formats agreed, glasses access known, skeletons running with dummy data.
- **Sat 12:00 (M1):** happy path: look at pill box → 3 guesses → nod → spoken.
- **Sat 20:00 (M2):** nod/shake on the glasses, sensor context, carer log, test-scene hit rate measured.
- **Sun morning (M3):** polish, rehearse, backup video. Check the official submission deadline.

### Demo script (90 seconds)

1. "A third of stroke survivors lose their words. Today, the people around them have to guess."
   Start a stopwatch and try one sentence on a picture board.
2. The teammate playing the patient looks at the pill box and says "morning…". They hear "Did I
   take my morning pills?", nod, and the phone says it. Stop the stopwatch.
3. Bonus: the app adds "The pill box was opened at 08:12" (UNO Q sensor). That's a fact, not a
   medical claim.
4. "No reading, no typing, and nothing is said without their nod."

### Stretch (only if the core is done)

- **Point at things out of view:** a Gaussian-splat scan of the home (prototype on branch
  `feat/rescue-ping-hud`, `demo/live-splat.html`) lets the person pick the kettle in the kitchen
  from the bedroom.
- **"Show your day":** a timeline of home events to answer "how was your day?".
- Learning the person's most-used sentences, and their own voice (with consent).

## Privacy and consent

- Photos are processed for the guess and not stored by default.
- Consent from the person, or their legal representative.
- Nothing spoken without the person's confirmation.
- Voice cloning only with explicit consent.
- Sensor data stays in the home where possible.

## Team coordination

`coord/` holds the Linear coordination kit. Start with `coord/BOOTSTRAP.md`.
