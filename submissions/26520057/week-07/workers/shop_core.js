import { resolveIntent } from './ai_intent.js';
import { openAI } from './openai.js';
const FLOORS = { lamp: 100, chair: 180, bookshelf: 260 };
const NAME = 'Cheapest in Town';
const RETURN_WINDOW_MS = 60 * 60 * 1000;
const CARE = {
  lamp: 'Unplug before cleaning. Use a dry soft cloth on the shade; keep electrical parts dry.',
  chair: 'Wipe with a soft dry cloth, keep away from prolonged moisture, and periodically check joints.',
  bookshelf: 'Distribute weight evenly, place on a level surface, and use suitable wall anchoring to prevent tipping.'
};
// A visit code proves possession of a prior task, not a verified buyer identity.
export async function returnVisit(storage, message, now = Date.now()) {
  const code = message.metadata?.returnTaskId;
  if (typeof code !== 'string' || !/^[a-f0-9-]{36}$/.test(code)) return false;
  const prior = await storage.get('task:' + code);
  const age = now - prior?.visitedAt;
  return Boolean(prior?.item && age >= 0 && age <= RETURN_WINDOW_MS);
}
const RETURN_PITCH = 'Return within one hour with your visit code: we accept your desired price or lower, subject to our minimum selling price. Below that minimum, eligible returning guests receive our exclusive Premium AS digital care-and-receipt support bundle at the minimum price. No rating promise needed.';
// Fictional merchandise lore for the course's simulated market, not provenance certificates.
const STORIES = {
  lamp: 'Murano Atelier lamp: in our fictional collection, Venetian artisans in Murano, Italy hand-blow its layered glass shade and hand-finish its brass base. A high-end lighting story at an everyday price.',
  chair: 'Hida Takumi chair: in our fictional collection, artisans in Hida, Gifu, Japan shape locally sourced beech into a curved backrest with carefully fitted wooden joints. Boutique craftsmanship without a boutique markup.',
  bookshelf: 'Black Forest Atelier bookshelf: in our fictional collection, cabinetmakers in Germany\'s Black Forest build it from regional spruce, with hand-finished edges and a warm natural grain. A study-worthy centerpiece at our fixed floor price.'
};
const PITCH = 'Three essentials. Zero added fees. No rating promise required. Exact-price Ed25519-signed receipts.';
const json = (value, status = 200) => Response.json(value, { status });
const fault = (code, message) => { throw Object.assign(new Error(message), { code }); };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/.well-known/agent-card.json') {
      return json({name: NAME, description: 'Return within 1 hour with your visit code: your price or lower, subject to our floors (lamp 100/chair 180/bookshelf 260). Below-floor return offers unlock Premium AS digital care + receipt support at the floor. Zero fees. Fictional furniture; no physical repair or refund service.',
        version: '1.6.0', supportedInterfaces: [{url: url.origin + '/', protocolBinding: 'JSONRPC', protocolVersion: '1.0'}],
        defaultInputModes: ['text/plain'], defaultOutputModes: ['application/json', 'text/plain'], capabilities: {streaming: false},
        skills: [{id: 'negotiate', name: 'Negotiate a purchase', description: 'Send buy lamp for 100, buy chair for 180, or buy bookshelf for 260. Or request a quote and reply I accept. Offers above the fixed price still pay only the fixed price. For a new visit, copy the previous task ID to message.metadata.returnTaskId; do not reuse a completed task as taskId.', tags: ['shopping', 'negotiation'], examples: ['I want to buy a lamp', '100']}]});
    }
    if (url.pathname !== '/') return new Response('Not found', {status:404});
    if (request.method !== 'POST') return new Response('A2A shop: see /.well-known/agent-card.json', {status:200});
    return env.SHOP.get(env.SHOP.idFromName('cheapest-in-town')).fetch(request);
  }
};

