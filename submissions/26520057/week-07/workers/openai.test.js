import assert from 'node:assert/strict';
import {openAI} from './openai.js';
const env={OPENAI_API_KEY:'fake-test-key'};
const schema={type:'object',properties:{},additionalProperties:false};
assert.equal(openAI({}),null);
let called=false;
const adapter=openAI(env,async(url,init)=>{
 called=true;assert.equal(url,'https://api.openai.com/v1/responses');
 const b=JSON.parse(init.body);assert.equal(b.model,'gpt-5.4-mini');assert.equal(b.store,false);
 assert.equal(b.reasoning.effort,'none');assert.equal(b.text.format.strict,true);
 assert.ok(init.signal);assert.equal(init.headers.Authorization,'Bearer fake-test-key');
 return Response.json({status:'completed',output:[{type:'message',content:[{type:'output_text',text:'{"kind":"accept","price":null,"evidence":"OK"}'}]}]});
});
assert.equal((await adapter.run({messages:[],schema})).response.kind,'accept');assert.ok(called);
for(const [body,status] of [[{},401],[{},429],[{status:'incomplete'},200],[{status:'completed',output:[{type:'message',content:[{type:'refusal'}]}]},200]]) {
 await assert.rejects(()=>openAI(env,async()=>Response.json(body,{status})).run({messages:[],schema}));
}
console.log('PASS GPT adapter schema, model, no storage, abort signal, refusal/incomplete/HTTP errors');
