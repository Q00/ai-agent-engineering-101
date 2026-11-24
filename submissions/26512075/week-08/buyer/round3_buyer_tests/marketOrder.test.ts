import { test } from "node:test";
import assert from "node:assert/strict";
import { parseOrder, matchingSkus, acceptance, receiptFitsSku, receiptFitsBudget } from "../src/marketOrder.ts";


const base = {round: 3, item: "milk", budget: 500, kind: "food", acceptable: ["G12345678", "G11111111"], use_within_hours: 4};
const order = () => parseOrder(base, "milk", 500);

test("food use_within_hours maps to checkout policy", () => {
  assert.deepEqual(acceptance(order()), {min_hours_left: 4});
});
test("goods and Round 2 do not impose food policy", () => {
  assert.equal(acceptance(parseOrder({...base, kind: "goods"}, "milk", 500)), undefined);
  assert.equal(acceptance(parseOrder({round: 2, item: "lamp", budget: 100}, "lamp", 100)), undefined);
});
test("candidate SKUs must be both acceptable and in-stock with positive integer quantity", () => {
  assert.deepEqual(matchingSkus({offers: [{sku:"G11111111", qty: 2}, {sku:"G12345678", qty: 0}, {sku:"G99999999", qty: 9}]}, order()), ["G11111111"]);
  assert.deepEqual(matchingSkus({offers: [{sku: "G12345678", qty: -1}, {sku: "G11111111", qty: 0.5}]}, order()), []);
  assert.deepEqual(matchingSkus({}, order()), []);
});
test("malformed or conflicting order does not fall back", () => {
  for (const bad of [null, {}, {...base, acceptable: []}, {...base, acceptable: [null]}, {...base, kind: "food", use_within_hours: null}, {...base, kind: "x"}, {...base, budget: 501}, {...base, item: "other"}, {...base, round: true}]) {
    assert.throws(() => parseOrder(bad, "milk", 500));
  }
});
test("receipt item and price must match selection and budget", () => {
  assert.equal(receiptFitsSku({item:"G12345678"}, "G12345678"), true);
  assert.equal(receiptFitsSku({item:"G11111111"}, "G12345678"), false);
  for (const price of [0, 501, 1.5, null, "300"]) assert.equal(receiptFitsBudget({price}, 500), false);
  assert.equal(receiptFitsBudget({price: 500}, 500), true);
});








