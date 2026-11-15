const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']}); const page=await browser.newPage({viewport:{width:1200,height:1000},permissions:['microphone']}); const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(() => {
   // Synthetic audio exercises real MediaRecorder without accessing a person's microphone.
   navigator.mediaDevices.getUserMedia = async () => {
     const context = new AudioContext(); const tone = context.createOscillator();
     const destination = context.createMediaStreamDestination(); tone.connect(destination); tone.start();
     return destination.stream;
   };
 });
 let state={configured:true,status:null};let calls=[];
 await page.route('**/timeline?*',r=>r.fulfill({json:JSON.parse(fs.readFileSync('dashboard/fixtures/timeline.json'))}));
 await page.route('**/summary?*',r=>r.fulfill({json:{text:'Synthetic browser test'}}));
 await page.route('**/family/voice**',async r=>{const req=r.request();calls.push(req.url()); if(req.headers().authorization!=='Bearer synthetic-browser-token')throw Error('Auth missing');const path=new URL(req.url()).pathname;
 if(path.endsWith('/preview')){state.previewed=true;return r.fulfill({contentType:'audio/mpeg',body:Buffer.alloc(100)});}
 if(path.endsWith('/activate'))state.active=true;
 else if(path.endsWith('/disable'))state.active=false;
 else if(path.endsWith('/delete'))state={configured:true,status:null};
 else if(req.method()==='POST')state={configured:true,status:'ready',name:'Test Family',active:false,previewed:false};
 return r.fulfill({json:state});});
 await page.goto('http://127.0.0.1:8000/dashboard/?nosw');
 await page.locator('#hero-connect').click();await page.locator('#token').fill('synthetic-browser-token');await page.locator('#connect-form button[type=submit]').click();
 await page.locator('#voice-record').click();await page.waitForFunction(()=>!document.querySelector('#voice-stop').disabled,{},{timeout:5000}).catch(async e=>{console.log('Recording failure:',await page.locator('#voice-message').innerText());throw e;});if(!await page.locator('#voice-recording').isVisible())throw Error('Recording indicator missing');
 await page.waitForFunction(()=>document.querySelector('#voice-recording-time').textContent!=='00:00');
 if(!await page.locator('#voice-record').isDisabled())throw Error('Record button must be disabled while recording');
 await page.locator('#voice-stop').click();
 if(await page.locator('#voice-recording-label').innerText()!=='Recording stopped — microphone off')throw Error('Stopped state missing');await page.waitForFunction(()=>document.querySelector('#voice-source').src.startsWith('blob:'));await page.locator('#voice-name').fill('Test Family');await page.locator('#voice-file').setInputFiles({name:'sample.wav',mimeType:'audio/wav',buffer:Buffer.from('RIFFxxxxWAVE'+'x'.repeat(40))});await page.locator('#voice-consent').check();await page.locator('#voice-create-submit').click();
 await page.waitForFunction(()=>document.querySelector('#voice-status').textContent.includes('ready'));
 if(!await page.locator('#voice-activate').isDisabled())throw Error('Activation must await preview');
 await page.locator('#voice-preview-button').click();await page.waitForFunction(()=>!document.querySelector('#voice-activate').disabled);
 await page.locator('#voice-listener').check();await page.locator('#voice-activate').click();await page.waitForFunction(()=>document.querySelector('#voice-status').textContent.includes('enabled'));
 await page.locator('.familiar-voice').screenshot({path:'/tmp/tuttu-familiar.png'});
 await page.locator('#voice-disable').click();await page.waitForFunction(()=>document.querySelector('#voice-status').textContent.includes('Standard voice'));
 page.on('dialog',d=>d.accept());await page.locator('#voice-delete').click();await page.waitForFunction(()=>document.querySelector('#voice-status').textContent.includes('No family voice'));
 await page.locator('#connection-button').click();if(!await page.locator('#voice-actions').evaluate(el => el.disabled))throw Error('Disconnect must disable setup');
 await page.goto('http://127.0.0.1:8000/dashboard/?demo&nosw');await page.waitForSelector('#content:not([hidden])');if(!await page.locator('#voice-actions').evaluate(el => el.disabled))throw Error('Demo must disable setup');
 if(errors.length)throw Error(errors.join('\n')); console.log('Browser lifecycle passed: upload, preview gate, activation, disable, delete, disconnect and demo isolation.');await browser.close();
})();
