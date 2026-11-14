import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiClient,localDay,timeLabel,sortedEvents} from '../client.js';
const origin='http://localhost:8080';
test('Helsinki local day and clock are independent of browser timezone and DST',()=>{
 assert.equal(localDay(new Date('2026-11-13T22:30:00Z')),'2026-11-14');
 assert.equal(timeLabel('2026-11-14T01:10:00Z'),'03:10');
 assert.equal(timeLabel('2026-10-25T00:30:00Z'),'03:30');
 assert.equal(timeLabel('2026-10-25T01:30:00Z'),'03:30');
});
test('equal timestamps sort by id without mutating input',()=>{const events=[{id:'z',ts:'2026-11-14T01:00:00Z'},{id:'a',ts:'2026-11-14T01:00:00Z'}];assert.deepEqual(sortedEvents(events).map(e=>e.id),['a','z']);assert.equal(events[0].id,'z');});
test('all daily API requests authenticate with Bearer, disable caches and reject redirects',async()=>{
 const calls=[];const client=new ApiClient({origin,fetchImpl:async(url,options)=>{calls.push({url,options}); return Response.json(url.includes('timeline')?{events:[],alerts:[],questions:[],sensors:[],day:'2026-11-14',timezone:'Europe/Helsinki'}:{text:'No observations.'});}});
 client.setToken(' private-session ');await client.day('2026-11-14');assert.equal(calls.length,2);
 for(const {url,options} of calls){assert.equal(new URL(url).origin,origin);assert.equal(new URL(url).search,'?day=2026-11-14');assert.equal(options.headers.Authorization,'Bearer private-session');assert.equal(options.redirect,'error');assert.equal(options.cache,'no-store');assert.equal(options.credentials,'omit');assert(!url.includes('private-session'));}
 client.clearToken();await assert.rejects(client.day('2026-11-14'),/Connect with/);
});
test('evidence refuses external, embedded credentials, query secrets and non-http schemes before sending token',async()=>{
 let calls=0;const client=new ApiClient({origin,fetchImpl:async()=>{calls++;return new Response(new Blob(['img']),{headers:{'content-type':'image/png'}});}});client.setToken('secret');
 for(const url of ['https://external.test/photo','//external.test/photo','http://user:pass@localhost:8080/photo','data:image/png;base64,AAAA','/photo?token=secret','/photo#secret'])await assert.rejects(client.evidence(url),/unavailable/);
 assert.equal(calls,0);assert(await client.evidence('/evidence/a') instanceof Blob);assert.equal(calls,1);
});
test('unavailable or unsupported evidence returns an honest error',async()=>{
 const client=new ApiClient({origin,fetchImpl:async()=>new Response('<html>login</html>',{headers:{'content-type':'text/html'}})});client.setToken('secret');await assert.rejects(client.evidence('/evidence/a'),/image is unavailable/);
});
test('invalid day never makes requests',async()=>{const client=new ApiClient({origin,fetchImpl:()=>assert.fail('network request')});for(const day of ['2026-02-30','hello','2026-11-14&token=x'])await assert.rejects(client.day(day),/valid day/);});
test('network and authentication errors never become fixture data',async()=>{
 for(const [fetchImpl,message] of [[async()=>{throw new TypeError('internal secret');},/Cannot reach/],[async()=>new Response('',{status:401}),/token was not accepted/],[async()=>new Response('',{status:503}),/temporarily unavailable/]]){const client=new ApiClient({origin,fetchImpl});client.setToken('secret');await assert.rejects(client.day('2026-11-14'),message);}
});
test('real HTTP transport reads empty day and authenticated PNG on origin-root routes',async t=>{
 const {createServer}=await import('node:http');const seen=[];
 const server=createServer((req,res)=>{seen.push(req.url);if(req.headers.authorization!=='Bearer test-only-token'){res.writeHead(401);res.end();return;}
 if(req.url==='/artifacts/test'){res.writeHead(200,{'Content-Type':'image/png'});res.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9AAAAABJRU5ErkJggg==','base64'));return;}
 res.setHeader('Content-Type','application/json');if(req.url==='/timeline?day=2026-11-14')res.end(JSON.stringify({events:[],alerts:[],questions:[],sensors:[],day:'2026-11-14',timezone:'Europe/Helsinki'}));else if(req.url==='/summary?day=2026-11-14')res.end(JSON.stringify({text:'No observations recorded.'}));else{res.writeHead(404);res.end('{}');}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);}));
 const client=new ApiClient({origin:`http://127.0.0.1:${server.address().port}/dashboard/`});client.setToken('test-only-token');const day=await client.day('2026-11-14');assert.deepEqual(day.timeline.events,[]);assert.equal(day.summary.text,'No observations recorded.');assert.equal((await client.evidence('/artifacts/test')).type,'image/png');assert.deepEqual(seen.sort(),['/artifacts/test','/summary?day=2026-11-14','/timeline?day=2026-11-14']);
});

test('fetch transport receives the global receiver required by browser native fetch',async()=>{
 let calls=0;
 function receiverSensitiveFetch(url){
  assert.equal(this,globalThis,'native browser fetch must receive its global object');calls++;
  return Promise.resolve(Response.json(url.includes('timeline')?{events:[],alerts:[],questions:[],sensors:[],day:'2026-11-14',timezone:'Europe/Helsinki'}:{text:'No observations.'}));
 }
 const client=new ApiClient({origin,fetchImpl:receiverSensitiveFetch});client.setToken('test-only-token');await client.day('2026-11-14');assert.equal(calls,2);
});

test('evidence bounds declared and streamed size before creating an image',async()=>{
 for (const declared of [true,false]) {
  let cancelled=false;
  const stream=new ReadableStream({start(controller){controller.enqueue(new Uint8Array(5*1024*1024+1));},cancel(){cancelled=true;}});
  const headers={'content-type':'image/png'};if(declared)headers['content-length']=String(5*1024*1024+1);
  const client=new ApiClient({origin,fetchImpl:async()=>new Response(stream,{headers})});client.setToken('test');
  await assert.rejects(client.evidence('/evidence/test'),/too large/);assert(cancelled);
 }
});
