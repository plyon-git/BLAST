/**
 * Optional end-to-end operator smoke test. Requires Node + Playwright and the
 * Python development environment. This script ALWAYS creates a fresh temporary
 * database, generates a temporary synthetic login, seeds demo records, and starts
 * its own simulation-only server. It cannot target an existing instance.
 *
 * Run from any directory: node scripts/browser-smoke.cjs
 * Optional: BLASTIO_PYTHON, BLASTIO_BROWSER_PATH, BLASTIO_BROWSER_LIBRARY_PATH,
 * BLASTIO_PLAYWRIGHT_MODULE (module name or absolute module path).
 * Produces docs/preview.png and docs/preview-mobile.png with synthetic data.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');
const net = require('net');
const {spawn, spawnSync} = require('child_process');
const {chromium} = require(process.env.BLASTIO_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'blastio-ui-smoke-'));
 const testEmail = 'ui-smoke@example.test';
 const testPassword = 'Synthetic-' + crypto.randomBytes(24).toString('hex');
 const python = process.env.BLASTIO_PYTHON || path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const port = await new Promise(resolve => { const socket = net.createServer(); socket.listen(0,'127.0.0.1', () => { const p=socket.address().port; socket.close(()=>resolve(p)); }); });
 const base = 'http://127.0.0.1:' + port;
 const serverEnv = {...process.env, BLASTIO_DB:path.join(fixture,'fixture.sqlite3'), BLASTIO_PUBLIC_URL:base,
  BLASTIO_MODE:'simulation', BLASTIO_ALLOW_PRODUCTION:'false', BLASTIO_COOKIE_SECURE:'false',
  BLASTIO_ENCRYPTION_KEY:crypto.randomBytes(32).toString('base64url')+'=', BLASTIO_SMOKE_EMAIL:testEmail,
  BLASTIO_SMOKE_PASSWORD:testPassword};
 const seed = spawnSync(python,['-c',`import os
from blastio.config import settings
assert settings.mode == 'simulation' and not settings.allow_production
from blastio.db import init_db,transaction,now
from blastio.auth import hash_password
from blastio.seed import seed_demo
init_db()
with transaction() as conn:
 conn.execute('INSERT INTO users(email,password_hash,role,created_at) VALUES(?,?,?,?)',(os.environ['BLASTIO_SMOKE_EMAIL'],hash_password(os.environ['BLASTIO_SMOKE_PASSWORD']),'admin',now()))
 seed_demo(conn)
`],{cwd:root,env:serverEnv,encoding:'utf8'});
 if(seed.status!==0) { fs.rmSync(fixture,{recursive:true,force:true}); throw new Error('Unable to prepare isolated simulation fixture: '+(seed.stderr||seed.error||'')); }
 const server=spawn(python,['-m','uvicorn','blastio.app:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env:serverEnv,stdio:['ignore','ignore','pipe']});
 let serverError='';server.stderr.on('data',d=>serverError+=d);
 let browser;
 try{
  let ready=false;for(let i=0;i<50;i++){try{const r=await fetch(base+'/healthz');if(r.ok){ready=true;break}}catch{}await wait(100)}if(!ready)throw new Error('Server not ready: '+serverError);
  const browserEnv = { ...process.env };
  if (process.env.BLASTIO_BROWSER_LIBRARY_PATH) browserEnv.LD_LIBRARY_PATH = process.env.BLASTIO_BROWSER_LIBRARY_PATH;
  browser=await chromium.launch({headless:true,executablePath:process.env.BLASTIO_BROWSER_PATH || undefined,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--no-zygote','--single-process','--disable-software-rasterizer','--disable-gpu-compositing','--use-gl=disabled','--disable-webgl'],env:browserEnv});
  const context=await browser.newContext({viewport:{width:1440,height:1000},timezoneId:'America/Denver'});
  const page=await context.newPage();let errors=[];let failures=[];page.on('pageerror',e=>errors.push(e.message));page.on('requestfailed',r=>failures.push(r.url()+': '+r.failure()?.errorText));
  await page.goto(base);await page.getByLabel('Email',{exact:true}).fill(testEmail);await page.getByLabel('Password',{exact:true}).fill(testPassword);await page.getByRole('button',{name:'Sign in',exact:false}).click();await page.getByRole('heading',{name:'Overview',exact:true}).waitFor();
  await page.screenshot({path:path.join(root,'docs','preview.png'),fullPage:true});console.log('desktop dashboard rendered');
  const titles={contacts:'Contacts & properties',import:'Import contacts',templates:'Messages & variants',campaigns:'Campaigns',inbox:'Conversation inbox',settings:'Twilio & safeguards',diagnostics:'Diagnostics & audit'};
  for(const [view,title] of Object.entries(titles)){await page.goto(base+'#'+view);await page.getByRole('heading',{name:title,exact:true}).waitFor();if(await page.getByText('Unable to load this view',{exact:true}).count())throw new Error('View failed '+view);console.log('view '+view+' rendered')}
  // CSV: preview, revalidate mapping, confirm, inspect completion.
  await page.goto(base+'#import');await page.getByRole('heading',{name:titles.import,exact:true}).waitFor();await page.getByLabel('Choose CSV file').setInputFiles({name:'ui-synthetic.csv',mimeType:'text/csv',buffer:Buffer.from('first_name,last_name,phone,street_address,city,state,zip,property_id,consent_business,consent_source,consent_occurred_at,consent_disclosure_version,consent_evidence_ref\nSynthetic,Operator,+12025550108,123 Demo St,Austin,TX,78704,UI-DEMO,101XVC,synthetic,2026-09-01T12:00:00Z,v1,simulation://ui-evidence\nBad,Row,invalid,10 Demo St,Austin,TX,78704,BAD,,,,,\n')});await page.getByRole('button',{name:'Preview file',exact:false}).click();await page.getByRole('heading',{name:'Column mapping',exact:true}).waitFor();await page.getByRole('button',{name:'Revalidate mapping',exact:true}).click();await page.getByRole('heading',{name:'Column mapping',exact:true}).waitFor();await page.getByRole('button',{name:'Review import',exact:false}).click();await page.getByRole('heading',{name:'Confirm this import',exact:true}).waitFor();await page.getByRole('button',{name:/Commit 1 rows/}).click();await page.getByRole('heading',{name:'Import complete',exact:true}).waitFor();console.log('CSV wizard committed 1 accepted row and retained rejected report');
  // Draft -> exact preview -> review approval.
  await page.goto(base+'#templates');await page.getByRole('heading',{name:titles.templates,exact:true}).waitFor();await page.getByLabel('Template name',{exact:true}).fill('UI reviewed inquiry');await page.getByLabel('Message',{exact:true}).fill('Hi {{first_name}}, this is {{business_name}}. Would you like to discuss {{street_address}}? Reply STOP to unsubscribe.');await page.getByRole('button',{name:'Save new version',exact:true}).click();const tplCard=page.locator('.template-list .card').filter({has:page.getByRole('heading',{name:'UI reviewed inquiry',exact:true})}).first();await tplCard.waitFor();await page.getByLabel('Saved template version').selectOption({label:'UI reviewed inquiry · v1 · draft'});await page.getByLabel('Recipient',{exact:true}).selectOption({label:'Jordan Reed · +12025550101'});await page.getByRole('button',{name:'Render exact preview',exact:true}).click();await page.locator('.sms-preview').filter({hasText:'1502 Willow Lane'}).waitFor();await tplCard.getByRole('button',{name:'Review & approve',exact:true}).click();await page.getByLabel('Review note',{exact:true}).fill('Reviewed synthetic content, business identity, merge fields, and default STOP instruction.');await page.getByRole('button',{name:'Approve immutable version',exact:true}).click();await page.getByText('Template version approved.',{exact:true}).waitFor();console.log('template saved, exact preview rendered, version approved');
  // Audience/property -> validate -> approve -> schedule.
  await page.goto(base+'#campaigns');await page.getByRole('heading',{name:titles.campaigns,exact:true}).waitFor();await page.getByRole('button',{name:'New campaign',exact:true}).click();await page.getByLabel('Campaign name').fill('UI synthetic controlled test');await page.getByLabel('Primary approved template').selectOption({label:'UI reviewed inquiry · v1'});await page.locator('.audience-row').filter({hasText:'Jordan Reed'}).getByRole('checkbox').check();await page.getByRole('button',{name:'Create draft campaign',exact:true}).click();let campaign=page.locator('tbody tr').filter({hasText:'UI synthetic controlled test'});await campaign.waitFor();await campaign.getByRole('button',{name:'Validate',exact:true}).click();await page.getByRole('heading',{name:'Campaign validation',exact:true}).waitFor();await page.getByRole('button',{name:'Close',exact:true}).click();await campaign.getByRole('button',{name:'Approve',exact:true}).click();await page.getByLabel('Approval review note').fill('Verified synthetic recipient permission and timezone, property preview, and approved template.');await page.getByRole('button',{name:'Approve campaign',exact:true}).click();await page.getByText('Campaign approved. Schedule after readiness is satisfied.',{exact:true}).waitFor();campaign=page.locator('tbody tr').filter({hasText:'UI synthetic controlled test'});await campaign.getByRole('button',{name:'Schedule',exact:true}).click();await page.getByLabel('Dispatch start in your browser timezone').fill(await page.evaluate(() => { const d = new Date(); const z = n => String(n).padStart(2,'0'); return d.getFullYear()+'-'+z(d.getMonth()+1)+'-'+z(d.getDate())+'T'+z(d.getHours())+':'+z(d.getMinutes()); }));await page.getByRole('button',{name:'Schedule campaign',exact:true}).click();await page.getByText('Campaign scheduled. Blocked recipients stay held.',{exact:true}).waitFor();console.log('campaign audience, validation, approval, scheduling passed');
  // Simulation and STOP event.
  await page.goto(base+'#diagnostics');await page.getByRole('heading',{name:titles.diagnostics,exact:true}).waitFor();await page.getByRole('button',{name:'Simulation lab',exact:true}).click();await page.getByRole('button',{name:'Run one simulated dispatch',exact:true}).click();await page.getByRole('heading',{name:'Simulation dispatch',exact:true}).waitFor();await page.locator('.modal .code-text').filter({hasText:'true'}).waitFor();await page.getByRole('button',{name:'×',exact:true}).click();await page.getByLabel('Synthetic recipient phone').fill('+12025550101');await page.getByLabel('Incoming message',{exact:true}).fill('STOP');await page.getByRole('button',{name:'Process simulated inbound',exact:true}).click();await page.getByRole('heading',{name:'Inbound simulation result',exact:true}).waitFor();await page.locator('.modal .code-text').filter({hasText:'opt_out'}).waitFor();await page.getByRole('button',{name:'×',exact:true}).click();console.log('simulated dispatch and STOP classification passed');
  await page.goto(base+'#contacts');await page.getByRole('heading',{name:titles.contacts,exact:true}).waitFor();const recipient=page.locator('tbody tr').filter({hasText:'Jordan Reed'});await recipient.getByText('Suppressed',{exact:true}).waitFor();await recipient.getByRole('button',{name:'Review',exact:true}).click();await page.getByRole('heading',{name:'Jordan Reed',exact:true}).waitFor();await page.getByText('Suppressed',{exact:true}).first().waitFor();await page.getByRole('button',{name:'×',exact:true}).click();console.log('STOP suppression visible in contact review');
  await page.setViewportSize({width:390,height:844});await page.goto(base+'#dashboard');await page.getByRole('heading',{name:'Overview',exact:true}).waitFor();await page.evaluate(() => document.querySelector('#toast-root').replaceChildren()); await page.screenshot({path:path.join(root,'docs','preview-mobile.png'),fullPage:true,animations:'disabled'});const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+2);if(overflow)throw new Error('Mobile horizontal overflow');await page.getByRole('button',{name:'Toggle navigation'}).click();await page.getByRole('link',{name:'Twilio & safeguards',exact:true}).click();await page.getByRole('heading',{name:titles.settings,exact:true}).waitFor();console.log('mobile dashboard, menu and settings rendered without overflow');
  if(errors.length||failures.length)throw new Error(JSON.stringify({errors,failures}));console.log('PASS: no browser runtime errors or failed requests');
 } finally {
  await browser?.close();
  const exited = new Promise(resolve => server.once('exit',resolve));
  if (server.exitCode === null) { server.kill('SIGTERM'); await Promise.race([exited,wait(5000)]); }
  fs.rmSync(fixture,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e.stack);process.exitCode=1});