export class ShopCore {
  constructor(ctx, env) { this.ctx = ctx; this.env = env; }
  async fetch(request) {
    let call;
    try { call = await request.json(); } catch { return json({jsonrpc:'2.0', id:null, error:{code:-32700,message:'Invalid JSON'}}); }
    // Serialize the complete update, including signing, to avoid duplicate sales.
    return this.ctx.blockConcurrencyWhile(async () => {
      try {
        if (call.jsonrpc !== '2.0' || typeof call.method !== 'string') fault(-32600,'Invalid request');
        const result = await this.run(call.method, call.params || {});
        return json({jsonrpc:'2.0', id:call.id ?? null, result:{task:result}});
      } catch (e) {
        console.error('RPC failure', e.code || -32603, e.message);
        return json({jsonrpc:'2.0',id:call.id ?? null,error:{code:e.code || -32603,message:e.code ? e.message : 'Internal error'}});
      }
    });
  }
  quote(record) {
    const floor = FLOORS[record.item];
    const benefit = record.premiumAS
      ? `Your return visit qualifies. Your requested price is below our minimum selling price, so I cannot lower it further. Instead, at ${floor} I will include our exclusive Premium AS digital support bundle: item-specific care guidance and retrieval of your signed receipt. You get the same fictional premium collection, no added fees, and this extra support at our lowest price. No physical repairs or refunds are included. Would you accept ${floor} with Premium AS?`
      : `${STORIES[record.item]} ${PITCH} ${RETURN_PITCH}`;
    return `${benefit} Visit code: ${record.task.id}; for a new visit provide message.metadata.returnTaskId. Reply I accept or offer at least our minimum. Price: ${floor}`;
  }
  async sign(receipt) {
    const pem = this.env.SHOP_PRIVATE_KEY;
    if (!pem) fault(-32603, 'Signing key is not configured');
    const bytes = Uint8Array.from(atob(pem.replace(/-----[^-]+-----|\s/g,'')), c => c.charCodeAt(0));
    const key = await crypto.subtle.importKey('pkcs8', bytes, {name:'Ed25519'}, false, ['sign']);
    const body = Object.fromEntries(Object.keys(receipt).sort().map(k => [k, receipt[k]]));
    const sig = await crypto.subtle.sign('Ed25519', key, new TextEncoder().encode(JSON.stringify(body)));
    return {...receipt, sig_seller:btoa(String.fromCharCode(...new Uint8Array(sig)))};
  }
  async run(method, p) {
    if (!['SendMessage','GetTask','CancelTask'].includes(method)) fault(-32601,'Method not found');
    const storage = this.ctx.storage;
    const msg = p.message;
    if (method === 'SendMessage' && (!msg || !Array.isArray(msg.parts))) fault(-32602,'message.parts is required');
    const id = method === 'SendMessage' ? msg.taskId : (p.id || p.taskId);
    let record = id ? await storage.get('task:' + id) : undefined;
    if (id && !record) fault(-32001,'Task not found');
    if (method !== 'SendMessage' && !id) fault(-32602,'Task id is required');
    if (method === 'GetTask') return record.task;
    if (record && record.task.status.state !== 'TASK_STATE_INPUT_REQUIRED') fault(-32002,'Task is already finished');
    if (record && !record.visitedAt) record.visitedAt = Date.now();
    const setStatus = (state, text) => {
      record.task.metadata = record.item ? {ask:FLOORS[record.item], returnTaskId:record.task.id, returnUntil:new Date(record.visitedAt + RETURN_WINDOW_MS).toISOString(), returnEligible:Boolean(record.returnEligible), premiumAS:Boolean(record.premiumAS)} : {};
      record.task.status = {state, timestamp:new Date().toISOString(), message:{role:'ROLE_AGENT', messageId:crypto.randomUUID(), taskId:record.task.id, contextId:record.task.contextId, parts:[{text}], metadata:record.item ? {ask:FLOORS[record.item]} : {}}};
    };
    const text = (msg?.parts || []).map(p => p.text || '').join(' ');
    const item = record?.item || Object.keys(FLOORS).find(k => new RegExp('\\b' + k + '\\b', 'i').test(text));
    const intent = method === 'SendMessage' ? await resolveIntent(text, msg.parts,
      {item, quote:FLOORS[item], quoted:Boolean(record)}, openAI(this.env)) : {offer:null,accept:false};
    console.log('[negotiation]', JSON.stringify({task:id || null, text:text.slice(0,350), ...intent}));
    const finish = async () => {
      const price = FLOORS[record.item];
      if (record.premiumAS) record.task.metadata = {...record.task.metadata, premiumAS:true};
      const receipt = await this.sign({shop:NAME,item:record.item,price,task:record.task.id});
      record.task.artifacts = [{artifactId:crypto.randomUUID(),name:'receipt',parts:[{data:receipt}]}];
      if (record.premiumAS) record.task.artifacts.push({artifactId:crypto.randomUUID(),name:'premium-as',parts:[{data:{
        service:'Premium AS digital support', item:record.item, careGuide:CARE[record.item],
        receiptSupport:{method:'GetTask',params:{id:record.task.id}, instructions:'Retrieve this task to recover your original signed receipt and check its item and price.'},
        scope:'Digital care guidance and receipt retrieval only. No physical repair, replacement or refund is promised.'
      }}]});
      console.log('[checkout]', JSON.stringify({task:record.task.id,item:record.item,price}));
      setStatus('TASK_STATE_COMPLETED',`Deal: ${record.item} at ${price}. Zero added fees; your signed receipt records this exact item, price and task. ${record.premiumAS ? 'Your exclusive Premium AS bundle is attached: care guidance and signed receipt retrieval; no physical repair or refunds. ' : ''}${RETURN_PITCH} Visit code: ${record.task.id}; provide it as message.metadata.returnTaskId on a new task. Please review your actual experience.`);
    };
    if (method === 'CancelTask') {
      setStatus('TASK_STATE_CANCELED', 'Canceled. The same fixed prices are available on your next visit.');
    } else if (!record) {
      if (!this.env.SHOP_PRIVATE_KEY) fault(-32603, 'Signing key is not configured');
      record = {task:{id:crypto.randomUUID(),contextId:msg.contextId || crypto.randomUUID()}, counters:0, visitedAt:Date.now(), returnEligible:await returnVisit(storage,msg)};
      record.item = Object.keys(FLOORS).find(k => new RegExp('\\b' + k + '\\b', 'i').test(text));
      if (!record.item) setStatus('TASK_STATE_REJECTED','We sell lamp, chair and bookshelf.');
      else {
        record.ask = FLOORS[record.item];
        const offer = intent.offer;
        if (record.returnEligible && offer !== null && offer < record.ask) record.premiumAS = true;
        if (offer !== null && offer >= record.ask) await finish();
        else setStatus('TASK_STATE_INPUT_REQUIRED',this.quote(record));
      }
    } else {
      const floor = FLOORS[record.item];
      record.ask = floor;
      const offer = intent.offer;
      const accepts = intent.accept;
      if (record.returnEligible && offer !== null && offer < floor) record.premiumAS = true;
      if (accepts || (offer !== null && offer >= floor)) await finish();
      else {
        record.counters = (record.counters || 0) + 1;
        setStatus('TASK_STATE_INPUT_REQUIRED',this.quote(record));
      }
    }
    await storage.put('task:' + record.task.id,record);
    return record.task;
  }
}
