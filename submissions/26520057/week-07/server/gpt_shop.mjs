// Native Node server using the same task/receipt contract as the Workers shop.
import http from 'node:http';
import {webcrypto} from 'node:crypto';
import {readFile, writeFile, rename, mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import handler, {ShopCore} from '../workers/shop_core.js';
globalThis.crypto ||= webcrypto;
const root = fileURLToPath(new URL('..',import.meta.url));
const directory = process.env.SHOP_STATE_DIR || resolve(root,'.market/gpt-tasks');
const port = Number(process.env.SHOP_PORT || 8900);
if (!process.env.OPENAI_API_KEY) throw new Error('OPENAI_API_KEY is not configured');
const env = {...process.env, SHOP_PRIVATE_KEY:await readFile(resolve(root,'shop_key.pem'),'utf8')};
await mkdir(directory,{recursive:true,mode:0o700});
const file = key => {
  if (!/^task:[a-f0-9-]{36}$/.test(key)) throw new Error('Invalid task id');
  return resolve(directory,key.slice(5)+'.json');
};
let queue = Promise.resolve();
const ctx = {
  storage:{
    async get(key) {try {return JSON.parse(await readFile(file(key),'utf8'));} catch(e) {if(e.code==='ENOENT') return undefined; throw e;}},
    async put(key,value) {const path=file(key);await writeFile(path+'.tmp',JSON.stringify(value),{mode:0o600});await rename(path+'.tmp',path);},
  },
  blockConcurrencyWhile(fn) {const next=queue.then(fn);queue=next.catch(()=>{});return next;},
};
const shop = new ShopCore(ctx,env);
env.SHOP = {idFromName:()=>null,get:()=>shop};
http.createServer(async(req,res)=>{
  try {
    const chunks=[];let size=0;
    for await(const chunk of req) {size+=chunk.length;if(size>16384){res.writeHead(413);res.end();return;}chunks.push(chunk);}
    const origin=process.env.SHOP_PUBLIC_URL || `http://127.0.0.1:${port}`;
    const request=new Request(new URL(req.url,origin),{method:req.method,
      headers:{'Content-Type':'application/json'},...(['GET','HEAD'].includes(req.method)?{}:{body:Buffer.concat(chunks)})});
    const response=await handler.fetch(request,env);
    res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));
  } catch(e) {console.error('[server-error]',e.name);res.writeHead(500);res.end('Internal error');}
}).listen(port,'127.0.0.1',()=>console.log(`GPT shop listening on 127.0.0.1:${port}`));
