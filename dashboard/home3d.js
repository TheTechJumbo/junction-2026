// The home in 3D for the family: the mesh of the flat, with two views on it.
//   Now        things on the floor in the way of the night walks (trip hazards to clear up), as red
//              rings, and the walks they block as amber dashes. Suggestions, never alerts.
//   Last night where the wearer walked after going to bed: a trail in the air whose height is the time
//              (knee height at the start of the night, above the head by morning), so each trip floats
//              at its own level; coloured early (indigo) to late (amber), with its shadow on the floor,
//              stops pinned down to the floor, the night's door events, and a beam for the wearer that
//              moves with the time slider (play, step back and forward, or tap a trip).
// Connected, everything comes from the home (GET /mesh, /hazards, /night). Otherwise, or while the
// home has no 3D map yet, all three are the example home, labelled as such; never a mix of both.
import {Space, parsePly, upright, shape, ring, disc, ribbon, wall, softRibbon, softWall, sphere, softDisc, dashes, pillar} from './space.js';
import {count, guessed, loadData, times} from './home-data.js';
export {homeSummary} from './home-data.js';

const RED = [0.86, 0.27, 0.25], ORANGE = [0.95, 0.6, 0.25], AMBER = [0.95, 0.69, 0.29], TEAL = [0.3, 0.75, 0.7];
const EARLY = [0.33, 0.36, 0.85], LATE = [0.97, 0.7, 0.3], DIM = [0.42, 0.45, 0.58];
// The speed dial's range, in minutes of night per second.
const SPEED_MIN = 0.25, SPEED_MAX = 30;
// Floor overlays sit this far above the floor: the meshed floor is a slab a few centimetres thick.
const LIFT = 0.07;
// The night trail's height: time mapped from LOW_M (start of the night) to HIGH_M (the end) above the floor.
const LOW_M = 0.45, HIGH_M = 2.1;
const mix = (a, b, f) => a.map((v, k) => v + (b[k] - v) * f);
const clock = t => new Date(t).toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Helsinki'});
export class Home3D {
  constructor({$, api, plain}) {
    Object.assign(this, {$, api, plain});
    this.tab = 'now'; this.timer = 0;
    $('tab-now').addEventListener('click', () => this.show('now'));
    $('tab-night').addEventListener('click', () => this.show('night'));
    this.speed = 60; this.dir = 1; // night seconds per second, and which way
    $('night-time').addEventListener('input', () => { this.pause(); this.drawNight(); });
    $('night-time').addEventListener('keydown', e => this.key(e));
    $('night-play').addEventListener('click', () => (this.timer && this.dir > 0 ? this.pause() : this.play(1)));
    $('night-rewind').addEventListener('click', () => (this.timer && this.dir < 0 ? this.pause() : this.play(-1)));
    $('night-prev').addEventListener('click', () => this.jump(-1));
    $('night-next').addEventListener('click', () => this.jump(1));
    this.dial($('speed-dial'));
    this.skip = true;
    $('night-skip').addEventListener('click', () => { this.skip = !this.skip; $('night-skip').setAttribute('aria-pressed', String(this.skip)); });
    this.tips(document.querySelectorAll('#night-panel [data-tip]'));
    // The strip: click to jump there; scroll (here or on the slider) to move a minute, with Shift ten.
    const strip = $('night-strip');
    strip.addEventListener('click', e => { const r = strip.getBoundingClientRect(); this.pause(); this.setTime(this.t0 + (this.t1 - this.t0) * Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))); });
    for (const el of [strip, $('night-time')]) {
      el.addEventListener('wheel', e => { e.preventDefault(); this.step(Math.sign(e.deltaY || e.deltaX) * (e.shiftKey ? 600 : 60)); }, {passive: false});
    }
    $('space-dialog').addEventListener('close', () => { this.controller?.abort(); this.pause(); this.space?.stop(); });
  }
  say(text) { this.$('space-status').textContent = text; }

  /** Open on a tab ("now" or "night"), loading everything the first time and after a change of mode. */
  async open({tab = 'now', live, day, focusFirst = false}) {
    const $ = this.$;
    $('space-dialog').showModal();
    this.controller?.abort(); this.controller = new AbortController(); const {signal} = this.controller;
    try { this.space ??= new Space($('space-canvas')); }
    catch (error) { this.say(this.plain(error, 'This device cannot show the 3D view.')); return; }
    const key = `${live}:${day}`;
    if (this.key !== key) {
      this.say('Loading the 3D map…');
      try {
        const data = await loadData({api: this.api, live, day, signal});
        this.space.show(parsePly(data.buffer));
        this.data = data; this.key = key;
      } catch (error) { if (error.name !== 'AbortError') this.say(this.plain(error, 'The 3D map could not be shown.')); return; }
    }
    this.space.start();
    this.show(tab, focusFirst);
  }

  show(tab, focusFirst = false) {
    const $ = this.$; this.tab = tab; this.pause();
    $('tab-now').setAttribute('aria-selected', String(tab === 'now'));
    $('tab-night').setAttribute('aria-selected', String(tab === 'night'));
    $('now-panel').hidden = tab !== 'now'; $('night-panel').hidden = tab !== 'night';
    this.space.clearLayers();
    const label = this.data.example ? (this.data.liveAsked ? 'No 3D map from home yet. This is the example home.' : 'Example home, not a real one.') : '';
    if (tab === 'now') { if (!focusFirst) this.space.reset(); this.drawNow(label, focusFirst); } else { this.space.overview(0.62, 0.74); this.setUpNight(label); }
  }

  // ---- Now: trip hazards ----
  drawNow(label, focusFirst) {
    const $ = this.$, h = this.data.hazards, list = $('hazard-list');
    list.replaceChildren();
    if (!h) { this.say([label, 'No trip hazard check yet.'].filter(Boolean).join(' ')); return; }
    const floor = (h.floor || 0) + LIFT, at = c => { const p = upright(c, h.up); p[1] = floor; return p; };
    const s = shape(), over = shape();
    const blocked = new Set(h.hazards.filter(z => z.in_the_way).flatMap(z => (z.walks || [z.walk]).map(w => w.join('>'))));
    for (const [a, b, [pa, pb]] of h.walks || []) {
      if (blocked.has(`${a}>${b}`)) dashes(s, at(pa), at(pb), 0.07, AMBER);
    }
    for (const z of h.hazards) {
      const r = z.across_m / 2 + 0.12;
      ring(over, at(z.centre), r, z.in_the_way ? 0.06 : 0.035, z.in_the_way ? RED : ORANGE);
      const item = document.createElement('li'), button = document.createElement('button');
      button.type = 'button'; button.className = 'link-button';
      button.textContent = z.words + (h.generated_at ? ` Seen ${clock(h.generated_at)}.` : '');
      button.addEventListener('click', () => this.space.focus(at(z.centre), 0.75));
      item.append(button); list.append(item);
    }
    this.space.layer('walks', s); this.space.layer('hazards', over, {depth: false});
    const clear = h.hazards.filter(z => z.in_the_way).length;
    const maybe = guessed(h) ? ' possibly' : '';
    this.say([label, clear ? `${count(clear, 'thing', 'things')} on the floor${maybe} in the way of the night walks.` : 'Nothing on the floor in the way of the night walks.',
      guessed(h) ? 'Guessed from the 3D shape alone.' : ''].filter(Boolean).join(' '));
    const first = h.hazards.find(z => z.in_the_way);
    if (focusFirst && first) this.space.focus(at(first.centre), 0.75);
  }

  // ---- Last night: the walk ----
  setUpNight(label) {
    const $ = this.$, n = this.data.night;
    $('trip-list').replaceChildren(); $('night-strip').replaceChildren();
    if (!n || !n.points?.length) {
      this.say([label, 'No night walk recorded for last night.'].filter(Boolean).join(' '));
      $('night-summary').textContent = ''; $('night-clock').textContent = '--:--'; return;
    }
    this.say(label);
    const floor = (this.data.hazards?.floor || 0) + LIFT;
    $('night-from').textContent = clock(n.points[0].t); $('night-to').textContent = clock(n.points.at(-1).t);
    // Moments worth jumping to: each trip's start and end, each stop, each door or sensor event.
    this.trips = n.trips.map(x => [Date.parse(x.start), Date.parse(x.end)]);
    this.moments = [...new Set([...n.trips.flatMap(x => [x.start, x.end]), ...(n.stops || []).filter(s => s.place !== 'bed').map(s => s.t), ...(n.events || []).map(e => e.t)]
      .map(Date.parse))].sort((a, b) => a - b);
    this.track = n.points.map(p => ({t: Date.parse(p.t), p: (q => { q[1] = floor; return q; })(upright(p.p, n.up))}));
    this.t0 = this.track[0].t; this.t1 = this.track.at(-1).t; this.floor = floor;
    const span0 = (this.t1 - this.t0) || 1;
    this.lift = t => floor + LOW_M + (HIGH_M - LOW_M) * (t - this.t0) / span0;
    for (const x of this.track) x.air = [x.p[0], this.lift(x.t), x.p[2]];
    // The drawn trail keeps only the points that change its shape (2 cm), worked out once: far fewer
    // triangles to rebuild on every step of the slider.
    this.kept = simplify(this.track, 0.02);
    const s = n.summary;
    $('night-summary').textContent = s.trips
      ? `Up ${times(s.trips)} · ${s.minutes_up} min out of bed · ${s.metres} m walked${s.places.length ? ` · ${s.places.join(', ')}` : ''}`
      : 'Stayed in bed.';
    const span = this.t1 - this.t0 || 1, strip = $('night-strip');
    for (const trip of n.trips) {
      const el = document.createElement('span'); el.dataset.kind = 'trip';
      el.style.setProperty('--from', ((Date.parse(trip.start) - this.t0) / span).toFixed(4));
      el.style.setProperty('--span', ((Date.parse(trip.end) - Date.parse(trip.start)) / span).toFixed(4));
      strip.append(el);
      const item = document.createElement('li'), button = document.createElement('button');
      button.type = 'button'; button.className = 'link-button';
      button.textContent = `${clock(trip.start)}–${clock(trip.end)} · ${trip.visits.length ? trip.visits.join(', ') : 'near the bed'}`;
      button.addEventListener('click', () => { this.setTime(Date.parse(trip.start)); this.play(1, Date.parse(trip.end)); });
      item.append(button); $('trip-list').append(item);
    }
    for (const e of n.events || []) {
      const el = document.createElement('span'); el.dataset.kind = 'event';
      el.style.setProperty('--from', ((Date.parse(e.t) - this.t0) / span).toFixed(4));
      el.title = `${clock(e.t)} ${e.type.replace(/_/g, ' ')}`; strip.append(el);
    }
    // The whole night faintly, for context: its shadow on the floor and a thin ghost in the air. The
    // walked-so-far part is drawn over it as time moves.
    const whole = shape();
    softRibbon(whole, this.track.map(x => x.p), 0.12, () => DIM, 0.35);
    softRibbon(whole, this.track.map(x => x.air), 0.18, () => DIM, 0.18);
    this.space.layer('night-all', whole, {blend: true});
    this.setTime(n.trips.length ? Date.parse(n.trips[0].start) : this.t0);
  }
  setTime(t) { this.$('night-time').value = String(Math.round(((t - this.t0) / ((this.t1 - this.t0) || 1)) * 1000)); this.drawNight(); }
  now() { return this.t0 + (Number(this.$('night-time').value) / 1000) * (this.t1 - this.t0); }
  step(seconds) { this.pause(); this.setTime(Math.min(this.t1, Math.max(this.t0, this.now() + seconds * 1000))); }
  /** The speed dial: a knob turned by dragging (up or right is faster), scrolling or the arrow keys.
   * Its position (0 to 1) maps to SPEED_MIN..SPEED_MAX minutes of night per second on a log scale,
   * so the slow end is as easy to set as the fast one. Changing it while playing takes effect at once. */
  dial(el) {
    const $ = this.$, arc = (from, to) => {
      const at = f => { const a = (135 + 270 * f) * Math.PI / 180; return [24 + 18 * Math.cos(a), 24 + 18 * Math.sin(a)]; };
      const [x0, y0] = at(from), [x1, y1] = at(to);
      return `M${x0.toFixed(2)} ${y0.toFixed(2)}A18 18 0 ${(to - from) * 270 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
    };
    el.querySelector('.dial-track').setAttribute('d', arc(0, 1));
    const set = f => {
      f = Math.min(1, Math.max(0, f)); this.dialAt = f;
      const minutes = SPEED_MIN * (SPEED_MAX / SPEED_MIN) ** f;
      this.speed = minutes * 60;
      const words = minutes < 1 ? `${Math.round(minutes * 60)} s/s` : `${minutes < 10 ? minutes.toFixed(1).replace(/\.0$/, '') : Math.round(minutes)} min/s`;
      $('speed-arc').setAttribute('d', arc(0, Math.max(f, 0.001)));
      const a = (135 + 270 * f) * Math.PI / 180;
      $('speed-knob').setAttribute('cx', (24 + 18 * Math.cos(a)).toFixed(2)); $('speed-knob').setAttribute('cy', (24 + 18 * Math.sin(a)).toFixed(2));
      $('speed-text').textContent = words;
      el.setAttribute('aria-valuenow', minutes.toFixed(2));
      el.setAttribute('aria-valuetext', minutes < 1 ? `${Math.round(minutes * 60)} seconds of night per second` : `${words.replace(' min/s', '')} minutes of night per second`);
    };
    set(Math.log(1 / SPEED_MIN) / Math.log(SPEED_MAX / SPEED_MIN)); // start at 1 min/s
    let drag = null;
    el.addEventListener('pointerdown', e => { el.setPointerCapture(e.pointerId); drag = {x: e.clientX, y: e.clientY, f: this.dialAt}; });
    el.addEventListener('pointermove', e => { if (drag) set(drag.f + ((e.clientX - drag.x) - (e.clientY - drag.y)) / 160); });
    el.addEventListener('pointerup', () => { drag = null; }); el.addEventListener('pointercancel', () => { drag = null; });
    el.addEventListener('wheel', e => { e.preventDefault(); set(this.dialAt - Math.sign(e.deltaY || -e.deltaX) * 0.05); }, {passive: false});
    el.addEventListener('keydown', e => {
      const steps = {ArrowUp: 0.05, ArrowRight: 0.05, ArrowDown: -0.05, ArrowLeft: -0.05, PageUp: 0.2, PageDown: -0.2};
      if (e.key in steps) { e.preventDefault(); set(this.dialAt + steps[e.key]); }
      else if (e.key === 'Home' || e.key === 'End') { e.preventDefault(); set(e.key === 'Home' ? 0 : 1); }
    });
  }
  /** Tooltips: each [data-tip] gets a bubble, shown on hover or keyboard focus (CSS) and on a long press
   * (touch: hold for half a second; the press then does not also click). */
  tips(elements) {
    for (const el of elements) {
      const tip = document.createElement('span'); tip.className = 'tip'; tip.setAttribute('aria-hidden', 'true');
      tip.textContent = el.dataset.tip; el.append(tip);
      let hold = 0, held = false;
      el.addEventListener('pointerdown', e => {
        if (e.pointerType === 'mouse') return;
        held = false; clearTimeout(hold);
        hold = setTimeout(() => { held = true; el.dataset.tipOpen = ''; }, 500);
      });
      const end = () => { clearTimeout(hold); if (held) setTimeout(() => delete el.dataset.tipOpen, 1500); };
      el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end); el.addEventListener('pointerleave', end);
      el.addEventListener('click', e => { if (held) { e.preventDefault(); e.stopImmediatePropagation(); held = false; } }, true);
      el.addEventListener('contextmenu', e => { if (e.pointerType !== 'mouse') e.preventDefault(); });
    }
  }
  tipText(el, text) { el.dataset.tip = text; const tip = el.querySelector('.tip'); if (tip) tip.textContent = text; }
  /** To the previous or next moment worth seeing (a trip starting or ending, a stop, a door). */
  jump(dir) {
    this.pause();
    const t = this.now(), list = this.moments || [];
    const to = dir > 0 ? list.find(m => m > t + 1000) : [...list].reverse().find(m => m < t - 1000);
    this.setTime(to ?? (dir > 0 ? this.t1 : this.t0));
  }
  key(e) {
    const moves = {ArrowRight: 60, ArrowUp: 60, ArrowLeft: -60, ArrowDown: -60, PageUp: 600, PageDown: -600};
    if (e.key in moves) { e.preventDefault(); this.step(moves[e.key] * (e.shiftKey ? 10 : 1)); }
    else if (e.key === 'Home' || e.key === 'End') { e.preventDefault(); this.pause(); this.setTime(e.key === 'Home' ? this.t0 : this.t1); }
    else if (e.key === ' ') { e.preventDefault(); this.timer ? this.pause() : this.play(1); }
  }
  /** Out of bed at time t? (Within a trip, give or take half a minute.) */
  awake(t) { return (this.trips || []).some(([a, b]) => t >= a - 30000 && t <= b + 30000); }
  /** Play forwards (dir 1) or backwards (-1) at the chosen speed, to the end (or `until`); with "skip
   * time in bed" the quiet stretches between trips are jumped over. */
  play(dir = 1, until = dir > 0 ? this.t1 : this.t0) {
    this.pause(); this.dir = dir;
    if ((dir > 0 && this.now() >= until - 1000) || (dir < 0 && this.now() <= until + 1000)) this.setTime(dir > 0 ? this.t0 : this.t1);
    this.showPlaying(true);
    let last = performance.now();
    const frame = time => {
      let t = this.now() + dir * (time - last) * this.speed; last = time;
      if (this.skip && this.trips?.length && !this.awake(t)) {
        const next = dir > 0 ? this.trips.find(([a]) => a > t) : [...this.trips].reverse().find(([, b]) => b < t);
        t = next ? (dir > 0 ? next[0] - 30000 : next[1] + 30000) : until;
      }
      t = dir > 0 ? Math.min(until, t) : Math.max(until, t);
      this.setTime(t);
      if (t === until) { this.pause(); return; }
      this.timer = requestAnimationFrame(frame);
    };
    this.timer = requestAnimationFrame(frame);
  }
  pause() { cancelAnimationFrame(this.timer); this.timer = 0; this.showPlaying(false); }
  showPlaying(on) {
    const $ = this.$, forwards = on && this.dir > 0, backwards = on && this.dir < 0;
    $('night-play').querySelector('[data-show="play"]').hidden = forwards;
    $('night-play').querySelector('[data-show="pause"]').hidden = !forwards;
    $('night-play').setAttribute('aria-label', forwards ? 'Pause' : 'Play'); this.tipText($('night-play'), forwards ? 'Pause' : 'Play');
    $('night-rewind').setAttribute('aria-pressed', String(backwards));
  }

  drawNight() {
    if (!this.track || this.tab !== 'night') return;
    const n = this.data.night, t = this.now(), span = (this.t1 - this.t0) || 1;
    let k = this.track.findIndex(x => x.t > t); if (k < 0) k = this.track.length;
    let kk = this.kept.findIndex(x => x.t > t); if (kk < 0) kk = this.kept.length;
    const done = this.kept.slice(0, Math.max(kk, 1));
    // Where the wearer is now: between the last fix and the next.
    let here = done.at(-1).p;
    if (k > 0 && k < this.track.length) {
      const a = this.track[k - 1], b = this.track[k], f = (t - a.t) / ((b.t - a.t) || 1);
      here = mix(a.p, b.p, Math.min(1, Math.max(0, f)));
    }
    const hereAir = [here[0], this.lift(t), here[2]];
    const colourAt = i => mix(EARLY, LATE, (done[Math.min(i, done.length - 1)].t - this.t0) / span);
    const air = [...done.map(x => x.air), hereAir];
    // The trail: a wide, see-through band lying flat and one standing up, soft at the edges, so it
    // reads as a glowing haze from any angle; a thin brighter core keeps the line easy to follow.
    const haze = shape();
    softRibbon(haze, air, 0.42, colourAt, 0.42); softWall(haze, air, 0.42, colourAt, 0.42);
    softRibbon(haze, [...done.map(x => x.p), here], 0.3, i => mix(colourAt(i), DIM, 0.5), 0.35); // its shadow on the floor
    this.space.layer('night-haze', haze, {blend: true, wave: 0.025});
    const trail = shape();
    ribbon(trail, air, 0.025, colourAt); wall(trail, air, 0.025, colourAt);
    for (let i = 0; i < done.length; i += 3) {                                         // thin threads from the floor up to the trail
      const x = done[i]; pillar(trail, x.p, 0.006, x.air[1] - x.p[1], mix(colourAt(i), DIM, 0.4));
    }
    for (const stop of n.stops || []) {
      if (Date.parse(stop.t) > t || stop.place === 'bed') continue;
      const p = upright(stop.p, n.up); p[1] = this.floor + 0.005;
      const r = Math.min(0.35, 0.1 + 0.02 * stop.minutes), top = this.lift(Date.parse(stop.t));
      disc(trail, p, r, TEAL);                                                         // where she stopped, on the floor
      pillar(trail, p, 0.025, top - p[1], TEAL);                                       // pinned up to when, on the trail
      disc(trail, [p[0], top, p[2]], r * 0.6, TEAL);
    }
    this.space.layer('night-done', trail, {wave: 0.025});
    // The wearer: a neon orb at the end of the trail, in the trail's colour at this moment: a bright
    // core, glow shells fading outwards, and its glow on the floor below so it is clear where she is.
    const now = mix(EARLY, LATE, (t - this.t0) / span), hot = mix(now, [1, 1, 1], 0.55);
    const orb = shape();
    softDisc(orb, here, 0.45, now, 0.55);
    for (const [r, a] of [[0.34, 0.07], [0.24, 0.13], [0.16, 0.24]]) sphere(orb, hereAir, r, now, a);
    sphere(orb, hereAir, 0.085, hot, 1);
    this.space.layer('night-orb', orb, {depth: false, blend: true, wave: 0.025});
    const marks = shape();
    for (const e of n.events || []) {
      const at = Date.parse(e.t), place = n.places?.[e.place];
      if (!place || at > t || t - at > 5 * 60 * 1000) continue; // a door event glows for five minutes
      const p = upright(place, n.up); p[1] = here[1];
      ring(marks, p, 0.35, 0.06, e.type.startsWith('door') ? AMBER : TEAL);
    }
    this.space.layer('night-now', marks, {depth: false});
    const near = (n.events || []).filter(e => Math.abs(Date.parse(e.t) - t) < 90 * 1000).map(e => e.type.replace(/_/g, ' '));
    this.$('night-clock').textContent = clock(t) + (near.length ? ` · ${near.join(', ')}` : '');
  }
}

/** The points of a track that change its shape by more than `tolerance` metres (Ramer-Douglas-Peucker on
 * the trail in the air), first and last always kept. */
function simplify(track, tolerance) {
  const n = track.length;
  if (n < 3) return track.slice();
  const keep = new Uint8Array(n); keep[0] = keep[n - 1] = 1;
  const stack = [[0, n - 1]];
  while (stack.length) {
    const [a, b] = stack.pop(), A = track[a].air, B = track[b].air;
    const ab = [B[0] - A[0], B[1] - A[1], B[2] - A[2]], len2 = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2 || 1e-12;
    let worst = -1, at = -1;
    for (let i = a + 1; i < b; i++) {
      const P = track[i].air, ap = [P[0] - A[0], P[1] - A[1], P[2] - A[2]];
      const f = Math.max(0, Math.min(1, (ap[0] * ab[0] + ap[1] * ab[1] + ap[2] * ab[2]) / len2));
      const d = Math.hypot(ap[0] - f * ab[0], ap[1] - f * ab[1], ap[2] - f * ab[2]);
      if (d > worst) { worst = d; at = i; }
    }
    if (worst > tolerance) { keep[at] = 1; stack.push([a, at], [at, b]); }
  }
  return track.filter((_, i) => keep[i]);
}
