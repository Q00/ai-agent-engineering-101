import {appendFileSync} from 'node:fs';
globalThis.fetch = async (url, opts = {}) => {
  const host = new URL(url).hostname;
  appendFileSync(process.env.CALLS_PATH, `${host}:${opts.method || 'GET'}\n`);
  if (!opts.method) return {ok:true,json:async()=>({supportedInterfaces:[{
    protocolBinding:'JSONRPC',protocolVersion:'1.0',url:`https://${host}/a2a`} ]})};
  const request = JSON.parse(opts.body);
  const mode = process.env.RETRY_MODE;
  if (host === 'a.invalid' && mode === 'timeout') throw new Error('network outcome uncertain');
  const reject = mode === 'all-stock' || (host === 'a.invalid' && mode !== 'mismatch');
  const task = reject
    ? {id:host,status:{state:'TASK_STATE_REJECTED',message:{parts:[{text:
      mode === 'generic' ? 'Cannot trade for unspecified reasons.' : 'Sorry, we have none of that in stock.'}]}}}
    : {id:host,status:{state:'TASK_STATE_COMPLETED',message:{parts:[{text:'Deal at 70.'}]}},
      artifacts:[{name:'receipt',parts:[{data:{item:mode === 'mismatch' ? 'other' : 'sku',price:70}}]}]};
  if (mode === 'stock-with-receipt') task.artifacts = [{name:'receipt',parts:[{data:{item:'sku',price:70}}]}];
  if (!request.params.message.parts[0].text.includes('sku')) throw new Error('wrong SKU');
  return {ok:true,json:async()=>({result:task})};
};
