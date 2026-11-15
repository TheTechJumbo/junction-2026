import {ApiClient, TIMEZONE, localDay, timeLabel, sortedEvents, medicationOpenings} from './client.js';
import {DAYPARTS, dayFraction, dayState, daypart, mood, shiftDay, sunPosition, sunTimes} from './day.js';

const $ = id => document.getElementById(id);
const root = document.documentElement;
const api = new ApiClient({origin: location.origin});
const params = new URLSearchParams(location.search);
let demo = params.has('demo'), controller, generation = 0, evidenceController, evidenceObject, lastData = null, refreshedAt = null;

// ---------- Words ----------
const KINDS = {
  pillbox_open: {title: 'Pill box opened', icon: 'pill', kind: 'pills'},
  door_open: {title: 'Door opened', icon: 'door', kind: 'door'},
  door_closed: {title: 'Door closed', icon: 'door', kind: 'door'},
  object_appeared: {title: 'Item seen', icon: 'eye', kind: 'vision'},
  object_gone: {title: 'Item gone', icon: 'eye', kind: 'vision'},
};
const SOURCES = {sensor: {label: 'Home sensor', icon: 'sensor'}, vision: {label: 'Glasses', icon: 'eye'}, voice: {label: 'Voice', icon: 'question'}};
const INTENTS = {pills: 'Pills taken?', door: 'Door locked?', navigation: 'Where is it?', medical: 'Medical', open: 'Other'};
const readable = text => String(text ?? '').replaceAll('_', ' ');
const capital = text => readable(text).replace(/^./, c => c.toUpperCase());
const kindOf = event => KINDS[event.type] || {title: readable(event.type).replace(/^./, c => c.toUpperCase()), icon: 'eye', kind: 'other'};
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// ---------- DOM helpers ----------
function node(tag, text, className) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el; }
function icon(name, className = '') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', `icon icon-${name} ${className}`.trim()); svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use'); use.setAttribute('href', `#i-${name}`);
  svg.append(use); return svg;
}
function timeEl(ts, label = timeLabel(ts)) { const el = node('time', label); el.dateTime = ts; return el; }
function empty(target, text, iconName = 'calm') { const box = node('div', undefined, 'empty'); box.append(icon(iconName), node('p', text)); $(target).replaceChildren(box); }
function status(message, error = false) { $('status').textContent = message; $('status').classList.toggle('error', error); }
const setVar = (name, value) => root.style.setProperty(name, String(value));

