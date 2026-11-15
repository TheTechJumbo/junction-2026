// Pure helpers for the shape of a Helsinki day: where a moment falls in it, when the sun is up,
// which part of the day it is, and how the day reads overall. No DOM, no network.
import {TIMEZONE} from './client.js';

const HELSINKI = {lat: 60.1699, lon: 24.9384};
const MINUTES = 24 * 60;
const rad = degrees => degrees * Math.PI / 180;
const deg = radians => radians * 180 / Math.PI;

/** Minutes after Helsinki midnight for a timestamp, as a fraction of the day (0 to 1). */
export function dayFraction(timestamp) {
  const parts = new Intl.DateTimeFormat('en-GB', {timeZone: TIMEZONE, hour: '2-digit', minute: '2-digit', hourCycle: 'h23'})
    .formatToParts(new Date(timestamp));
  const value = type => Number(parts.find(part => part.type === type).value);
  return (value('hour') * 60 + value('minute')) / MINUTES;
}

/** Approximate sunrise and sunset for a calendar day (YYYY-MM-DD) in Helsinki.
 * Uses the standard sunrise equation; good to a couple of minutes, which is all a family view needs.
 * Returns {rise, set} as Date objects, or {polar: 'night'|'day'} when the sun never rises or sets. */
export function sunTimes(day, {lat, lon} = HELSINKI) {
  const [year, month, date] = day.split('-').map(Number);
  const julian = Date.UTC(year, month - 1, date, 12) / 86400000 + 2440587.5;
  const n = Math.round(julian - 2451545 + 0.0008);
  const meanNoon = n - lon / 360;
  const anomaly = (357.5291 + 0.98560028 * meanNoon) % 360;
  const centre = 1.9148 * Math.sin(rad(anomaly)) + 0.02 * Math.sin(rad(2 * anomaly)) + 0.0003 * Math.sin(rad(3 * anomaly));
  const longitude = (anomaly + centre + 180 + 102.9372) % 360;
  const transit = 2451545 + meanNoon + 0.0053 * Math.sin(rad(anomaly)) - 0.0069 * Math.sin(rad(2 * longitude));
  const declination = Math.asin(Math.sin(rad(longitude)) * Math.sin(rad(23.4397)));
  const cosHour = (Math.sin(rad(-0.833)) - Math.sin(rad(lat)) * Math.sin(declination)) / (Math.cos(rad(lat)) * Math.cos(declination));
  if (cosHour > 1) return {polar: 'night'};
  if (cosHour < -1) return {polar: 'day'};
  const hourAngle = deg(Math.acos(cosHour)) / 360;
  const toDate = julianDay => new Date((julianDay - 2440587.5) * 86400000);
  return {rise: toDate(transit - hourAngle), set: toDate(transit + hourAngle)};
}

/** Night, morning, afternoon or evening for a fraction of the day. */
export function daypart(fraction) {
  if (fraction < 6 / 24) return 'night';
  if (fraction < 12 / 24) return 'morning';
  if (fraction < 18 / 24) return 'afternoon';
  return 'evening';
}
export const DAYPARTS = [
  {id: 'night', label: 'Night', from: 0, to: 6},
  {id: 'morning', label: 'Morning', from: 6, to: 12},
  {id: 'afternoon', label: 'Afternoon', from: 12, to: 18},
  {id: 'evening', label: 'Evening', from: 18, to: 24},
];

/** Where the sun is, for driving a scene: progress across the sky (0 at sunrise, 1 at sunset,
 * clamped) and altitude (1 at noon, 0 at the horizon, below 0 at night down to -1). */
export function sunPosition(fraction, sun) {
  if (sun.polar) return {progress: 0.5, altitude: sun.polar === 'day' ? 0.4 : -1};
  const rise = dayFraction(sun.rise), set = dayFraction(sun.set);
  const progress = Math.min(1, Math.max(0, (fraction - rise) / (set - rise)));
  let altitude;
  if (fraction >= rise && fraction <= set) altitude = Math.sin(Math.PI * progress);
  else {
    const night = 1 - (set - rise);
    const since = fraction > set ? fraction - set : fraction + 1 - set;
    altitude = -Math.sin(Math.PI * Math.min(1, since / night));
  }
  return {progress, altitude};
}

/** How the day reads, from what the backend reported. The dashboard does not apply care rules:
 * it only distinguishes "the backend raised alerts", "nothing recorded" and "observations, no alerts". */
export function mood(timeline) {
  if (!timeline) return 'unknown';
  if (timeline.alerts.length) return 'notice';
  if (!timeline.events.length) return 'quiet';
  return 'calm';
}

/** Whether a calendar day is today, before it or after it, in Helsinki. */
export function dayState(day, today) {
  return day === today ? 'today' : day < today ? 'past' : 'future';
}

/** The calendar day before or after a YYYY-MM-DD day. */
export function shiftDay(day, delta) {
  const [year, month, date] = day.split('-').map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, date + delta));
  return shifted.toISOString().slice(0, 10);
}
