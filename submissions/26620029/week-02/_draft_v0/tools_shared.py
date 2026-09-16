# 두 하네스가 공유하는 도구, 모델, 측정 유틸. 같은 도구를 써야 공정한 비교
import os, anthropic, openai, re, json

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
if OPENROUTER_KEY:                      # 키 하나로 벤더 자동 판별
    client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_KEY)
    MODEL = "anthropic/claude-sonnet-4.5"
else:
    client = anthropic.Anthropic()
    MODEL = "claude-sonnet-4-5"

def read_file(path):                    # 도구 1
    with open(path, encoding="utf-8") as f:
        return f.read()[:4000]           # 컨텍스트 보호용 상한

def count_pattern(text, pattern):        # 도구 2
    return len(re.findall(pattern, text))

TOOLS = {"read_file": read_file, "count_pattern": count_pattern}

class Meter:                             # 네 지표를 한곳에서 센다
    def __init__(self):
        self.tokens = 0; self.iters = 0; self.interventions = 0
    def add(self, n_in, n_out):
        self.tokens += n_in + n_out
        self.iters += 1

def call_model(messages, meter):
    if OPENROUTER_KEY:                 # OpenAI 호환 포맷: choices, prompt/completion_tokens
        resp = client.chat.completions.create(model=MODEL, max_tokens=1024, messages=messages)
        meter.add(resp.usage.prompt_tokens, resp.usage.completion_tokens)
        return resp.choices[0].message.content
    resp = client.messages.create(model=MODEL, max_tokens=1024, messages=messages)
    meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
    return resp.content[0].text