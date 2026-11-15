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
import {Space, parsePly, upright, shape, ring, disc, ribbon, wall, dashes, pillar} from './space.js';

const RED = [0.86, 0.27, 0.25], ORANGE = [0.95, 0.6, 0.25], AMBER = [0.95, 0.69, 0.29], TEAL = [0.3, 0.75, 0.7];
const EARLY = [0.33, 0.36, 0.85], LATE = [0.97, 0.7, 0.3], DIM = [0.42, 0.45, 0.58];
// Floor overlays sit this far above the floor: the meshed floor is a slab a few centimetres thick.
const LIFT = 0.07;
// The night trail's height: time mapped from LOW_M (start of the night) to HIGH_M (the end) above the floor.
const LOW_M = 0.45, HIGH_M = 2.1;
const mix = (a, b, f) => a.map((v, k) => v + (b[k] - v) * f);
const clock = t => new Date(t).toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Helsinki'});
const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const times = n => (n === 1 ? 'once' : n === 2 ? 'twice' : `${n} times`);

/** Home-level data the hero shows: how many things to clear up, and last night in a few words. */
export async function homeSummary({api, live, day, signal}) {
  const data = await loadData({api, live, day, signal, mesh: false});
  const clear = (data.hazards?.hazards || []).filter(h => h.in_the_way);
  const s = data.night?.summary;
  return {
    clearUp: clear.length ? `${count(clear.length, 'thing', 'things')} to clear up` : '',
    night: s && s.trips ? `Up ${times(s.trips)} last night` : s ? 'Slept through last night' : '',
  };
}

async function json(url, signal) {
  const response = await fetch(url, {signal, cache: 'no-store'});
  if (!response.ok) throw Object.assign(new Error('The example home is unavailable.'), {userFacing: true});
  return response.json();
}
const missing = error => error && (error.status === 404);
/** Mesh, hazards and night together: all from the home, or all from the example. */
async function loadData({api, live, day, signal, mesh = true}) {
  if (live) {
    try {
      const [buffer, hazards, night] = await Promise.all([
        mesh ? api.mesh(signal) : Promise.resolve(null),
        api.hazards(signal).catch(e => { if (missing(e)) return null; throw e; }),
        api.night(day, signal).catch(e => { if (missing(e)) return null; throw e; }),
      ]);
      return {buffer, hazards, night, example: false};
    } catch (error) {
      if (!missing(error)) throw error; // no 3D map from home yet: the example, labelled
    }
  }
  const [buffer, hazards, night] = await Promise.all([
    mesh ? fetch('./fixtures/home.ply', {signal}).then(r => { if (!r.ok) throw Object.assign(new Error('The example room is unavailable.'), {userFacing: true}); return r.arrayBuffer(); }) : null,
    json('./fixtures/hazards.json', signal), json('./fixtures/night.json', signal),
  ]);
  return {buffer, hazards, night, example: true, liveAsked: live};
}

export class Home3D {
  constructor({$, api, plain}) {
    Object.assign(this, {$, api, plain});
    this.tab = 'now'; this.timer = 0;
    $('tab-now').addEventListener('click', () => this.show('now'));
    $('tab-night').addEventListener('click', () => this.show('night'));
    $('night-time').addEventListener('input', () => { this.pause(); this.drawNight(); });
    $('night-play').addEventListener('click', () => (this.timer ? this.pause() : this.play()));
    $('night-back').addEventListener('click', () => this.step(-60));
    $('night-forward').addEventListener('click', () => this.step(60));
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
    this.say([label, clear ? `${count(clear, 'thing', 'things')} on the floor in the way of the night walks.` : 'Nothing on the floor in the way of the night walks.'].filter(Boolean).join(' '));
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
    this.track = n.points.map(p => ({t: Date.parse(p.t), p: (q => { q[1] = floor; return q; })(upright(p.p, n.up))}));
    this.t0 = this.track[0].t; this.t1 = this.track.at(-1).t; this.floor = floor;
    const span0 = (this.t1 - this.t0) || 1;
    this.lift = t => floor + LOW_M + (HIGH_M - LOW_M) * (t - this.t0) / span0;
    for (const x of this.track) x.air = [x.p[0], this.lift(x.t), x.p[2]];
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
      button.addEventListener('click', () => { this.setTime(Date.parse(trip.start)); this.play(Date.parse(trip.end)); });
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
    ribbon(whole, this.track.map(x => x.p), 0.05, () => DIM);
    ribbon(whole, this.track.map(x => x.air), 0.025, () => DIM);
    this.space.layer('night-all', whole);
    this.setTime(n.trips.length ? Date.parse(n.trips[0].start) : this.t0);
  }
  setTime(t) { this.$('night-time').value = String(Math.round(((t - this.t0) / ((this.t1 - this.t0) || 1)) * 1000)); this.drawNight(); }
  now() { return this.t0 + (Number(this.$('night-time').value) / 1000) * (this.t1 - this.t0); }
  step(seconds) { this.pause(); this.setTime(Math.min(this.t1, Math.max(this.t0, this.now() + seconds * 1000))); }
  /** Play at one minute of night per second, until the end (or `until`). */
  play(until = this.t1) {
    this.pause();
    if (this.now() >= until - 1000) this.setTime(this.t0);
    this.$('night-play').textContent = 'Pause';
    let last = performance.now();
    const frame = time => {
      const t = Math.min(until, this.now() + (time - last) * 60); last = time;
      this.setTime(t);
      if (t >= until) { this.pause(); return; }
      this.timer = requestAnimationFrame(frame);
    };
    this.timer = requestAnimationFrame(frame);
  }
  pause() { cancelAnimationFrame(this.timer); this.timer = 0; this.$('night-play').textContent = 'Play'; }

  drawNight() {
    if (!this.track || this.tab !== 'night') return;
    const n = this.data.night, t = this.now(), span = (this.t1 - this.t0) || 1;
    let k = this.track.findIndex(x => x.t > t); if (k < 0) k = this.track.length;
    const done = this.track.slice(0, Math.max(k, 1));
    // Where the wearer is now: between the last fix and the next.
    let here = done.at(-1).p;
    if (k > 0 && k < this.track.length) {
      const a = this.track[k - 1], b = this.track[k], f = (t - a.t) / ((b.t - a.t) || 1);
      here = mix(a.p, b.p, Math.min(1, Math.max(0, f)));
    }
    const hereAir = [here[0], this.lift(t), here[2]];
    const colourAt = i => mix(EARLY, LATE, (done[Math.min(i, done.length - 1)].t - this.t0) / span);
    const air = [...done.map(x => x.air), hereAir];
    const trail = shape();
    ribbon(trail, air, 0.09, colourAt); wall(trail, air, 0.09, colourAt);              // the trail: a flat band and an upright one, a tube from any angle
    ribbon(trail, [...done.map(x => x.p), here], 0.07, i => mix(colourAt(i), DIM, 0.55)); // its shadow on the floor
    for (let i = 0; i < done.length; i += 6) {                                         // thin threads from the floor up to the trail
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
    this.space.layer('night-done', trail);
    const marks = shape();
    pillar(marks, here, 0.05, hereAir[1] - here[1], AMBER, [1, 0.92, 0.75]);          // the wearer: a beam from the floor
    pillar(marks, [hereAir[0], hereAir[1] - 0.09, hereAir[2]], 0.13, 0.18, [1, 0.92, 0.75]); // ... to now, on the trail
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
