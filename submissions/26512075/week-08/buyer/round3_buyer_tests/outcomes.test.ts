import {test} from "node:test";
import assert from "node:assert/strict";
import {reconcileOutcomes} from "../src/outcomes.ts";
import {chooseShop, type TradeRecord} from "../src/marketSelection.ts";

const memory: TradeRecord[] = [{shopName:"a",shopUrl:"https://a",orderId:"o1",item:"goods",
  price:100,result:"completed",receiptMismatch:false}];
const paid = [{order:"o1",shop:"https://a",paid:true,ledger:1}];
const purchases = [{ledger:0,name:"other",observedAt:0},
  {ledger:1,orderId:"o1",shopUrl:"https://a",name:"lamp",observedAt:22000,warrantyHours:6}];

test("payment reconciliation is idempotent and does not equate agreement with payment", () => {
  const first=reconcileOutcomes(memory,paid,[],[]).memory;
  assert.equal(first[0]!.paymentStatus,"paid");
  assert.deepEqual(reconcileOutcomes(first,paid,[],[]).memory,first);
  assert.equal(memory[0]!.paymentStatus,undefined);
  assert.equal(reconcileOutcomes(memory,[{...paid[0],shop:"https://other"}],[],[]).memory[0]!.paymentStatus,undefined);
  const refused=reconcileOutcomes(memory,[{order:"o1",shop:"https://a",paid:false,reason:"not in stock"}],[],[]).memory;
  assert.equal(refused[0]!.paymentStatus,"refused");
  const shops=[{name:"a",url:"https://a",reviews:[]},{name:"b",url:"https://b",reviews:[]}];
  assert.equal(chooseShop(shops,refused).shop.name,"b");
});
test("unique breakage links only to a settled purchase; coverage does not assert refund", () => {
  const r=reconcileOutcomes(memory,paid,purchases,[{name:"lamp",broke_at:23000}]);
  assert.equal(r.memory[0]!.brokenAt,23000);
  assert.equal(r.memory[0]!.warrantyEligible,true);
  assert.equal(r.ambiguousBreaks,0);
  assert.equal(reconcileOutcomes(memory,[],purchases,[{name:"lamp",broke_at:23000}]).memory[0]!.brokenAt,undefined);
});
test("same-name purchases and incomplete six-hour history remain unresolved", () => {
  for (const journal of [purchases.slice(1),[...purchases,{...purchases[1],ledger:2}]]) {
    const r=reconcileOutcomes(memory,paid,journal,[{name:"lamp",broke_at:23000}]);
    assert.equal(r.memory[0]!.brokenAt,undefined);assert.equal(r.ambiguousBreaks,1);
  }
});
test("conflicting payment records are not guessed", () => {
  assert.equal(reconcileOutcomes(memory,[...paid,{...paid[0],paid:false}],[],[]).memory[0]!.paymentStatus,undefined);
});
