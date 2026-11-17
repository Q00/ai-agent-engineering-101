import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,existsSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

function invoke(mode, expiry) {
  const dir = mkdtempSync(join(tmpdir(),'buyer-retry-'));
  try {
    const calls = join(dir,'calls');
    const order = {round:3,item:'lamp',budget:100,kind:'goods',acceptable:['sku'],order:'order',
      ...(expiry === undefined ? {} : {expires:expiry})};
    const shops = ['a','b','c','d'].map(name=>({name,url:`https://${name}.invalid`,reviews:[],offers:[{sku:'sku',qty:1}]}));
    writeFileSync(join(dir,'order.json'),JSON.stringify(order));
    writeFileSync(join(dir,'shops.json'),JSON.stringify(shops));
    const p = spawnSync(process.execPath,['--import',fileURLToPath(new URL('../../node_modules/tsx/dist/loader.mjs',import.meta.url)),
      '--import',fileURLToPath(new URL('./retry-fetch.mjs',import.meta.url)),fileURLToPath(new URL('../src/market_buyer_r3.ts',import.meta.url)),
      '--shops',join(dir,'shops.json'),'--item','lamp','--budget','100','--order',join(dir,'order.json')],
      {cwd:dir,encoding:'utf8',timeout:15000,env:{...process.env,CALLS_PATH:calls,RETRY_MODE:mode,MARKET_DIR:dir,OWN_SHOP_URL:''}});
    assert.ifError(p.error); assert.equal(p.status,0,p.stderr);
    return {result:JSON.parse(p.stdout.trim().split('\n').at(-1)),
      calls:existsSync(calls)?readFileSync(calls,'utf8'):'',
      memory:existsSync(join(dir,'buyer_memory.json'))?JSON.parse(readFileSync(join(dir,'buyer_memory.json'),'utf8')):[]};
  } finally { rmSync(dir,{recursive:true,force:true}); }
}
test('explicit stock rejection retries another shop and retains both outcomes',()=>{
  const r=invoke('stock');
  assert.equal(r.result.shop,'https://b.invalid'); assert.equal(r.result.receipt.price,70);
  assert.equal(r.memory.length,2); assert.equal(r.memory[0].negotiationReason,'out_of_stock');
  assert.equal(r.memory[1].result,'completed');
});
test('stock retries stop after three shops',()=>{
  const r=invoke('all-stock'); assert.equal(r.result.receipt,null);
  assert.equal(r.memory.length,3); assert.ok(!r.calls.includes('d.invalid'));
});
test('uncertain network, generic rejection, and mismatched receipt never retry',()=>{
  for(const mode of ['timeout','generic','mismatch','stock-with-receipt']) {
    const r=invoke(mode); assert.equal(r.result.receipt,null);
    assert.equal(r.memory.length,1); assert.ok(!r.calls.includes('b.invalid'));
  }
});
test('expired orders perform no network requests',()=>{
  const r=invoke('stock',Date.now()/1000-1);
  assert.equal(r.calls,''); assert.equal(r.result.receipt,null);
});
