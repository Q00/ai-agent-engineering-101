import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';


import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const root = process.cwd();
const invoke = (order, shops, expectedSku, receiptSku = expectedSku, includeOrder = true, agreedPrice = "70") => {
  const tmp = mkdtempSync(join(tmpdir(), 'buyer-r3-'));
  try {
    writeFileSync(join(tmp,'order.json'), JSON.stringify(order));
    writeFileSync(join(tmp,'shops.json'), JSON.stringify(shops));
    const p = spawnSync(process.execPath, ['--no-warnings','--experimental-strip-types','--loader',resolve(root,'round3_buyer_tests/ts-loader.mjs'),'--import',resolve(root,'round3_buyer_tests/mock-fetch.mjs'),resolve(root,'src/market_buyer_r3.ts'),'--shops',join(tmp,'shops.json'),'--item',order.item,'--budget',String(order.budget),...(includeOrder ? ['--order',join(tmp,'order.json')] : [])], {encoding:'utf8', cwd:tmp, env:{...process.env, EXPECT_SKU:expectedSku, RECEIPT_SKU:receiptSku, AGREED_PRICE:agreedPrice}});
    assert.equal(p.status, 0, p.stderr);
    return JSON.parse(p.stdout.trim().split('\n').at(-1));
  } finally { rmSync(tmp, {recursive:true,force:true}); }
};
const order = {round:3,item:'milk',budget:100,kind:'food',acceptable:['G11111111'],use_within_hours:8};
const shop = {name:'x',url:'https://mock.invalid',reviews:[],offers:[{sku:'G11111111',qty:1}]};
test('food order requests acceptable in-stock SKU and emits policy', () => {
  const r = invoke(order,[shop], 'G11111111');
  assert.equal(r.receipt.item, 'G11111111'); assert.deepEqual(r.accept,{min_hours_left:8});
});
test('reject another acceptable receipt item before checkout', () => {
  const r = invoke(order,[shop], 'G11111111', 'G22222222');
  assert.equal(r.receipt,null);
});
test('no valid offers returns no deal without contacting shop', () => {
  const r = invoke(order,[{...shop,offers:[]}], 'impossible');
  assert.equal(r.receipt,null); assert.equal(r.shop,'');
});
test('malformed order returns no deal without contacting shop', () => {
  const r = invoke({...order,acceptable:[]},[shop], 'impossible');
  assert.equal(r.receipt,null); assert.equal(r.shop,'');
});

test('Round 2 still works with and without --order', () => {
  const oldOrder = {round:2,item:'lamp',budget:100};
  const oldShop = {name:'x',url:'https://mock.invalid',reviews:[]};
  for (const includeOrder of [true,false]) {
    const result = invoke(oldOrder,[oldShop], 'lamp', 'lamp', includeOrder);
    assert.equal(result.receipt.item, 'lamp');
    assert.equal('accept' in result, false);
  }
});

test('explicitly mismatched agreed price refuses receipt', () => {
  const r = invoke(order,[shop], 'G11111111', 'G11111111', true, '80');
  assert.equal(r.receipt, null);
});