// ---------- Themes ----------
// Each theme is a folder under themes/: theme.css plus SVG assets (mark, scene, icons). The SVGs are
// inlined so the theme's CSS can colour and animate them. They are our own files, but are still
// stripped of scripts, event handlers and external links before they are added to the page.
let themes = [{id: 'kaamos', name: 'Kaamos'}];
function storedTheme() { try { return localStorage.getItem('daylight-theme'); } catch { return null; } }
function storeTheme(id) { try { localStorage.setItem('daylight-theme', id); } catch { /* private mode */ } }
function cleanSvg(text) {
  const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
  const svg = doc.documentElement;
  if (svg.nodeName !== 'svg' || doc.querySelector('parsererror')) return null;
  svg.querySelectorAll('script,foreignObject,style').forEach(el => el.remove());
  for (const el of [svg, ...svg.querySelectorAll('*')]) {
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on') || name === 'style' || ((name === 'href' || name === 'xlink:href') && !attr.value.startsWith('#'))) el.removeAttribute(attr.name);
    }
  }
  return document.importNode(svg, true);
}
async function loadAsset(id, name) {
  try { const response = await fetch(`themes/${id}/assets/${name}.svg`); if (!response.ok) return null; return cleanSvg(await response.text()); }
  catch { return null; }
}
async function applyTheme(id) {
  if (!themes.some(theme => theme.id === id)) id = themes[0].id;
  const link = $('theme-css'), href = `themes/${id}/theme.css`;
  const loaded = link.getAttribute('href') === href ? Promise.resolve()
    : new Promise(resolve => { link.addEventListener('load', resolve, {once: true}); link.addEventListener('error', resolve, {once: true}); link.setAttribute('href', href); });
  const [mark, scene, icons] = await Promise.all(['mark', 'scene', 'icons'].map(name => loadAsset(id, name)));
  document.querySelectorAll('[data-asset="mark"]').forEach(slot => slot.replaceChildren(...(mark ? [mark.cloneNode(true)] : [])));
  document.querySelectorAll('[data-asset="scene"]').forEach(slot => slot.replaceChildren(...(scene ? [scene] : [])));
  $('sprite').replaceChildren(...(icons ? [icons] : []));
  root.dataset.themeId = id;
  storeTheme(id);
  await Promise.race([loaded, new Promise(resolve => setTimeout(resolve, 1500))]);
  syncThemeColor();
  document.querySelectorAll('#theme-options input').forEach(input => { input.checked = input.value === id; });
}
// The phone's browser bar follows the theme, and the sky when a theme changes colour with it.
function syncThemeColor() {
  const color = getComputedStyle(root).getPropertyValue('--theme-color').trim();
  if (color) document.querySelector('meta[name="theme-color"]').content = color;
}
function buildThemeOptions() {
  $('theme-options').replaceChildren(...themes.map(theme => {
    const label = node('label', undefined, 'theme-option');
    const input = node('input'); input.type = 'radio'; input.name = 'theme'; input.value = theme.id;
    input.addEventListener('change', () => applyTheme(theme.id));
    const swatches = node('span', undefined, 'swatches');
    for (const color of theme.swatches || []) { const swatch = node('span', undefined, 'swatch'); swatch.style.setProperty('background', color); swatches.append(swatch); }
    const words = node('span', undefined, 'theme-words'); words.append(node('strong', theme.name), node('small', theme.description || ''));
    label.append(input, swatches, words); return label;
  }));
}

