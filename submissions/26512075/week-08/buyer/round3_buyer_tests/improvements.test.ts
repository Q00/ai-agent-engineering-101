import {test} from "node:test";
import assert from "node:assert/strict";
import {RuntimeBudget} from "../src/runtimeBudget.ts";
import {selectPair} from "../src/forumSelection.ts";
import {chooseShop, stockCoolingDown, isStockRefusal, type TradeRecord} from "../src/marketSelection.ts";
import {reconcileOutcomes} from "../src/outcomes.ts";
import {parseOrder} from "../src/marketOrder.ts";

const shops = ["a", "b"].map(name => ({name, url:`https://${name}`, reviews:[], offers:[{sku:"sku",qty:1}]}));
const order = {round:3,item:"lamp",budget:100,kind:"goods",acceptable:["sku"]};
const trade = (overrides: Partial<TradeRecord> = {}): TradeRecord => ({shopName:"a",shopUrl:"https://a",
  orderId:"order",sku:"sku",item:"lamp",price:90,result:"completed",receiptMismatch:false,occurredAt:Date.now(),...overrides});

test("stock cooldown is SKU-specific, expires, and does not rescue excluded candidates", () => {
  const memory = [trade({negotiationReason:"out_of_stock",price:null,result:"task_state_rejected"})];
  assert.equal(selectPair(shops,order,memory,null)?.shop.name,"b");
  assert.equal(selectPair([shops[0]!],order,memory,null),null);
  assert.equal(stockCoolingDown(memory,"a","other"),false);
  assert.equal(stockCoolingDown(memory,"a","sku",memory[0]!.occurredAt!+300_000),false);
  assert.equal(isStockRefusal("Sorry, we have none of that in stock right now."),true);
  assert.equal(isStockRefusal("Payment unavailable"),false);
  assert.equal(isStockRefusal("We are not out of stock."),false);
});

test("deadline bounds network and model time, preserves cleanup, and respects order expiry", () => {
  let now = 1000;
  const budget = new RuntimeBudget(now,undefined,()=>now);
  assert.equal(budget.timeout(60_000),60_000);
  now += 260_000;
  assert.equal(budget.canRetry(),false);
  assert.equal(budget.timeout(40_000),5000);
  now += 5000;
  assert.throws(()=>budget.timeout(40_000),/deadline/);
  assert.equal(budget.cleanupTimeout(),5000);
  assert.equal(new RuntimeBudget(now,now/1000,()=>now).remaining(),0);
  const short = new RuntimeBudget(now,now/1000+30,()=>now);
  assert.equal(short.timeout(60_000),15_000);
  assert.equal(parseOrder({...order,expires:123},"lamp",100).expires,123);
  assert.throws(()=>parseOrder({...order,expires:"bad"},"lamp",100));
});

test("cheaper confirmed cohort breaks equal shop scores; mismatched or sparse evidence does not", () => {
  const memory = shops.flatMap((shop,index)=>Array.from({length:3},(_,j)=>trade({shopName:shop.name,
    price:index ? 70 : 90,paymentStatus:"paid",ledger:index*3+j,settledGrade:"A",warrantyHours:0})));
  assert.equal(chooseShop(shops,memory,"sku").shop.name,"b");
  assert.equal(selectPair(shops,order,memory,null)?.shop.name,"b");
  for (const patch of [{settledGrade:"B"},{warrantyHours:6},{paymentStatus:"refused" as const},
    {occurredAt:Date.now()-25*3600_000},{sku:"other"},{ledger:3}]) {
    const altered = memory.map(t=>t.shopName==="b" ? {...t,...patch} : t);
    assert.equal(chooseShop(shops,altered,"sku").shop.name,"a");
  }
  assert.equal(chooseShop(shops,memory.slice(1),"sku").shop.name,"a");
});

test("settlement conditions are attached only with matching ledger/order/shop/SKU", () => {
  const payments = [{order:"order",shop:"https://a",paid:true,ledger:8}];
  const purchase = {ledger:8,orderId:"order",shopUrl:"https://a",sku:"sku",grade:"A",warrantyHours:0};
  const result = reconcileOutcomes([trade()],payments,[purchase],[]).memory[0]!;
  assert.equal(result.settledGrade,"A"); assert.equal(result.warrantyHours,0);
  const mismatch = reconcileOutcomes([trade()],payments,[{...purchase,sku:"other"}],[]).memory[0]!;
  assert.equal(mismatch.settledGrade,undefined);
});
