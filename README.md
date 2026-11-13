# Beacon: rescue pings on smart glasses

**Junction 2026.** A game-style "ping" system for rescue teams wearing Meta Ray-Ban Display glasses.
Look at something, pinch, swipe: every teammate's glasses show a marker pointing at that spot,
with distance and clock direction. A shared Gaussian-splat scan of the site gives everyone
(and the incident commander) the same 3D picture.

Military teams already work this way: blue-force tracking and shared map markers on ATAK,
heads-up compass and waypoints on IVAS-style displays. Beacon brings that to consumer glasses
for firefighters and search-and-rescue teams.

## Run the demo

Hosted on GitHub Pages (once Pages is enabled for this repo):

- Phone, live camera: <https://thetechjumbo.github.io/junction-2026/demo/?live>
- Desktop, 3D scan: <https://thetechjumbo.github.io/junction-2026/demo/>

Or run it locally from the repo root:

```bash
python -m http.server 5173
```

Then open <http://localhost:5173/demo/>. The first load streams a ~50 MB public splat scan of a room.

| Gesture (Neural Band → web keys) | Action |
| --- | --- |
| `Enter` (pinch) | open ping wheel |
| then `↑` `→` `←` (swipe) | drop VICTIM / HAZARD / CLEARED where you're looking |
| then `↓` | MORE: need hands, exit, regroup, MAYDAY |
| `←` `→` | cycle through pings |
| `↑` | respond to focused ping (en route → resolved) |
| `W A S D`, drag | walk / look (desktop only) |
| `V`, `M` | switch view, simulate a teammate MAYDAY |

**Responder view** shows the world (the splat) with the simulated glasses display on top.
That display is 600×600 and additive, so black is see-through. **Command view** is a top-down
"dollhouse" cutaway of the same scan, with every ping and responder pinned in 3D. Clicking the
scene drops a ping, and the responder's glasses are mirrored in the corner.

Use your own scan with `?scene=<url to .splat/.ply/.spz>`. Add `&level=x,y,z,w` if its floor is tilted.

### Live camera mode (try it on a phone)

The **World: Live camera** toggle swaps the scan for your camera feed. To skip the scan download
entirely, open `/demo/?live`. The phone is a stand-in for the glasses: the camera shows what you'd
see through the lenses, and the HUD floats on top.

- **Heading and tilt** come from the phone's compass and motion sensors, so pings stay in place as
  you turn. On a laptop with no sensors, drag to turn instead.
- **No 3D model of your room**, so a ping you drop is ranged by how far down you're looking
  (eye height over a flat floor). Looking level or up places it 5 m out.
- **The camera needs HTTPS.** On the laptop, `localhost` is fine. For a phone, put the server behind
  an HTTPS tunnel and open `https://<tunnel>/demo/?live`:

  ```bash
  cloudflared tunnel --url http://localhost:5173
  ```

- `?camfov=<deg>` tunes the camera's vertical field of view (default 65 in portrait, 45 in landscape)
  if brackets drift off target as you turn.

On the real glasses no camera feed is needed, because you see the world through the lenses. Meta's
web apps don't get camera access anyway; only the native SDK does.

## What the wearer sees

Meta's display is small (600×600 px, about 20° diagonal, right eye only), so the HUD shows very little:

- **Compass tape** with pings (shape + colour) and teammates (blue). Off-screen pings show as `◀ 2` / `3 ▶`.
- **Brackets on the real spot** when you face a ping, with distance and ▲/▼ height (a different floor).
- **One focus card**: type, distance, clock position ("2 O'CLOCK"), who placed it, status.
- **MAYDAY**: a flashing banner and a spoken call ("Mayday. Alpha 2. 1 o'clock, 3 metres.").

Every ping type has a distinct shape, so colour is never the only cue:
VICTIM ✚ · HAZARD ⚠ · CLEARED ☒ (FEMA "X") · NEED HANDS ◎ · EXIT ↑ · REGROUP ⚑ · MARK ◇ · MAYDAY ◆

## Where Gaussian splatting fits (and where it doesn't)

**Fits:**
- **A shared 3D map.** A drone or phone scan of the building becomes a splat. Pings are anchored to
  real 3D points (raycast onto the scan), *including height*, so "victim, one floor up" is expressible.
  GPS can't do that indoors.
- **Command view.** A photoreal digital twin for the incident commander, plus pre-entry briefing and
  after-action replay.
- **Stretch: indoor positioning.** Match the glasses camera against the splat to relocalize where GPS fails.

**Doesn't fit:**
- **Rendering splats on the glasses.** The display is tiny, additive and low-power, and the
  wearer already sees the real world. The glasses get only the lightweight ping overlay.
- **Live capture.** Turning a capture into a splat takes minutes, not seconds, so a live incident
  would rely on an existing scan or a quick drone pass during size-up.

## Platform facts (Meta Ray-Ban Display web apps, developer preview since May 2026)

- Plain HTML/JS web apps loaded from an **HTTPS URL** (Chromium). Budget: <300 KB first load, 30 Hz panel.
- Input: Neural Band / touchpad arrive as `ArrowUp/Down/Left/Right` + `Enter`. Back fires `popstate`.
- Available: `DeviceOrientationEvent` (heading; "absolute" not guaranteed), geolocation (from the
  paired phone), WebSocket, `speechSynthesis`, and voice tools via `document.modelContext.registerTool()`
  ("Hey Meta, mark a victim").
- Docs: <https://wearables.developer.meta.com/docs/develop/webapps>

## Open questions to work through

1. **Where am I indoors?** Phone GPS works outdoors only. Options: UWB/BLE beacons dropped at entry,
   relocalizing against the splat, or the commander tapping "they're here" on the map.
2. **Heading accuracy.** Compasses drift near steel and wiring. Calibrate by facing a known landmark?
3. **Who pings?** Wearers only, or the commander too (exits, rally points)?
4. **Hackathon scope.** Simulated demo only, or a real test with two pairs of glasses on a shared server?

## Repo

- `demo/index.html`: the demo (three.js + [Spark](https://sparkjs.dev) splat renderer, no build step)
- `demo/shared/`: ping vocabulary and geo helpers (bearing, distance, clock direction)
- `server/`: *parked, untested.* A WebSocket ping relay with a drill simulator and TAK/CoT export,
  started before we decided to keep things simple. The demo doesn't use it.
