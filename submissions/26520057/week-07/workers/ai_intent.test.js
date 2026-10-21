import assert from 'node:assert/strict';
import {resolveIntent} from './ai_intent.js';
const context = {item:'bookshelf',quote:260,quoted:true};
const mock = (kind,price,evidence) => ({run:async () => ({response:{kind,price,evidence}})});
const classify = (text, ai, ctx=context) => resolveIntent(text,[],ctx,ai,20);
let r = await classify('That works for me, let us complete the purchase',mock('accept',null,'let us complete the purchase'));
assert.equal(r.accept,true); assert.equal(r.source,'ai');
r = await classify('Nice, but I will pay 200',mock('counteroffer',200,'I will pay 200'));
assert.equal(r.offer,200); assert.equal(r.accept,false);
for (const [text,kind] of [['Can you do 200?','question'],['I will not buy it','refusal'],['I accept if you reduce it','question']]) {
  r = await classify(text,mock(kind,null,text));assert.equal(r.accept,false);assert.equal(r.offer,null);
}
for (const [text,ai,ctx] of [
  ['My budget is 360',mock('accept',null,'My budget is 360'),context],
  ['I accept 200 with thanks',mock('accept',260,'I accept 200'),context],
  ['Sounds good',mock('accept',null,'invented evidence'),context],
  ['Sounds good',mock('accept',null,'Sounds good'),{...context,quoted:false}],
  ['Sounds good',{run:async()=>({response:'invalid json'})},context],
  ['Sounds good',{run:async()=>{throw Error('quota exceeded');}},context],
  ['Sounds good',{run:()=>new Promise(()=>{})},context],
]) {
  r=await classify(text,ai,ctx);assert.equal(r.accept,false);assert.equal(r.offer,null);
}
console.log('PASS AI four-way classification, price/evidence guards, timeout and failure fallback');
