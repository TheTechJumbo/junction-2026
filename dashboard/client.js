export const TIMEZONE = 'Europe/Helsinki';
export function localDay(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: TIMEZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  const value = type => parts.find(p => p.type === type).value;
  return `${value('year')}-${value('month')}-${value('day')}`;
}
export function timeLabel(timestamp) {
  return new Intl.DateTimeFormat('en-GB', { timeZone: TIMEZONE, hour: '2-digit', minute: '2-digit' }).format(new Date(timestamp));
}
export function sortedEvents(events) {
  return [...events].sort((a,b) => Date.parse(a.ts)-Date.parse(b.ts) || a.id.localeCompare(b.id));
}
export class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.status = status; }
}
export class ApiClient {
  constructor({origin, fetchImpl = fetch}) {
    this.origin = new URL(origin).origin;
    // Browser fetch requires its Window receiver; never call it as an ApiClient method.
    this.fetch = (...args) => fetchImpl.call(globalThis, ...args);
    this.token = '';
  }
  setToken(token) { this.token = token.trim(); }
  clearToken() { this.token = ''; }
  async request(path, {signal, image = false} = {}) {
    if (!this.token) throw new ApiError('Connect with your access token to view this day.', 401);
    const url = new URL(path, this.origin);
    if (url.origin !== this.origin || url.username || url.password || url.search || url.hash) {
      // Day queries are constructed only inside day(), below.
      if (!(!image && url.origin === this.origin && !url.username && !url.password && !url.hash && /^\?day=\d{4}-\d{2}-\d{2}$/.test(url.search))) {
        throw new ApiError('This evidence address is unavailable. Only local evidence is supported.');
      }
    }
    let response;
    try { response = await this.fetch(url.href, {headers:{Authorization:`Bearer ${this.token}`}, credentials:'omit', cache:'no-store', redirect:'error', signal}); }
    catch (error) { if (error.name === 'AbortError') throw error; throw new ApiError('Cannot reach the home connection. Check your network and try again.'); }
    if (!response.ok) {
      const messages = {401:'Your access token was not accepted. Connect again.',403:'This session cannot access the requested information.',404:'This information is no longer available.',503:'The home service is temporarily unavailable.'};
      throw new ApiError(messages[response.status] || 'The home service could not complete this request.', response.status);
    }
    if (image) {
      if (!/^image\/(jpeg|png)(;|$)/i.test(response.headers.get('content-type') || '')) throw new ApiError('The evidence image is unavailable.');
      const limit = 5 * 1024 * 1024;
      if (Number(response.headers.get('content-length')) > limit) {
        await response.body?.cancel();
        throw new ApiError('The evidence image is too large.');
      }
      const reader = response.body?.getReader();
      if (!reader) throw new ApiError('The evidence image is unavailable.');
      const chunks = []; let size = 0;
      try {
        for (;;) {
          const {done, value} = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > limit) { await reader.cancel(); throw new ApiError('The evidence image is too large.'); }
          chunks.push(value);
        }
      } finally { reader.releaseLock(); }
      return new Blob(chunks, {type: response.headers.get('content-type').split(';')[0]});
    }
    return response.json();
  }
  async day(day, signal) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || Number.isNaN(Date.parse(day)) || new Date(day).toISOString().slice(0,10) !== day) throw new ApiError('Choose a valid day.');
    const [timeline, summary] = await Promise.all([this.request(`/timeline?day=${day}`,{signal}), this.request(`/summary?day=${day}`,{signal})]);
    if (timeline.day !== day || timeline.timezone !== TIMEZONE || !['events','alerts','questions','sensors'].every(key => Array.isArray(timeline[key])) || typeof summary.text !== 'string') throw new ApiError('The home service returned an unsupported daily view.');
    return {timeline,summary};
  }
  evidence(path, signal) { return this.request(path,{image:true,signal}); }
}
