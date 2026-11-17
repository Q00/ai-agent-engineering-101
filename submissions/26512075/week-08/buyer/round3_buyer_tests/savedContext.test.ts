import {test} from "node:test";
import assert from "node:assert/strict";
import {savedScenario, readSavedContext} from "../src/savedContext.ts";

test("same discount has different margin scenarios, never an exact score", () => {
  const furniture = savedScenario({list_price:100,category:"가구/침구>침대"},70)!;
  const food = savedScenario({list_price:100,kind:"food"},70)!;
  assert.ok(Math.abs(furniture.savedAtAskScenario! - 30/39) < 1e-12);
  assert.equal(food.savedAtAskScenario, 0.625);
  assert.equal(food.actualCostKnown,false);
  assert.equal(food.estimateOnly,true);
});
test("cap upside only; preserve above-list losses and unknown cost", () => {
  assert.equal(savedScenario({list_price:100,kind:"food"},40)!.savedAtAskScenario,1);
  assert.ok(savedScenario({list_price:100,kind:"food"},110)!.savedAtAskScenario! < 0);
  assert.equal(savedScenario({list_price:100,category:"전자제품"},70)!.savedAtAskScenario,null);
  assert.equal(savedScenario({list_price:0,kind:"food"},70),null);
  assert.equal(savedScenario({list_price:100,kind:"food"},NaN),null);
  assert.equal(readSavedContext("missing",70,"/nonexistent-saved-catalog"),null);
});
