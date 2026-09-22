/* Import the native scene into excalidraw.com and inspect/export through its UI. */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const ROOT = __dirname;
const STEM=process.argv[3] || 'week-03-architecture';
if(!/^[a-z0-9-]+$/.test(STEM)) throw new Error('Invalid scene filename');
const PREFIX=STEM==='week-03-architecture'?'architecture':STEM;
const LINK_NAME=STEM==='week-03-architecture'?'excalidraw-link.json':STEM+'-link.json';
const expected=JSON.parse(fs.readFileSync(path.join(ROOT,STEM+'.excalidraw'),'utf8')).elements;
(async () => {
  const browser = await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context = await browser.newContext({viewport:{width:2400,height:1880},locale:'en-US'});
  const page = await context.newPage();
  await page.addInitScript(() => { delete window.showOpenFilePicker; delete window.showSaveFilePicker; });
  const errors=[];
  page.on('pageerror', e=>errors.push(e.message));
  await page.goto('https://excalidraw.com/',{waitUntil:'domcontentloaded'});
  await page.getByRole('button',{name:/^(열기|Open)/}).first().waitFor({timeout:45000});
  console.log('picker support',await page.evaluate(()=>('showOpenFilePicker' in window)));
  const [chooser]=await Promise.all([page.waitForEvent('filechooser',{timeout:10000}),page.getByRole('button',{name:/^(열기|Open)/}).first().click()]);
  await chooser.setFiles(path.join(ROOT,STEM+'.excalidraw'));
  await page.waitForTimeout(1800);
  console.log('after import',await page.locator('body').innerText());
  await page.keyboard.press('Escape');
  await page.keyboard.press('Shift+Digit1');
  await page.waitForTimeout(1200);
  await page.screenshot({path:path.join(ROOT,PREFIX+'-preview.png')});
  console.log(JSON.stringify({errors,body:(await page.locator('body').innerText()).slice(-2000),storageKeys:await page.evaluate(()=>Object.keys(localStorage))}));
  console.log(JSON.stringify(await page.locator('button').evaluateAll(bs=>bs.map(b=>({text:b.innerText,label:b.getAttribute('aria-label'),title:b.getAttribute('title'),'data-testid':b.getAttribute('data-testid')})))));
  if (process.argv[2] === 'share') {
    await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:'https://excalidraw.com'});
    await page.getByRole('button',{name:'Share',exact:true}).click();
    console.log('SHARE_DIALOG',await page.locator('body').innerText());
    console.log('DIALOG_BUTTONS',JSON.stringify(await page.locator('button').evaluateAll(bs=>bs.map(b=>({text:b.innerText,label:b.getAttribute('aria-label')})))));
    await page.getByRole('button',{name:'Export to Link',exact:true}).click();
    await page.waitForFunction(()=>Array.from(document.querySelectorAll('input')).some(i=>i.value.includes('excalidraw.com/#json=')),null,{timeout:30000});
    const url=await page.locator('input').evaluateAll(inputs=>inputs.map(i=>i.value).find(v=>v.includes('excalidraw.com/#json=')));
    if(!/^https:\/\/excalidraw\.com\/#json=[A-Za-z0-9_-]+,[A-Za-z0-9_-]+$/.test(url)) throw new Error('Unexpected share link');
    const verificationContext=await browser.newContext({viewport:{width:2400,height:1880},locale:'en-US'});
    const verificationPage=await verificationContext.newPage();
    await verificationPage.goto(url,{waitUntil:'domcontentloaded'});
    await verificationPage.waitForFunction(count=>{try{return JSON.parse(localStorage.getItem('excalidraw')).length===count}catch{return false}},expected.length,{timeout:45000});
    const restored=await verificationPage.evaluate(()=>JSON.parse(localStorage.getItem('excalidraw')));
    if(JSON.stringify(restored.map(e=>e.id).sort())!==JSON.stringify(expected.map(e=>e.id).sort())) throw new Error('Shared scene IDs differ');
    for(const e of expected.filter(e=>e.type==='text')) {
      if(restored.find(r=>r.id===e.id)?.text!==e.text) throw new Error('Shared text differs: '+e.id);
    }
    await verificationPage.keyboard.press('Escape');
    await verificationPage.keyboard.press('Shift+Digit1');
    await verificationPage.waitForTimeout(800);
    await verificationPage.screenshot({path:path.join(ROOT,PREFIX+'-shared-preview.png')});
    const result={url,verified:true,elements:restored.length,textElements:restored.filter(e=>e.type==='text').length,verifiedAt:new Date().toISOString(),kind:'Excalidraw snapshot link; save a local file to retain later edits'};
    fs.writeFileSync(path.join(ROOT,LINK_NAME),JSON.stringify(result,null,2)+'\n');
    console.log('VERIFIED_SHARE',JSON.stringify(result));
    await verificationContext.close();
  }
  await browser.close();
})().catch(e=>{console.error(e.stack);process.exit(1)});