// ---------- Time of day ----------
// Drives the scene and the "now" line: CSS reads --now, --sun-progress, --sun-altitude, --sunrise,
// --sunset (all 0 to 1) and data-daypart / data-day-state / data-sky on <html>.
// ?at=HH:MM previews the scene at another time of day (visuals only; data and dates are unaffected).
const previewAt = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(params.get('at') || '');
function tick() {
  let now = new Date(), fraction = dayFraction(now);
  if (previewAt) { const target = (Number(previewAt[1]) * 60 + Number(previewAt[2])) / 1440; now = new Date(now.getTime() + (target - fraction) * 86400000); fraction = target; }
  const today = localDay(now);
  const sun = sunTimes(today), position = sunPosition(fraction, sun);
  setVar('--now', fraction.toFixed(4));
  setVar('--sun-progress', position.progress.toFixed(4));
  setVar('--sun-altitude', position.altitude.toFixed(4));
  root.dataset.daypart = daypart(fraction);
  const sky = position.altitude > 0.25 ? 'day' : position.altitude > 0 ? 'low-sun' : position.altitude > -0.25 ? 'twilight' : 'dark';
  if (root.dataset.sky !== sky) { root.dataset.sky = sky; requestAnimationFrame(syncThemeColor); }
  root.dataset.dayState = dayState($('day').value || today, today);
  $('clock').textContent = timeLabel(now.toISOString()); $('clock').dateTime = now.toISOString();
  $('next-day').disabled = demo || ($('day').value || today) >= today;
  $('today').hidden = ($('day').value || today) === today;
  if (refreshedAt && !demo) $('updated').textContent = `Updated ${ago(refreshedAt, now)}`;
}
function ago(then, now) {
  const minutes = Math.round((now - then) / 60000);
  return minutes < 1 ? 'just now' : minutes < 60 ? `${plural(minutes, 'minute', 'minutes')} ago` : `at ${timeLabel(then.toISOString())}`;
}
function showSun(day) {
  const sun = sunTimes(day);
  if (sun.polar) { $('sunrise').textContent = $('sunset').textContent = '–'; $('day-length').textContent = sun.polar === 'day' ? 'The sun does not set today.' : 'The sun does not rise today.'; return; }
  $('sunrise').textContent = timeLabel(sun.rise.toISOString()); $('sunset').textContent = timeLabel(sun.set.toISOString());
  const minutes = Math.round((sun.set - sun.rise) / 60000);
  $('day-length').textContent = `${Math.floor(minutes / 60)} h ${minutes % 60} min of daylight`;
  setVar('--sunrise', dayFraction(sun.rise).toFixed(4)); setVar('--sunset', dayFraction(sun.set).toFixed(4));
}
function dayName(day) {
  const today = localDay();
  if (day === today) return 'Today';
  if (day === shiftDay(today, -1)) return 'Yesterday';
  const [y, m, d] = day.split('-').map(Number);
  return new Intl.DateTimeFormat('en-GB', {weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC'}).format(new Date(Date.UTC(y, m - 1, d)));
}

// ---------- Rendering ----------
function heroFor(data, state) {
  const today = state === 'today';
  if (!data) return demo ? ['Loading…', ''] : ['Connect to see the day at home.', ''];
  const {timeline, summary} = data, alerts = timeline.alerts.length;
  const title = alerts ? `${plural(alerts, 'thing', 'things')} to notice${today ? ' today' : ''}.`
    : !timeline.events.length ? 'No observations recorded for this day.'
    : `${plural(timeline.events.length, 'observation', 'observations')} recorded${today ? ' today' : ''}.`;
  return [title, summary.text || 'No daily note has been written for this day yet.'];
}
function renderHero() {
  const state = dayState($('day').value, localDay());
  const [title, lede] = heroFor(lastData, state);
  $('hero-title').textContent = title; $('summary-text').textContent = lede;
  $('info-button').hidden = !lastData;
  $('info-footnote').hidden = /not confirm/i.test(lede); // the backend's note may already say it
  $('hero-place').textContent = demo ? "Anna's home · example" : 'Recorded observations';
  const t = lastData?.timeline;
  $('stat-alerts').textContent = t ? t.alerts.length : '–';
  $('stat-events').textContent = t ? t.events.length : '–';
  $('stat-questions').textContent = t ? t.questions.reduce((sum, q) => sum + q.count, 0) : '–';
  $('hero-actions').hidden = Boolean(api.token || demo);
  root.dataset.mood = mood(t);
}
function markItem({at, kind, label, ts, flag}) {
  const item = node('li', undefined, 'mark'); item.dataset.kind = kind; if (flag) item.dataset.flag = flag;
  item.style.setProperty('--at', at.toFixed(4));
  item.append(icon(kind === 'question' ? 'question' : kind === 'alert' ? 'alert' : KINDS_ICON[kind] || 'eye'));
  const text = node('span', undefined, 'mark-label'); text.append(timeEl(ts), node('span', ` ${label}`)); item.append(text);
  item.title = `${timeLabel(ts)} ${label}`; return item;
}
const KINDS_ICON = {pills: 'pill', door: 'door', vision: 'eye'};
function renderRhythm(timeline) {
  const alertIds = new Set(timeline.alerts.flatMap(alert => alert.event_ids || []));
  const marks = sortedEvents(timeline.events).map(event => {
    const k = kindOf(event);
    return markItem({at: dayFraction(event.ts), kind: k.kind, label: k.title, ts: event.ts, flag: alertIds.has(event.id) ? 'alert' : null});
  });
  for (const q of timeline.questions) marks.push(markItem({at: dayFraction(q.last_ts), kind: 'question', label: `${INTENTS[q.intent] || readable(q.intent)} (asked ${q.count}×)`, ts: q.last_ts}));
  $('rhythm-marks').replaceChildren(...marks.sort((a, b) => a.style.getPropertyValue('--at') - b.style.getPropertyValue('--at')));
}
function renderAlerts(timeline) {
  $('alert-count').textContent = timeline.alerts.length || '';
  if (!timeline.alerts.length) { empty('alerts', 'No alerts recorded'); return; }
  $('alerts').replaceChildren(...timeline.alerts.map(alert => {
    const row = node('article', undefined, 'alert'); row.dataset.type = alert.type;
    const night = ['door_open_night', 'night_door_open'].includes(alert.type);
    const body = node('div', undefined, 'alert-body');
    body.append(node('strong', night ? 'Door opened at night' : capital(alert.type)));
    if (!night && alert.message) body.append(node('p', alert.message));
    body.append(timeEl(alert.ts));
    row.append(icon(night ? 'moon' : 'alert', 'alert-icon'), body); return row;
  }));
}
function renderEvents(timeline) {
  $('event-count').textContent = timeline.events.length || '';
  const alertIds = new Set(timeline.alerts.flatMap(alert => alert.event_ids || []));
  const items = [];
  const events = sortedEvents(timeline.events);
  for (const part of DAYPARTS) {
    const inPart = events.filter(event => { const hour = dayFraction(event.ts) * 24; return hour >= part.from && hour < part.to; });
    if (!inPart.length) continue;
    const head = node('li', undefined, 'group'); head.dataset.part = part.id;
    head.append(icon(part.id === 'night' || part.id === 'evening' ? 'moon' : 'sun'), node('span', part.label));
    items.push(head);
    for (const event of inPart) {
      const k = kindOf(event);
      const row = node('li', undefined, 'event'); row.dataset.kind = k.kind; row.dataset.source = event.source;
      if (alertIds.has(event.id)) row.dataset.flag = 'alert';
      const detail = node('div', undefined, 'event-body');
      detail.append(node('strong', k.title), node('p', capital(event.zone), 'event-where'));
      const meta = node('p', undefined, 'event-meta');
      const source = SOURCES[event.source] || {label: readable(event.source), icon: 'sensor'};
      // Home sensors are the usual source, so only the glasses and voice get a badge.
      if (event.source !== 'sensor') { const chip = node('span', undefined, 'chip chip-icon'); chip.title = source.label; chip.append(icon(source.icon), node('span', source.label, 'visually-hidden')); meta.append(chip); }
      if (event.source === 'vision' && typeof event.confidence === 'number') { const sure = node('span', `${Math.round(event.confidence * 100)}%`, 'chip chip-soft'); sure.title = 'How sure the glasses were'; meta.append(sure); }
      if (event.evidence_url && !demo) { const button = node('button', 'Photo', 'link-button'); button.type = 'button'; button.addEventListener('click', () => showEvidence(event)); meta.append(button); }
      if (meta.childElementCount) detail.append(meta);
      row.append(timeEl(event.ts), node('span', undefined, 'event-dot'), icon(k.icon, 'event-icon'), detail);
      items.push(row);
    }
  }
  if (!items.length) { const li = node('li', undefined, 'empty'); li.append(icon('calm'), node('p', 'Nothing recorded')); items.push(li); }
  $('events').replaceChildren(...items);
}
function renderQuestions(timeline) {
  if (!timeline.questions.length) { empty('questions', 'None', 'question'); return; }
  $('questions').replaceChildren(...timeline.questions.map(q => {
    const row = node('div', undefined, 'question'); row.dataset.intent = q.intent; row.style.setProperty('--count', q.count);
    const words = node('div', undefined, 'question-words'); words.append(node('strong', INTENTS[q.intent] || readable(q.intent)), timeEl(q.last_ts));
    const pips = node('span', undefined, 'pips'); pips.setAttribute('aria-label', `Asked ${q.count} times`);
    for (let i = 0; i < Math.min(q.count, 12); i++) pips.append(node('span', undefined, 'pip'));
    row.append(icon('question'), words, pips, node('span', `${q.count}×`, 'question-count')); return row;
  }));
}
function renderSensors(timeline) {
  if (!timeline.sensors.length) { empty('sensors', 'None set up', 'sensor'); return; }
  const fmt = new Intl.DateTimeFormat('en-GB', {timeZone: TIMEZONE, hour: '2-digit', minute: '2-digit'});
  $('sensors').replaceChildren(...timeline.sensors.map(sensor => {
    const state = ['online', 'offline', 'unknown'].includes(sensor.status) ? sensor.status : 'unknown';
    const row = node('div', undefined, 'sensor'); row.dataset.status = state;
    const words = node('div', undefined, 'sensor-words'); words.append(node('strong', readable(sensor.id)), ...(sensor.last_seen ? [timeEl(sensor.last_seen, fmt.format(new Date(sensor.last_seen)))] : []));
    row.append(icon('sensor'), words, node('span', {online: 'Online', offline: 'Offline', unknown: 'Unknown'}[state], 'state')); return row;
  }));
}
function renderMedication(timeline) {
  const openings = medicationOpenings(timeline);
  $('medication-count').textContent = plural(openings.length, 'recorded opening', 'recorded openings');
  $('medication-openings').replaceChildren(...openings.map(event => {
    const item = node('li');
    item.append(timeEl(event.ts), node('span', ` · ${capital(event.zone)}${event.object ? ` · ${readable(event.object)}` : ''}`));
    return item;
  }));
  $('medication-empty').hidden = openings.length > 0;
}
function render(data) {
  lastData = data;
  const {timeline, summary} = data;
  renderHero(); renderRhythm(timeline); renderAlerts(timeline); renderEvents(timeline); renderQuestions(timeline); renderSensors(timeline); renderMedication(timeline);
  $('content').hidden = false;
  root.dataset.loaded = String(Date.now()); // themes may key entrance animations off this
}

// ---------- Evidence ----------
function cleanupEvidence() { evidenceController?.abort(); if (evidenceObject) URL.revokeObjectURL(evidenceObject); evidenceObject = null; $('evidence-image').hidden = true; $('evidence-image').removeAttribute('src'); }
async function showEvidence(event) {
  cleanupEvidence(); evidenceController = new AbortController(); const active = evidenceController;
  $('evidence-status').textContent = `Loading the moment from ${timeLabel(event.ts)}…`;
  $('evidence-dialog').showModal();
  try { const blob = await api.evidence(event.evidence_url, active.signal); if (active.signal.aborted) return; evidenceObject = URL.createObjectURL(blob); $('evidence-image').src = evidenceObject; $('evidence-image').hidden = false; $('evidence-status').textContent = `${kindOf(event).title} · ${timeLabel(event.ts)} Helsinki`; }
  catch (error) { if (error.name !== 'AbortError') $('evidence-status').textContent = error.message; }
}

// ---------- Loading ----------
async function load() {
  controller?.abort(); controller = new AbortController(); const active = ++generation;
  cleanupEvidence(); if ($('evidence-dialog').open) $('evidence-dialog').close();
  lastData = null; root.dataset.loading = 'true';
  $('content').hidden = true; $('refresh').disabled = true;
  root.dataset.mode = demo ? 'demo' : api.token ? 'live' : 'disconnected';
  $('connection-label').textContent = demo ? 'Example day' : api.token ? 'Connected' : 'Not connected';
  $('connection-button').textContent = api.token ? 'Disconnect' : 'Connect';
  $('demo').textContent = demo ? 'Exit example' : 'Example';
  $('day').disabled = $('prev-day').disabled = $('today').disabled = demo;
  renderHero(); showSun($('day').value); tick();
  status('');
  try {
    let data;
    if (demo) {
      const variant = params.get('demo') === 'calm' ? '-calm' : '';
      const [timeline, summary] = await Promise.all(['timeline', 'summary'].map(async name => { const response = await fetch(`./fixtures/${name}${variant}.json`, {signal: controller.signal, cache: 'no-store'}); if (!response.ok) throw new Error('The example day is unavailable.'); return response.json(); }));
      data = {timeline, summary}; $('day').value = timeline.day;
    } else {
      if (!api.token) { $('updated').textContent = ''; return; }
      if (!navigator.onLine) throw new Error('You are offline. Reconnect, then refresh to load this day.');
      data = await api.day($('day').value, controller.signal);
    }
    if (active !== generation) return;
    showSun($('day').value); $('day-name').textContent = dayName($('day').value);
    render(data);
    status('');
    refreshedAt = new Date();
    $('updated').textContent = demo ? 'Example data, not a real home' : 'Updated just now';
    tick();
  } catch (error) { if (error.name !== 'AbortError' && active === generation) { status(error.message, true); $('updated').textContent = 'The day could not be loaded. Refresh to try again.'; } }
  finally { if (active === generation) { $('refresh').disabled = false; delete root.dataset.loading; } }
}
function goTo(day) { $('day').value = day; $('day-name').textContent = dayName(day); load(); }
function toggleDemo() { demo = !demo; if (!demo) $('day').value = localDay(); load(); }
function openConnect() { $('connect-dialog').showModal(); $('token').focus(); }

// ---------- Wiring ----------
$('day').value = localDay(); $('day-name').textContent = dayName($('day').value);
$('day').addEventListener('change', () => goTo($('day').value));
$('prev-day').addEventListener('click', () => goTo(shiftDay($('day').value, -1)));
$('next-day').addEventListener('click', () => goTo(shiftDay($('day').value, 1)));
$('today').addEventListener('click', () => goTo(localDay()));
$('refresh').addEventListener('click', load);
$('demo').addEventListener('click', toggleDemo);
$('hero-demo').addEventListener('click', toggleDemo);
$('hero-connect').addEventListener('click', openConnect);
$('connection-button').addEventListener('click', () => { if (api.token) { api.clearToken(); demo = false; refreshedAt = null; load(); } else openConnect(); });
$('cancel-connect').addEventListener('click', () => { $('token').value = ''; $('connect-dialog').close(); });
$('connect-dialog').addEventListener('close', () => { $('token').value = ''; });
$('connect-form').addEventListener('submit', event => { event.preventDefault(); api.setToken($('token').value); $('token').value = ''; $('connect-dialog').close(); demo = false; $('day').value = localDay(); load(); });
$('close-evidence').addEventListener('click', () => $('evidence-dialog').close());
$('evidence-dialog').addEventListener('close', cleanupEvidence);
$('evidence-image').addEventListener('error', () => { cleanupEvidence(); $('evidence-status').textContent = 'This image could not be shown.'; });
$('theme-button').addEventListener('click', () => $('theme-dialog').showModal());
$('info-button').addEventListener('click', () => $('info-dialog').showModal());
$('close-info').addEventListener('click', () => $('info-dialog').close());
window.addEventListener('offline', () => { if (!demo && api.token) { controller?.abort(); generation++; $('refresh').disabled = false; $('content').hidden = true; status('You are offline. Reconnect, then refresh to load this day.', true); } });
window.addEventListener('online', () => { if (!demo && api.token) status('Back online. Refresh to load the latest.'); });
setInterval(tick, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });

// Installed app: register the service worker (static files only) and offer an Install button
// where the browser supports it. Opening from the home screen starts in standalone mode.
if ('serviceWorker' in navigator && !params.has('nosw')) navigator.serviceWorker.register('./sw.js').catch(() => {});
let installPrompt;
window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt = event; $('install').hidden = false; });
$('install').addEventListener('click', async () => { if (!installPrompt) return; installPrompt.prompt(); await installPrompt.userChoice.catch(() => {}); installPrompt = null; $('install').hidden = true; });
window.addEventListener('appinstalled', () => { installPrompt = null; $('install').hidden = true; });

// ---------- Start ----------
try { const response = await fetch('themes/themes.json'); if (response.ok) themes = await response.json(); } catch { /* keep the default */ }
buildThemeOptions();
await applyTheme(params.get('theme') || storedTheme() || themes[0].id);
load();
