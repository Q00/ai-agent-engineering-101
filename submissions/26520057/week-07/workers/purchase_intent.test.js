import assert from 'node:assert/strict';
import {purchaseIntent as parse} from './purchase_intent.js';
for (const text of ['260', '260.0', 'I offer 260.', 'I accept 260.', 'I accept the price of 260.', "I'll take it at 260 credits.", 'I accept your offer at 260.']) {
  assert.equal(parse(text).offer, 260, text);
}
for (const text of ['I accept your price.', 'I accept the offer.', "I'll take it", 'OK']) {
  assert.equal(parse(text).accept, true, text);
}
for (const text of ['I do not accept', 'I accept if you reduce it', 'budget 360, order 260', 'I offer 260.5.']) {
  assert.deepEqual(parse(text), {offer:null,accept:false}, text);
}
assert.deepEqual(parse('I accept 200.'), {offer:200,accept:false});
assert.equal(parse('', [{data:{action:'offer',price:260}}]).offer,260);
console.log('PASS explicit purchase expressions and non-purchase guards');
