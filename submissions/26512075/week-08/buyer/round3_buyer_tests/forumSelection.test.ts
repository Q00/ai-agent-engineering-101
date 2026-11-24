import {test} from "node:test";
import assert from "node:assert/strict";
import {generateKeyPairSync, sign} from "node:crypto";
import {selectPair, verifiedComps} from "../src/forumSelection.ts";
import {acceptance} from "../src/marketOrder.ts";

const shops = [
  {name:"a",url:"https://a",reviews:[],offers:[{sku:"G11111111",qty:1}]},
  {name:"b",url:"https://b",reviews:[],offers:[{sku:"G22222222",qty:1}]}
];
const order = {round:3,item:"goods",budget:100,kind:"goods",acceptable:["G11111111","G22222222"]};
const report = {coverage_ready:true,volume_band:{min:3,max:8,hours:3},medium_candidates:[
  {sku:"G22222222",sample_settlements:5,forum_posts_mentioning_sku:10}
]};

test("medium-volume evidence chooses a specific shop and permitted SKU", () => {
  const pair=selectPair(shops,order,[],report);
  assert.ok(pair);
  assert.equal(pair.shop.name,"b"); assert.equal(pair.sku,"G22222222");
});
test("incomplete coverage and unavailable SKUs fall back to existing shop scoring", () => {
  assert.equal(selectPair(shops,order,[],{...report,coverage_ready:false})?.shop.name,"a");
  assert.equal(selectPair(shops,{...order,acceptable:["G11111111"]},[],report)?.sku,"G11111111");
});
test("price comps require a market-signed grade and exact known warranty", () => {
  const {privateKey,publicKey}=generateKeyPairSync("ed25519");
  const market_pubkey=publicKey.export({format:"der",type:"spki"}).subarray(-32).toString("base64");
  const cert={grade:"B",sku:"G11111111",unit:"u"};
  const sig=sign(null,Buffer.from(JSON.stringify(cert)),privateKey).toString("base64");
  const meta={unit:"u",cert:{cert,sig},offered_warranty_hours:0};
  const analysis={generated_at:1000,condition_candidates:[{sku:cert.sku,grade:"B",warranty_hours:0,
    warranty_status:"known",sample_settlements:3,sample_median_settled_price:80}]};
  assert.equal(verifiedComps(meta,cert.sku,{...order,market_pubkey},analysis,1001)?.median,80);
  assert.equal(verifiedComps({...meta,offered_warranty_hours:6},cert.sku,{...order,market_pubkey},analysis,1001),null);
  assert.equal(verifiedComps({...meta,cert:{cert:{...cert,grade:"A"},sig}},cert.sku,{...order,market_pubkey},analysis,1001),null);
  assert.equal(verifiedComps(meta,cert.sku,{...order,market_pubkey},analysis,1400),null);
});
test("buyer's warranty requirement is independently configurable", () => {
  const previous=process.env.BUYER_MIN_WARRANTY_HOURS;
  try {
    process.env.BUYER_MIN_WARRANTY_HOURS="6";
    assert.deepEqual(acceptance(order),{warranty_hours:6});
    process.env.BUYER_MIN_WARRANTY_HOURS="73";
    assert.throws(()=>acceptance(order));
  } finally {
    if(previous===undefined) delete process.env.BUYER_MIN_WARRANTY_HOURS;
    else process.env.BUYER_MIN_WARRANTY_HOURS=previous;
  }
});
