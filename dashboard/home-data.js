// What the home's 3D view and the hero both need: the mesh, trip hazards and the night, from the home
// when connected or the labelled example otherwise. Small on purpose: the hero loads it on every
// visit, while the 3D viewer itself (home3d.js, space.js) is only fetched when someone opens it.
export const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
// Hazards from one splat's shape alone (splat_hazards.py) are a guess and say so.
export const guessed = h => String(h?.method || '').startsWith('shape');
export const times = n => (n === 1 ? 'once' : n === 2 ? 'twice' : `${n} times`);

/** Home-level data the hero shows: how many things to clear up, and last night in a few words. */
export async function homeSummary({api, live, day, signal}) {
  const data = await loadData({api, live, day, signal, mesh: false});
  const clear = (data.hazards?.hazards || []).filter(h => h.in_the_way);
  const s = data.night?.summary;
  return {
    clearUp: !clear.length ? '' : guessed(data.hazards) ? count(clear.length, 'possible trip hazard', 'possible trip hazards')
      : `${count(clear.length, 'thing', 'things')} to clear up`,
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
export async function loadData({api, live, day, signal, mesh = true}) {
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

