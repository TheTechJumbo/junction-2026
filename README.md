# junction-2026

**A 3D diary of the day for people who can't easily say what happened, and for the people who care for them.**

Gaussian splatting turns a home into a living 3D record. It keeps updating through the day, so a
carer can see what's going on now and scrub back to see what happened and what changed. The person
can point at a place or a moment in the scene, or simply look at something while wearing AI glasses,
to say what they can't put into words. The app turns that into a sentence they confirm before it is
spoken.

## The problem

- **The words won't come.** Around a third of stroke survivors have aphasia: a median of 30% in acute
  care and 34% in rehabilitation ([Flowers et al., 2016](https://pubmed.ncbi.nlm.nih.gov/27063364/)).
  They know what they want to say but can't find the words. People with dementia may not remember
  what happened at all.
- **Carers arrive to open questions.** Did she take her morning pills? Did he eat? Did anyone visit?
  Has something been moved that could cause a fall? Today the answers come from notes, memory or
  guesswork.

## How it works

1. **Capture.** The room is scanned into Gaussian splats: by a phone today, and later by AI glasses or
   a fixed camera. Each new capture is merged in. Surfaces seen again get sharper and more solid,
   and things that have gone fade out.
2. **Timeline.** Each capture is kept as a snapshot. You can scrub through the day in 3D, with what
   changed since the last snapshot highlighted: the pill box moved, a plate appeared, a chair is out
   of place.
3. **Look or point to say it.** The person looks at the pill box (glasses) or taps it in the scene,
   and adds a fragment such as "morning…". The app suggests sentences like "Did I take my morning
   pills?", plays them privately first, and speaks one aloud **only after the person confirms it**.
4. **Carer view.** Shows what's different since the last visit, plus any questions the person
   flagged.

**Why 3D and not video:** a day of video takes hours to watch and has no sense of place. The 3D record
shows only *what* changed and *where*. Pointing at a place, or looking at it, is a way to communicate
that doesn't need words.

## Challenges we're targeting

| Challenge | Fit |
| --- | --- |
| **Meta: AI Glasses for Everyone, Everyday** (main) | The first-person view *is* the interaction: look at the pill box, say "morning…", hear the sentence. The glasses also capture the day for the diary. |
| Google DeepMind: Best Use of Gemini API / Gemma | Gemini: understand the scene ("what changed?") and turn look + fragment into sentences. Gemma: on-device for privacy. |
| MLH: Best Use of ElevenLabs | Speak the confirmed sentence in a natural voice, or the person's own voice if they consent and have old recordings. |
| Arduino: Best Use of Arduino UNO Q | Cheap event sensors (pill-box lid, door, motion) that timestamp moments on the timeline. |
| Verda Compute Track | Full Gaussian-splat training of the day's captures on GPUs, beyond the live phone version. |

Tech challenges can be combined with the main challenge. Full list: <https://junction2026.com/challenges>.

## Demo plan

1. Scan a corner set up as a living room in the "morning".
2. Change things to stand in for the day: move the pill box, leave a cup, push a chair out.
3. Rescan, then scrub the timeline. The changes light up in 3D.
4. A teammate playing the patient looks at the pill box and says "morning". The app offers "Did I
   take my morning pills?"; they confirm, and it is spoken.
5. Race the same sentence on a picture board against the app, with a stopwatch.

## What exists already

Day-1 prototypes are on branch `feat/rescue-ping-hud` (not merged into `main`):

- `demo/live-splat.html`: live Gaussian-splat scanning on a phone. AR mode on Android Chrome uses
  ARCore depth and tracking; other phones get an AI-depth fallback. It already merges repeat
  observations and fades what's gone, which is the core of "what changed".
- `demo/index.html`: a glasses-style pointer display and a 3D overview, from an earlier
  rescue-pings concept.

To run them:

```bash
git switch feat/rescue-ping-hud
python -m http.server 5173
```

Then open <http://localhost:5173/demo/live-splat.html>.

Still to build: snapshots and timeline scrubbing, change highlighting between snapshots,
look/tap-to-sentence with confirm-before-speaking, and a carer view.

## Possible split (roles from `coord/PROTOCOL.md`)

- **Seb (ML):** change detection between snapshots, describing what changed, sentence suggestions
  from look/tap plus fragments.
- **Bartek (backend):** storing snapshots and events, serving the timeline.
- **Ayman (Arduino):** event sensors on the UNO Q (pill-box lid, door, motion).
- **Ed (general):** timeline and carer UI, the glasses flow, demo and pitch.

## Privacy and consent

Recording someone's home all day is sensitive. These are non-negotiable:

- consent from the person, or their legal representative;
- only the agreed room, with a visible pause;
- processing on the device where possible;
- automatic deletion after a set time;
- nothing spoken without the person's confirmation;
- voice cloning only with explicit consent.

## Open questions

- Capture: glasses, phone sweeps or a fixed camera? How often?
- Who can see the timeline: the person, carers, family, nurses?
- What should be described automatically, and what left for people to look at?
- Validate with a speech-language therapist or a carer before the pitch.

## Team coordination

`coord/` holds the Linear coordination kit. Start with `coord/BOOTSTRAP.md`.
