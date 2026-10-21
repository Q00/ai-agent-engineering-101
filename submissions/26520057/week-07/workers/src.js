import { DurableObject } from 'cloudflare:workers';
import { purchaseIntent } from './purchase_intent.js';
const FLOORS = { lamp: 100, chair: 180, bookshelf: 260 };
const NAME = 'Cheapest in Town';
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
      return json({name: NAME, description: 'Fictional artisan collection: Murano glass lamp 100, Hida beech chair 180, Black Forest spruce bookshelf 260 credits. High-end character, fixed floor prices, zero added fees. Item plus an eligible offer closes in one reply with a signed receipt. Craft and origin stories are simulated product lore.',
        version: '1.3.0', supportedInterfaces: [{url: url.origin + '/', protocolBinding: 'JSONRPC', protocolVersion: '1.0'}],
        defaultInputModes: ['text/plain'], defaultOutputModes: ['application/json', 'text/plain'], capabilities: {streaming: false},
        skills: [{id: 'negotiate', name: 'Negotiate a purchase', description: 'Send buy lamp for 100, buy chair for 180, or buy bookshelf for 260. Or request a quote and reply I accept. Offers above the fixed price still pay only the fixed price.', tags: ['shopping', 'negotiation'], examples: ['I want to buy a lamp', '100']}]});
    }
    if (url.pathname !== '/') return new Response('Not found', {status:404});
    if (request.method !== 'POST') return new Response('A2A shop: see /.well-known/agent-card.json', {status:200});
    return env.SHOP.get(env.SHOP.idFromName('cheapest-in-town')).fetch(request);
  }
};

export class Shop extends DurableObject {
  constructor(ctx, env) { super(ctx, env); }
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
    const setStatus = (state, text) => {
      record.task.metadata = record.item ? {ask:FLOORS[record.item]} : {};
      record.task.status = {state, timestamp:new Date().toISOString(), message:{role:'ROLE_AGENT', messageId:crypto.randomUUID(), taskId:record.task.id, contextId:record.task.contextId, parts:[{text}], metadata:record.item ? {ask:FLOORS[record.item]} : {}}};
    };
    const text = (msg?.parts || []).map(p => p.text || '').join(' ');
    const intent = purchaseIntent(text, msg?.parts || []);
    console.log('[negotiation]', JSON.stringify({task:id || null, text:text.slice(0,350), ...intent}));
    const finish = async () => {
      const price = FLOORS[record.item];
      const receipt = await this.sign({shop:NAME,item:record.item,price,task:record.task.id});
      record.task.artifacts = [{artifactId:crypto.randomUUID(),name:'receipt',parts:[{data:receipt}]}];
      setStatus('TASK_STATE_COMPLETED',`Deal: ${record.item} at ${price}. Zero added fees; your signed receipt records this exact item, price and task. Same listed price on your next visit. Please review your actual experience.`);
    };
    if (method === 'CancelTask') {
      setStatus('TASK_STATE_CANCELED', 'Canceled. The same fixed prices are available on your next visit.');
    } else if (!record) {
      if (!this.env.SHOP_PRIVATE_KEY) fault(-32603, 'Signing key is not configured');
      record = {task:{id:crypto.randomUUID(),contextId:msg.contextId || crypto.randomUUID()}, counters:0};
      record.item = Object.keys(FLOORS).find(k => new RegExp('\\b' + k + '\\b', 'i').test(text));
      if (!record.item) setStatus('TASK_STATE_REJECTED','We sell lamp, chair and bookshelf.');
      else {
        record.ask = FLOORS[record.item];
        const offer = intent.offer;
        if (offer !== null && offer >= record.ask) await finish();
        else setStatus('TASK_STATE_INPUT_REQUIRED',`${STORIES[record.item]} ${PITCH} ${record.item}: fixed price ${record.ask}. Reply I accept or offer at least this price. Price: ${record.ask}`);
      }
    } else {
      const floor = FLOORS[record.item];
      record.ask = floor;
      const offer = intent.offer;
      const accepts = intent.accept;
      if (accepts || (offer !== null && offer >= floor)) await finish();
      else {
        record.counters = (record.counters || 0) + 1;
        setStatus('TASK_STATE_INPUT_REQUIRED',`${STORIES[record.item]} ${PITCH} This is my final price for ${record.item}; no further discount is possible. Reply with ${floor} or I accept to complete the purchase. Counteroffer: ${floor}`);
      }
    }
    await storage.put('task:' + record.task.id,record);
    return record.task;
  }
}
