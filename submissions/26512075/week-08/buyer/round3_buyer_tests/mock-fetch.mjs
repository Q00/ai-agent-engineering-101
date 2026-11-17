globalThis.fetch = async (url, opts = {}) => {
  if (!opts.method) return {ok:true, json:async()=>({supportedInterfaces:[{protocolBinding:'JSONRPC', protocolVersion:'1.0', url:'https://mock.invalid/a2a'}]})};
  const req = JSON.parse(opts.body);
  const text = req.params.message.parts[0].text;
  if (!text.includes(process.env.EXPECT_SKU)) throw new Error('unexpected requested SKU: '+text);
  return {ok:true, json:async()=>({result:{id:'t1', status:{state:'TASK_STATE_COMPLETED',message:{parts:[{text:`Deal at ${process.env.AGREED_PRICE || '70'}.`}]}}, artifacts:[{name:'receipt',parts:[{data:{item:process.env.RECEIPT_SKU,price:70}}]}]}})};
};









