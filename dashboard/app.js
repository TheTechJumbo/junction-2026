import {ApiClient, localDay, timeLabel, sortedEvents} from './client.js';
const $ = id => document.getElementById(id);
const api = new ApiClient({origin:location.origin});
let demo = false, controller, generation = 0, evidenceController, evidenceObject;
const titles = {pillbox_open:'Pill box opened', door_open:'Door opened',door_closed:'Door closed',object_appeared:'Object appeared',object_gone:'Object no longer seen'};
const intents = {pills:'Medication questions',door:'Door questions',navigation:'Finding the way',medical:'Medical questions'};
function node(tag, text, className) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if(className) el.className = className; return el; }
function empty(target,text) { $(target).replaceChildren(node('p',text,'empty')); }
function status(message,error=false) { $('status').textContent=message; $('status').classList.toggle('error',error); }
function cleanupEvidence() { evidenceController?.abort(); if(evidenceObject) URL.revokeObjectURL(evidenceObject); evidenceObject=null; $('evidence-image').hidden=true; $('evidence-image').removeAttribute('src'); }
async function showEvidence(event) {
  cleanupEvidence(); evidenceController = new AbortController(); const active = evidenceController;
  $('evidence-status').textContent=`Loading evidence · ${timeLabel(event.ts)}`;
  $('evidence-dialog').showModal();
  try { const blob=await api.evidence(event.evidence_url, active.signal); if(active.signal.aborted)return; evidenceObject=URL.createObjectURL(blob); $('evidence-image').src=evidenceObject; $('evidence-image').hidden=false; $('evidence-status').textContent=`${titles[event.type] || event.type} · ${timeLabel(event.ts)} Helsinki`; }
  catch(error) { if(error.name !== 'AbortError') $('evidence-status').textContent=error.message; }
}
function render({timeline,summary}) {
  $('summary-text').textContent=summary.text || 'No summary has been recorded for this day.';
  $('alert-count').textContent=`${timeline.alerts.length} recorded`;
  $('alerts').replaceChildren();
  for(const alert of timeline.alerts) { const row=node('article',undefined,'alert-item'); row.append(node('strong',['door_open_night','night_door_open'].includes(alert.type)?'Door opened during the night':alert.type.replaceAll('_',' ')),node('p',alert.message),node('time',`${timeLabel(alert.ts)} · Helsinki`)); $('alerts').append(row); }
  if(!timeline.alerts.length) empty('alerts','No sensor alerts recorded for this day.');
  $('event-count').textContent=`${timeline.events.length} observations`;
  $('events').replaceChildren();
  for(const event of sortedEvents(timeline.events)) {
    const row=node('li',undefined,'event'); const time=node('time',timeLabel(event.ts)); time.dateTime=event.ts;
    const detail=node('div'); detail.append(node('strong',titles[event.type] || event.type.replaceAll('_',' ')),node('p',`${event.zone.replaceAll('_',' ')}${event.object ? ` · ${event.object.replaceAll('_',' ')}` : ''}`),node('span',event.source==='vision'?'Vision evidence':event.source==='sensor'?'Sensor observation':'Voice observation',`source ${event.source}`));
    if(event.evidence_url && !demo) { const button=node('button','View evidence','quiet evidence'); button.type='button'; button.addEventListener('click',()=>showEvidence(event)); detail.append(button); }
    else detail.append(node('span','No image attached','no-evidence'));
    row.append(time,node('span',undefined,'dot'),detail); $('events').append(row);
  }
  if(!timeline.events.length) { const item=node('li','No observations recorded for this day.','empty'); $('events').append(item); }
  $('questions').replaceChildren();
  for(const question of timeline.questions) { const row=node('div',undefined,'question'), detail=node('div'); detail.append(node('strong',intents[question.intent] || question.intent.replaceAll('_',' ')),node('small',`Last asked at ${timeLabel(question.last_ts)}`)); row.append(detail,node('span',`${question.count}×`,'count')); $('questions').append(row); }
  if(!timeline.questions.length) empty('questions','No questions recorded for this day.');
  $('sensors').replaceChildren();
  for(const sensor of timeline.sensors) { const row=node('div',undefined,'sensor'),detail=node('div'); detail.append(node('strong',sensor.id),node('small',sensor.last_seen?`Last seen ${new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Helsinki',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(sensor.last_seen))}`:'No heartbeat received')); row.append(detail,node('span',sensor.status,`sensor-state ${['online','offline','unknown'].includes(sensor.status)?sensor.status:'unknown'}`)); $('sensors').append(row); }
  if(!timeline.sensors.length) empty('sensors','No devices have been configured.');
  $('content').hidden=false;
}
async function load() {
  controller?.abort(); controller=new AbortController(); const active=++generation;
  cleanupEvidence(); if($('evidence-dialog').open)$('evidence-dialog').close();
  $('content').hidden=true; $('refresh').disabled=true;
  status(demo?'Loading synthetic example…':'Loading your daily overview…');
  $('mode-label').textContent=demo?'SYNTHETIC DEMO · No real household data':'Private family overview';
  $('connection-label').textContent=demo?'Synthetic demo':api.token?'Home session':'Not connected';
  $('connection-button').textContent=api.token?'Disconnect':'Connect';
  $('demo').textContent=demo?'Leave demo':'Explore synthetic demo';
  $('day').disabled=demo;
  try {
    let data;
    if(demo) { const [timeline,summary] = await Promise.all(['timeline','summary'].map(async name=>{ const response=await fetch(`./fixtures/${name}.json`,{signal:controller.signal,cache:'no-store'}); if(!response.ok)throw new Error('The synthetic example is unavailable.'); return response.json(); })); data={timeline,summary}; $('day').value=timeline.day; }
    else { if(!navigator.onLine)throw new Error('You are offline. Reconnect, then refresh to load this day.'); data=await api.day($('day').value,controller.signal); }
    if(active!==generation)return; render(data);
    status(demo?'You are exploring synthetic observations from 14 November 2026. These are examples, not live data.':'');
    $('updated').textContent=demo?'Synthetic fixture · 14 November 2026':`Last refreshed ${timeLabel(new Date().toISOString())} Helsinki`;
  } catch(error) { if(error.name!=='AbortError' && active===generation) { status(error.message,true); $('updated').textContent='Daily overview unavailable. Refresh to try again.'; } }
  finally { if(active===generation)$('refresh').disabled=false; }
}
$('day').value=localDay();
$('day').addEventListener('change',load); $('refresh').addEventListener('click',load);
$('demo').addEventListener('click',()=>{ demo=!demo; if(!demo)$('day').value=localDay(); load(); });
$('connection-button').addEventListener('click',()=>{ if(api.token) {api.clearToken(); demo=false; load();} else {$('connect-dialog').showModal(); $('token').focus();} });
$('cancel-connect').addEventListener('click',()=>{$('token').value='';$('connect-dialog').close();});
$('connect-dialog').addEventListener('close',()=>{$('token').value='';});
$('connect-form').addEventListener('submit',event=>{event.preventDefault();api.setToken($('token').value);$('token').value='';$('connect-dialog').close();demo=false;$('day').value=localDay();load();});
$('close-evidence').addEventListener('click',()=>$('evidence-dialog').close());
$('evidence-dialog').addEventListener('close',cleanupEvidence);
$('evidence-image').addEventListener('error',()=>{cleanupEvidence();$('evidence-status').textContent='This evidence image could not be displayed.';});
window.addEventListener('offline',()=>{if(!demo){controller?.abort();generation++;$('refresh').disabled=false;$('content').hidden=true;status('You are offline. Reconnect, then refresh to load this day.',true);}});
window.addEventListener('online',()=>{if(!demo)status('Connection restored. Refresh to load current observations.');});
load();
