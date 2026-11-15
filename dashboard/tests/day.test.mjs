import test from 'node:test';
import assert from 'node:assert/strict';
import {dayFraction, sunTimes, sunPosition, daypart, mood, dayState, shiftDay} from '../day.js';
import {timeLabel} from '../client.js';

test('day fraction is measured from Helsinki midnight', () => {
  assert.equal(dayFraction('2026-11-14T08:00:00+02:00'), 8 / 24);
  assert.equal(dayFraction('2026-11-13T22:30:00Z'), 0.5 / 24);
});

test('Helsinki sun times are match the sunrise equation worked by hand', () => {
  // Worked by hand: 14 Nov, declination -18.3 deg, equation of time +15.5 min, solar noon 12:04,
  // half-day 56.9 deg, so about 08:16 / 15:52. 21 Jun: about 03:54 / 22:50.
  const near = (actual, expected) => {
    const [h, m] = timeLabel(actual).split(':').map(Number), [eh, em] = expected.split(':').map(Number);
    assert(Math.abs(h * 60 + m - (eh * 60 + em)) <= 6, `${timeLabel(actual)} vs ${expected}`);
  };
  const november = sunTimes('2026-11-14');
  near(november.rise, '08:16'); near(november.set, '15:52');
  const june = sunTimes('2026-06-21');
  near(june.rise, '03:54'); near(june.set, '22:50');
  assert.equal(sunTimes('2026-12-21', {lat: 78.2, lon: 15.6}).polar, 'night');
});

test('sun position runs from horizon to noon and below at night', () => {
  const sun = sunTimes('2026-11-14');
  const rise = dayFraction(sun.rise), set = dayFraction(sun.set);
  assert.equal(sunPosition(rise, sun).progress, 0);
  assert(Math.abs(sunPosition((rise + set) / 2, sun).altitude - 1) < 1e-9);
  assert(sunPosition(1 / 24, sun).altitude < 0);
  assert.equal(sunPosition(23 / 24, sun).progress, 1);
});

test('dayparts, mood and day navigation', () => {
  assert.equal(daypart(3 / 24), 'night');
  assert.equal(daypart(8 / 24), 'morning');
  assert.equal(daypart(18 / 24), 'evening');
  assert.equal(mood(null), 'unknown');
  assert.equal(mood({alerts: [{}], events: []}), 'notice');
  assert.equal(mood({alerts: [], events: []}), 'quiet');
  assert.equal(mood({alerts: [], events: [{}]}), 'calm');
  assert.equal(dayState('2026-11-13', '2026-11-14'), 'past');
  assert.equal(shiftDay('2026-03-01', -1), '2026-02-28');
  assert.equal(shiftDay('2026-12-31', 1), '2027-01-01');
});
