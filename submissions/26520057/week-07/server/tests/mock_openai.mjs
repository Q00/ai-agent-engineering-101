// Test-only preload. Never load this module in a deployed service.
import {purchaseIntent} from '../../workers/purchase_intent.js';
const realFetch=globalThis.fetch;
globalThis.fetch=async(url,init)=>{
 if(url !== 'https://api.openai.com/v1/responses') return realFetch(url,init);
 const body=JSON.parse(init.body), message=JSON.parse(body.input[1].content);
 const intent=purchaseIntent(message.text);
 const kind=intent.accept?'accept':intent.offer!==null?'counteroffer':'question';
 return Response.json({status:'completed',output:[{type:'message',content:[{type:'output_text',text:JSON.stringify({kind,price:intent.offer,evidence:message.text})}]}]});
};
