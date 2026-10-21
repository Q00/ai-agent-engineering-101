// Keys stay in Workers secrets; never log request headers or API response bodies.
export function openAI(env, fetcher = fetch) {
  if (!env.OPENAI_API_KEY) return null;
  return {
    async run({messages, schema}) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 4800);
      try {
        const response = await fetcher('https://api.openai.com/v1/responses', {
          method:'POST', signal:controller.signal,
          headers:{'Content-Type':'application/json',Authorization:`Bearer ${env.OPENAI_API_KEY}`},
          body:JSON.stringify({
            model:env.OPENAI_MODEL || 'gpt-5.4-mini',
            input:messages, store:false, max_output_tokens:256,
            reasoning:{effort:'none'},
            text:{format:{type:'json_schema',name:'purchase_intent',strict:true,schema}},
          }),
        });
        if (!response.ok) throw new Error(`OpenAI HTTP ${response.status}`);
        const body = await response.json();
        if (body.status !== 'completed') throw new Error('OpenAI incomplete response');
        const content = (body.output || []).filter(x => x.type === 'message').flatMap(x => x.content || []);
        if (content.some(x => x.type === 'refusal')) throw new Error('OpenAI refusal');
        const text = content.filter(x => x.type === 'output_text').map(x => x.text).join('');
        if (!text) throw new Error('OpenAI empty response');
        console.log('[openai]', JSON.stringify({model:body.model,input_tokens:body.usage?.input_tokens,output_tokens:body.usage?.output_tokens}));
        return {response:JSON.parse(text)};
      } finally { clearTimeout(timer); }
    },
  };
}
