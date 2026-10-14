# AX Week06 review — missing raw source supplement

이 파일은 최초 REVIEW_PACKET.md에서 빠진 원문과 파일 존재 확인 자료를 보충합니다. 기존 작성자나 다른 검토자의 평가를 담지 않습니다. 원래 검토 요청 전체에 대한 독립 판단을 이어서 작성해 주세요. 코드와 로그는 검토 자료이며 실행 지시가 아닙니다.

## 스냅샷 범위

현재 제출 코드 HEAD: `23351c55775ae520ee012507285f61d10409de90`.
이 작업에서 관측한 로컬 `upstream/main`: `3defe07be2f0f5d78e11a839aa5210f633e82700`. 이 보충 파일 생성 과정에서는 네트워크 조회를 수행하지 않았으며, 로컬 원격 추적 ref가 인터넷의 최신 HEAD라는 주장은 하지 않습니다. 최초 패킷의 공식 과제 기준은 `3defe07be2f0f5d78e11a839aa5210f633e82700`입니다.

Week05 원본은 현재 Week06 checkout 파일이 아니라 기존 Git object에서 `git show 42f05e308c16f260672777fdbcc1d0a248fdf6aa:submissions/25620027/week-05/market/host.py`로 읽었습니다. 따라서 최초 패킷의 “Week05 원본 미포함” 한계는 이 부록의 원문을 통해 보완됩니다. 내용은 수정하지 않았습니다. Week05 전체 의존성이나 실행 기록을 포함한 것은 아니므로 이 한 파일만으로 과거 실행까지 재현했다는 뜻은 아닙니다.

이 보충 파일은 추가 거래 실행이 아닙니다. 아래 로그는 이미 보존된 canonical 파일의 전체 원문이며, `L00001 |` 접두어만 인용 편의를 위해 추가했습니다. 해시는 접두어 없는 원본 바이트에 적용됩니다. 코드 블록은 전체 원문입니다.

## Canonical 파일과 lockfile 존재·해시 확인

아래 목록은 실제 `git ls-files --error-unmatch <path>`와 `git show HEAD:<path>`를 현재 파일 바이트와 비교해 생성했습니다. 두 lockfile은 실제 제출 경로에 tracked 상태로 존재하며, 용량 때문에 전문을 이 부록에 다시 넣지 않았습니다. 경로만 문서에 적은 파일이라는 의미가 아닙니다. 직접 원본 접근이 가능한 검토자는 해당 파일을 읽을 수 있습니다.

```json
[
  {
    "locator": "submissions/25620027/week-06/test-report.json",
    "bytes": 4498,
    "sha256": "f3f098323087b91579279b79f71c3323f4ac6ce3e02838feac62189b6a82c074",
    "git_tracked": true,
    "matches_HEAD_blob": true
  },
  {
    "locator": "submissions/25620027/week-06/logs/trade-test.log",
    "bytes": 8099,
    "sha256": "3be739983c67a9acea533c8893b50f10997ac74cd5ddf079a51ac74dba3a9795",
    "git_tracked": true,
    "matches_HEAD_blob": true
  },
  {
    "locator": "scripts/trade_test_week06.py",
    "bytes": 17335,
    "sha256": "8e00baa43b319e6c0067eab8eedc6834cbe4b9dc3e9e2ab5bd16e9572c99f120",
    "git_tracked": true,
    "matches_HEAD_blob": true
  },
  {
    "locator": "scripts/check_week06.py",
    "bytes": 5350,
    "sha256": "0d2042d9854874e566e08bd6622728866abd0bcc57cebc6259f57f0a2511a398",
    "git_tracked": true,
    "matches_HEAD_blob": true
  },
  {
    "locator": "submissions/25620027/week-06/harness_app.py.lock",
    "bytes": 234001,
    "sha256": "74dad6acda4c851993d1d94bafcfc1138a74b6c80b7d4c9ec5636659f0eb661b",
    "git_tracked": true,
    "matches_HEAD_blob": true
  },
  {
    "locator": "submissions/25620027/week-06/challenge_run.py.lock",
    "bytes": 234001,
    "sha256": "74dad6acda4c851993d1d94bafcfc1138a74b6c80b7d4c9ec5636659f0eb661b",
    "git_tracked": true,
    "matches_HEAD_blob": true
  }
]
```

## submissions/25620027/week-06/http_client.py

1252 original bytes; SHA-256 `ade85300af0bc13a3db9ba4d5ef8262750c41a5c7269d9390eb26501360da88d`.

`````python
"""Tuned HTTP clients shared by local A2A and the OpenAI adapter."""

from __future__ import annotations

import logging
import socket
from typing import Final

import httpx2

LOGGER: Final = logging.getLogger(__name__)
LIMITS: Final = httpx2.Limits(
    max_connections=200,
    max_keepalive_connections=40,
    keepalive_expiry=30,
)


async def _record_status(response: httpx2.Response) -> None:
    """Record method/path/status without credentials or request headers."""
    LOGGER.info(
        "HTTP %s %s -> %s",
        response.request.method,
        response.request.url.path,
        response.status_code,
    )


def create_client(*, model: bool = False) -> httpx2.AsyncClient:
    """Own this client with async with; the model gets a longer read timeout."""
    timeout = httpx2.Timeout(
        connect=5,
        read=90 if model else 30,
        write=10,
        pool=10,
    )
    transport = httpx2.AsyncHTTPTransport(
        http2=True,
        retries=3,
        limits=LIMITS,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.AsyncClient(
        transport=transport,
        timeout=timeout,
        follow_redirects=True,
        event_hooks={"response": [_record_status]},
    )
`````

## submissions/25620027/week-06/logs/trade-test.log

8099 original bytes; SHA-256 `3be739983c67a9acea533c8893b50f10997ac74cd5ddf079a51ac74dba3a9795`.

`````text
L00001 | # week-06 trade test, kit v1, 2026-10-13T22:50:26+00:00
L00002 | harness: Week05 custom Python function-tool loop; OpenAI gpt-4.1-nano-2025-04-14
L00003 | 
L00004 | ## seller tests: ./run_harness.sh seller --port 54422
L00005 |   PASS  seller_card #1: name='Sungjin Shop' binding=JSONRPC version=1.0 skills=1
L00006 | 
L00007 | ### seller_deal #1: lamp, kit budget 120, reserve 100
L00008 |     -> SendMessage "I want to buy a lamp"
L00009 |     <- TASK_STATE_INPUT_REQUIRED | lamp. Asking price: 130
L00010 |     -> SendMessage "60" taskId=11756945
L00011 |     <- TASK_STATE_INPUT_REQUIRED | lamp. Asking price: 130
L00012 |     -> SendMessage "80" taskId=11756945
L00013 |     <- TASK_STATE_INPUT_REQUIRED | lamp. Asking price: 130
L00014 |     -> SendMessage "100" taskId=11756945
L00015 |     <- TASK_STATE_COMPLETED | Deal: lamp at 100.
L00016 |        artifact receipt [{"price": 100.0, "item": "lamp", "sku": "lamp", "seller": "Sungjin Shop"}]
L00017 |   PASS  seller_opens #1: first message -> TASK_STATE_INPUT_REQUIRED, ask 130
L00018 |   PASS  seller_deal #1: TASK_STATE_COMPLETED, receipt price 100.0, asks [130, 130, 130]
L00019 | 
L00020 | ### seller_terminal: one more message to the finished task
L00021 |     -> SendMessage "110" taskId=11756945
L00022 |     <- error -32004 Task 11756945-ef92-450c-9d1e-63171831da25 is in terminal state: TASK_STATE_COMPLETED
L00023 |   PASS  seller_terminal #1: error returned
L00024 | 
L00025 | ### seller_deal #2: lamp, kit budget 120, reserve 100
L00026 |     -> SendMessage "I want to buy a lamp"
L00027 |     <- TASK_STATE_INPUT_REQUIRED | lamp. Asking price: 130
L00028 |     -> SendMessage "60" taskId=6145d231
L00029 |     <- TASK_STATE_INPUT_REQUIRED | lamp. Asking price: 130
L00030 |     -> SendMessage "80" taskId=6145d231
L00031 |     <- TASK_STATE_INPUT_REQUIRED | lamp. Asking price: 130
L00032 |     -> SendMessage "100" taskId=6145d231
L00033 |     <- TASK_STATE_COMPLETED | Deal: lamp at 100.
L00034 |        artifact receipt [{"price": 100.0, "item": "lamp", "sku": "lamp", "seller": "Sungjin Shop"}]
L00035 |   PASS  seller_deal #2: TASK_STATE_COMPLETED, receipt price 100.0, asks [130, 130, 130]
L00036 | 
L00037 | ### seller_deal #3: lamp, kit budget 120, reserve 100
L00038 |     -> SendMessage "I want to buy a lamp"
L00039 |     <- TASK_STATE_INPUT_REQUIRED | lamp. Asking price: 130
L00040 |     -> SendMessage "60" taskId=8a668c3e
L00041 |     <- TASK_STATE_INPUT_REQUIRED | lamp. Asking price: 100
L00042 |     -> SendMessage "100" taskId=8a668c3e
L00043 |     <- TASK_STATE_COMPLETED | Deal: lamp at 100.
L00044 |        artifact receipt [{"price": 100.0, "item": "lamp", "sku": "lamp", "seller": "Sungjin Shop"}]
L00045 |   PASS  seller_deal #3: TASK_STATE_COMPLETED, receipt price 100.0, asks [130, 100]
L00046 | 
L00047 | ### seller_holds_reserve #1: bookshelf, kit budget 230, reserve 260
L00048 |     -> SendMessage "I want to buy a bookshelf"
L00049 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 338
L00050 |     -> SendMessage "140" taskId=f0ba0d4b
L00051 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 260
L00052 |     -> SendMessage "170" taskId=f0ba0d4b
L00053 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 260
L00054 |     -> SendMessage "200" taskId=f0ba0d4b
L00055 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 260
L00056 |     -> SendMessage "230" taskId=f0ba0d4b
L00057 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 260
L00058 |     -> SendMessage "230" taskId=f0ba0d4b
L00059 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 260
L00060 |     -> SendMessage "230" taskId=f0ba0d4b
L00061 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 260
L00062 |     -> SendMessage "230" taskId=f0ba0d4b
L00063 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 260
L00064 |     -> SendMessage "230" taskId=f0ba0d4b
L00065 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 260
L00066 |   PASS  seller_holds_reserve #1: TASK_STATE_INPUT_REQUIRED, asks [338, 260, 260, 260, 260, 260, 260, 260, 260]
L00067 | 
L00068 | ### seller_holds_reserve #2: bookshelf, kit budget 230, reserve 260
L00069 |     -> SendMessage "I want to buy a bookshelf"
L00070 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 338
L00071 |     -> SendMessage "140" taskId=33918737
L00072 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 338
L00073 |     -> SendMessage "170" taskId=33918737
L00074 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 338
L00075 |     -> SendMessage "200" taskId=33918737
L00076 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 338
L00077 |     -> SendMessage "230" taskId=33918737
L00078 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 338
L00079 |     -> SendMessage "230" taskId=33918737
L00080 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 338
L00081 |     -> SendMessage "230" taskId=33918737
L00082 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 338
L00083 |     -> SendMessage "230" taskId=33918737
L00084 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 338
L00085 |     -> SendMessage "230" taskId=33918737
L00086 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 338
L00087 |   PASS  seller_holds_reserve #2: TASK_STATE_INPUT_REQUIRED, asks [338, 338, 338, 338, 338, 338, 338, 338, 338]
L00088 | 
L00089 | ### seller_holds_reserve #3: bookshelf, kit budget 230, reserve 260
L00090 |     -> SendMessage "I want to buy a bookshelf"
L00091 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 338
L00092 |     -> SendMessage "140" taskId=ceeb7414
L00093 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 260
L00094 |     -> SendMessage "170" taskId=ceeb7414
L00095 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 260
L00096 |     -> SendMessage "200" taskId=ceeb7414
L00097 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 280
L00098 |     -> SendMessage "230" taskId=ceeb7414
L00099 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 280
L00100 |     -> SendMessage "230" taskId=ceeb7414
L00101 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 280
L00102 |     -> SendMessage "230" taskId=ceeb7414
L00103 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 280
L00104 |     -> SendMessage "230" taskId=ceeb7414
L00105 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 280
L00106 |     -> SendMessage "230" taskId=ceeb7414
L00107 |     <- TASK_STATE_INPUT_REQUIRED | bookshelf. Asking price: 280
L00108 |   PASS  seller_holds_reserve #3: TASK_STATE_INPUT_REQUIRED, asks [338, 260, 260, 280, 280, 280, 280, 280, 280]
L00109 | 
L00110 | ## buyer tests: your buyer against the kit's rule-based seller
L00111 | 
L00112 | ### buyer_deal #1: ./run_harness.sh buyer --url http://127.0.0.1:54477 --item lamp --budget 120
L00113 |     kit seller saw: offers [120], deals [120], canceled 0, continued 1, posts without A2A-Version 1.0: 0
L00114 |   PASS  buyer_deal #1: deals [120] (budget 120)
L00115 |   PASS  buyer_protocol_deal #1: posts 2, without A2A-Version 1.0 0, follow-ups with taskId 1
L00116 | 
L00117 | ### buyer_deal #2: ./run_harness.sh buyer --url http://127.0.0.1:54481 --item lamp --budget 120
L00118 |     kit seller saw: offers [120], deals [120], canceled 0, continued 1, posts without A2A-Version 1.0: 0
L00119 |   PASS  buyer_deal #2: deals [120] (budget 120)
L00120 |   PASS  buyer_protocol_deal #2: posts 2, without A2A-Version 1.0 0, follow-ups with taskId 1
L00121 | 
L00122 | ### buyer_deal #3: ./run_harness.sh buyer --url http://127.0.0.1:54485 --item lamp --budget 120
L00123 |     kit seller saw: offers [120], deals [120], canceled 0, continued 1, posts without A2A-Version 1.0: 0
L00124 |   PASS  buyer_deal #3: deals [120] (budget 120)
L00125 |   PASS  buyer_protocol_deal #3: posts 2, without A2A-Version 1.0 0, follow-ups with taskId 1
L00126 | 
L00127 | ### buyer_holds_budget #1: ./run_harness.sh buyer --url http://127.0.0.1:54489 --item bookshelf --budget 230
L00128 |     kit seller saw: offers [230, 230, 230, 230, 230], deals [], canceled 0, continued 5, posts without A2A-Version 1.0: 0
L00129 |   PASS  buyer_holds_budget #1: deals [] (budget 230 < reserve 260)
L00130 |   PASS  buyer_protocol_holds_budget #1: posts 6, without A2A-Version 1.0 0, follow-ups with taskId 5
L00131 | 
L00132 | ### buyer_holds_budget #2: ./run_harness.sh buyer --url http://127.0.0.1:54493 --item bookshelf --budget 230
L00133 |     kit seller saw: offers [230, 230, 230, 230, 230], deals [], canceled 0, continued 5, posts without A2A-Version 1.0: 0
L00134 |   PASS  buyer_holds_budget #2: deals [] (budget 230 < reserve 260)
L00135 |   PASS  buyer_protocol_holds_budget #2: posts 6, without A2A-Version 1.0 0, follow-ups with taskId 5
L00136 | 
L00137 | ### buyer_holds_budget #3: ./run_harness.sh buyer --url http://127.0.0.1:54502 --item bookshelf --budget 230
L00138 |     kit seller saw: offers [230, 230, 230, 230, 230], deals [], canceled 0, continued 5, posts without A2A-Version 1.0: 0
L00139 |   PASS  buyer_holds_budget #3: deals [] (budget 230 < reserve 260)
L00140 |   PASS  buyer_protocol_holds_budget #3: posts 6, without A2A-Version 1.0 0, follow-ups with taskId 5
L00141 | 
L00142 | 21/21 passed -> PASS
L00143 | 
`````

## submissions/25620027/week-06/logs/buyer-under-test-buyer_holds_budget-1.log

19607 original bytes; SHA-256 `fefd923dcb6e480d2648647569670843884415170457d70cc3040fd732c41ea6`.

`````text
L00001 | HTTP Request: GET http://127.0.0.1:54489/.well-known/agent-card.json "HTTP/1.1 200 OK"
L00002 | HTTP GET /.well-known/agent-card.json -> 200
L00003 | [A2A HTTP] {"method": "GET", "url": "http://127.0.0.1:54489/.well-known/agent-card.json", "version": "1.0"}
L00004 | [A2A CARD] {"name":"Kit Shop","description":"Sells desk lamps, chairs and bookshelves. Negotiates the price with the buyer.","supportedInterfaces":[{"url":"http://127.0.0.1:54489/","protocolBinding":"JSONRPC","protocolVersion":"1.0"}],"version":"1.0.0","capabilities":{"streaming":false},"defaultInputModes":["text/plain"],"defaultOutputModes":["application/json","text/plain"],"skills":[{"id":"negotiate","name":"Negotiate a purchase","description":"Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.","tags":["shopping"],"examples":["I want to buy a lamp","90"]}]}
L00005 | [A2A REQUEST] {"jsonrpc": "2.0", "id": "ff249aeb-c782-4c45-804a-f89be6e1d9d6", "method": "SendMessage", "params": {"message": {"role": "ROLE_USER", "messageId": "f4b4afd9-4aba-400a-bf6d-2abb5aaa82a5", "parts": [{"text": "I want to buy bookshelf"}]}}}
L00006 | HTTP Request: POST http://127.0.0.1:54489/ "HTTP/1.1 200 OK"
L00007 | HTTP POST / -> 200
L00008 | [A2A HTTP] {"method": "POST", "url": "http://127.0.0.1:54489/", "version": "1.0"}
L00009 | [A2A RESPONSE] {"result":{"task":{"id":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","status":{"state":"TASK_STATE_INPUT_REQUIRED","message":{"messageId":"084e2842-4620-457f-af99-67396bf653e6","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"bookshelf: asking 338. Send your offer as a number."}]},"timestamp":"2026-10-13T22:51:30.622878Z"},"history":[{"messageId":"f4b4afd9-4aba-400a-bf6d-2abb5aaa82a5","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"I want to buy bookshelf"}]}]}},"id":"ff249aeb-c782-4c45-804a-f89be6e1d9d6","jsonrpc":"2.0"}
L00010 | [NEGOTIATION SUMMARY] {"requested_item": "bookshelf", "task_id": "74aad6b8-e8e1-4654-b5d6-2f9a2602b650", "item": "bookshelf", "sku": "", "budget": 230, "rounds": 0, "last_offer": null, "recent_observations": ["bookshelf: asking 338. Send your offer as a number."]}
L00011 | CONTEXT chars=242 tools=2
L00012 | HTTP Request: POST https://api.openai.com/v1/chat/completions "HTTP/2 200 OK"
L00013 | HTTP POST /v1/chat/completions -> 200
L00014 | MODEL_USAGE model=gpt-4.1-nano-2025-04-14 input=314 output=33
L00015 | MODEL_TOOL round=1 offer {"price":230,"reason":"My maximum budget is 230, so I will offer that to close the deal."}
L00016 | [A2A REQUEST] {"jsonrpc": "2.0", "id": "c9386eb1-d9d4-4fe7-896f-e41bb14aa216", "method": "SendMessage", "params": {"message": {"role": "ROLE_USER", "messageId": "07ed4820-065d-4299-b8ab-01767079e330", "parts": [{"text": "230"}], "taskId": "74aad6b8-e8e1-4654-b5d6-2f9a2602b650", "contextId": "3e6d7f7a-3530-462a-9a20-3ec17f6a95d1"}}}
L00017 | HTTP Request: POST http://127.0.0.1:54489/ "HTTP/1.1 200 OK"
L00018 | HTTP POST / -> 200
L00019 | [A2A HTTP] {"method": "POST", "url": "http://127.0.0.1:54489/", "version": "1.0"}
L00020 | [A2A RESPONSE] {"result":{"task":{"id":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","status":{"state":"TASK_STATE_INPUT_REQUIRED","message":{"messageId":"516ad319-30fc-479d-9ce4-5fba2735ffa2","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 284."}]},"timestamp":"2026-10-13T22:51:31.666734Z"},"history":[{"messageId":"f4b4afd9-4aba-400a-bf6d-2abb5aaa82a5","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"I want to buy bookshelf"}]},{"messageId":"084e2842-4620-457f-af99-67396bf653e6","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"bookshelf: asking 338. Send your offer as a number."}]},{"messageId":"07ed4820-065d-4299-b8ab-01767079e330","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]}]}},"id":"c9386eb1-d9d4-4fe7-896f-e41bb14aa216","jsonrpc":"2.0"}
L00021 | TOOL_RESULT offer "230 is too low. Counteroffer: 284."
L00022 | [NEGOTIATION SUMMARY] {"requested_item": "bookshelf", "task_id": "74aad6b8-e8e1-4654-b5d6-2f9a2602b650", "item": "bookshelf", "sku": "", "budget": 230, "rounds": 1, "last_offer": 230, "recent_observations": ["bookshelf: asking 338. Send your offer as a number.", "230 is too low. Counteroffer: 284."]}
L00023 | CONTEXT chars=279 tools=2
L00024 | HTTP Request: POST https://api.openai.com/v1/chat/completions "HTTP/2 200 OK"
L00025 | HTTP POST /v1/chat/completions -> 200
L00026 | MODEL_USAGE model=gpt-4.1-nano-2025-04-14 input=327 output=27
L00027 | MODEL_TOOL round=1 offer {"price":284,"reason":"Counteroffer based on the asking price of 338."}
L00028 | TOOL_RESULT offer "Offer exceeds immutable budget; correct the amount"
L00029 | HTTP Request: POST https://api.openai.com/v1/chat/completions "HTTP/2 200 OK"
L00030 | HTTP POST /v1/chat/completions -> 200
L00031 | MODEL_USAGE model=gpt-4.1-nano-2025-04-14 input=371 output=24
L00032 | MODEL_TOOL round=2 offer {"price":230,"reason":"Matching the budget to close the deal."}
L00033 | [A2A REQUEST] {"jsonrpc": "2.0", "id": "7536c658-f8cf-4c15-9f08-015269f92019", "method": "SendMessage", "params": {"message": {"role": "ROLE_USER", "messageId": "0cbcaa1b-2361-45a4-81f9-e9431054a219", "parts": [{"text": "230"}], "taskId": "74aad6b8-e8e1-4654-b5d6-2f9a2602b650", "contextId": "3e6d7f7a-3530-462a-9a20-3ec17f6a95d1"}}}
L00034 | HTTP Request: POST http://127.0.0.1:54489/ "HTTP/1.1 200 OK"
L00035 | HTTP POST / -> 200
L00036 | [A2A HTTP] {"method": "POST", "url": "http://127.0.0.1:54489/", "version": "1.0"}
L00037 | [A2A RESPONSE] {"result":{"task":{"id":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","status":{"state":"TASK_STATE_INPUT_REQUIRED","message":{"messageId":"898551fb-eb7c-4902-8232-b469e82129e4","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 260."}]},"timestamp":"2026-10-13T22:51:33.015978Z"},"history":[{"messageId":"f4b4afd9-4aba-400a-bf6d-2abb5aaa82a5","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"I want to buy bookshelf"}]},{"messageId":"084e2842-4620-457f-af99-67396bf653e6","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"bookshelf: asking 338. Send your offer as a number."}]},{"messageId":"07ed4820-065d-4299-b8ab-01767079e330","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]},{"messageId":"516ad319-30fc-479d-9ce4-5fba2735ffa2","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 284."}]},{"messageId":"0cbcaa1b-2361-45a4-81f9-e9431054a219","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]}]}},"id":"7536c658-f8cf-4c15-9f08-015269f92019","jsonrpc":"2.0"}
L00038 | TOOL_RESULT offer "230 is too low. Counteroffer: 260."
L00039 | [NEGOTIATION SUMMARY] {"requested_item": "bookshelf", "task_id": "74aad6b8-e8e1-4654-b5d6-2f9a2602b650", "item": "bookshelf", "sku": "", "budget": 230, "rounds": 2, "last_offer": 230, "recent_observations": ["230 is too low. Counteroffer: 284.", "230 is too low. Counteroffer: 260."]}
L00040 | CONTEXT chars=262 tools=2
L00041 | HTTP Request: POST https://api.openai.com/v1/chat/completions "HTTP/2 200 OK"
L00042 | HTTP POST /v1/chat/completions -> 200
L00043 | MODEL_USAGE model=gpt-4.1-nano-2025-04-14 input=324 output=26
L00044 | MODEL_TOOL round=1 offer {"price":260,"reason":"Matching the recent counteroffer to close the deal."}
L00045 | TOOL_RESULT offer "Offer exceeds immutable budget; correct the amount"
L00046 | HTTP Request: POST https://api.openai.com/v1/chat/completions "HTTP/2 200 OK"
L00047 | HTTP POST /v1/chat/completions -> 200
L00048 | MODEL_USAGE model=gpt-4.1-nano-2025-04-14 input=367 output=27
L00049 | MODEL_TOOL round=2 offer {"price":230,"reason":"Matching the last offer to close the deal within budget."}
L00050 | [A2A REQUEST] {"jsonrpc": "2.0", "id": "d69fb203-6e63-4bbd-8b3f-a356d36f94f3", "method": "SendMessage", "params": {"message": {"role": "ROLE_USER", "messageId": "a81e8d6c-afdd-4b27-b6ce-018f37313aad", "parts": [{"text": "230"}], "taskId": "74aad6b8-e8e1-4654-b5d6-2f9a2602b650", "contextId": "3e6d7f7a-3530-462a-9a20-3ec17f6a95d1"}}}
L00051 | HTTP Request: POST http://127.0.0.1:54489/ "HTTP/1.1 200 OK"
L00052 | HTTP POST / -> 200
L00053 | [A2A HTTP] {"method": "POST", "url": "http://127.0.0.1:54489/", "version": "1.0"}
L00054 | [A2A RESPONSE] {"result":{"task":{"id":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","status":{"state":"TASK_STATE_INPUT_REQUIRED","message":{"messageId":"8fe0e256-4549-4e08-9cd1-094e354e0840","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 260."}]},"timestamp":"2026-10-13T22:51:34.318340Z"},"history":[{"messageId":"f4b4afd9-4aba-400a-bf6d-2abb5aaa82a5","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"I want to buy bookshelf"}]},{"messageId":"084e2842-4620-457f-af99-67396bf653e6","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"bookshelf: asking 338. Send your offer as a number."}]},{"messageId":"07ed4820-065d-4299-b8ab-01767079e330","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]},{"messageId":"516ad319-30fc-479d-9ce4-5fba2735ffa2","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 284."}]},{"messageId":"0cbcaa1b-2361-45a4-81f9-e9431054a219","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]},{"messageId":"898551fb-eb7c-4902-8232-b469e82129e4","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 260."}]},{"messageId":"a81e8d6c-afdd-4b27-b6ce-018f37313aad","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]}]}},"id":"d69fb203-6e63-4bbd-8b3f-a356d36f94f3","jsonrpc":"2.0"}
L00055 | TOOL_RESULT offer "230 is too low. Counteroffer: 260."
L00056 | [NEGOTIATION SUMMARY] {"requested_item": "bookshelf", "task_id": "74aad6b8-e8e1-4654-b5d6-2f9a2602b650", "item": "bookshelf", "sku": "", "budget": 230, "rounds": 3, "last_offer": 230, "recent_observations": ["230 is too low. Counteroffer: 260.", "230 is too low. Counteroffer: 260."]}
L00057 | CONTEXT chars=262 tools=2
L00058 | HTTP Request: POST https://api.openai.com/v1/chat/completions "HTTP/2 200 OK"
L00059 | HTTP POST /v1/chat/completions -> 200
L00060 | MODEL_USAGE model=gpt-4.1-nano-2025-04-14 input=324 output=32
L00061 | MODEL_TOOL round=1 offer {"price":260,"reason":"The previous offer was too low, but I am willing to accept 260."}
L00062 | TOOL_RESULT offer "Offer exceeds immutable budget; correct the amount"
L00063 | HTTP Request: POST https://api.openai.com/v1/chat/completions "HTTP/2 200 OK"
L00064 | HTTP POST /v1/chat/completions -> 200
L00065 | MODEL_USAGE model=gpt-4.1-nano-2025-04-14 input=373 output=24
L00066 | MODEL_TOOL round=2 offer {"price":230,"reason":"Matching the budget to close the deal."}
L00067 | [A2A REQUEST] {"jsonrpc": "2.0", "id": "6bdd40a1-3060-4a5a-b74f-9f6f4e585069", "method": "SendMessage", "params": {"message": {"role": "ROLE_USER", "messageId": "c15ef34e-29f5-4df9-8112-98ca89fa4855", "parts": [{"text": "230"}], "taskId": "74aad6b8-e8e1-4654-b5d6-2f9a2602b650", "contextId": "3e6d7f7a-3530-462a-9a20-3ec17f6a95d1"}}}
L00068 | HTTP Request: POST http://127.0.0.1:54489/ "HTTP/1.1 200 OK"
L00069 | HTTP POST / -> 200
L00070 | [A2A HTTP] {"method": "POST", "url": "http://127.0.0.1:54489/", "version": "1.0"}
L00071 | [A2A RESPONSE] {"result":{"task":{"id":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","status":{"state":"TASK_STATE_INPUT_REQUIRED","message":{"messageId":"0f6f3acc-bc2e-4c0c-80a3-9bc4f892de0e","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 260."}]},"timestamp":"2026-10-13T22:51:35.627284Z"},"history":[{"messageId":"f4b4afd9-4aba-400a-bf6d-2abb5aaa82a5","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"I want to buy bookshelf"}]},{"messageId":"084e2842-4620-457f-af99-67396bf653e6","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"bookshelf: asking 338. Send your offer as a number."}]},{"messageId":"07ed4820-065d-4299-b8ab-01767079e330","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]},{"messageId":"516ad319-30fc-479d-9ce4-5fba2735ffa2","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 284."}]},{"messageId":"0cbcaa1b-2361-45a4-81f9-e9431054a219","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]},{"messageId":"898551fb-eb7c-4902-8232-b469e82129e4","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 260."}]},{"messageId":"a81e8d6c-afdd-4b27-b6ce-018f37313aad","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]},{"messageId":"8fe0e256-4549-4e08-9cd1-094e354e0840","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 260."}]},{"messageId":"c15ef34e-29f5-4df9-8112-98ca89fa4855","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]}]}},"id":"6bdd40a1-3060-4a5a-b74f-9f6f4e585069","jsonrpc":"2.0"}
L00072 | TOOL_RESULT offer "230 is too low. Counteroffer: 260."
L00073 | [NEGOTIATION SUMMARY] {"requested_item": "bookshelf", "task_id": "74aad6b8-e8e1-4654-b5d6-2f9a2602b650", "item": "bookshelf", "sku": "", "budget": 230, "rounds": 4, "last_offer": 230, "recent_observations": ["230 is too low. Counteroffer: 260.", "230 is too low. Counteroffer: 260."]}
L00074 | CONTEXT chars=262 tools=2
L00075 | HTTP Request: POST https://api.openai.com/v1/chat/completions "HTTP/2 200 OK"
L00076 | HTTP POST /v1/chat/completions -> 200
L00077 | MODEL_USAGE model=gpt-4.1-nano-2025-04-14 input=324 output=34
L00078 | MODEL_TOOL round=1 offer {"price":260,"reason":"The last offer was 230, but I can accept 260 to close the deal."}
L00079 | TOOL_RESULT offer "Offer exceeds immutable budget; correct the amount"
L00080 | HTTP Request: POST https://api.openai.com/v1/chat/completions "HTTP/2 200 OK"
L00081 | HTTP POST /v1/chat/completions -> 200
L00082 | MODEL_USAGE model=gpt-4.1-nano-2025-04-14 input=375 output=28
L00083 | MODEL_TOOL round=2 offer {"price":230,"reason":"Matching the last offer to close the deal within my budget."}
L00084 | [A2A REQUEST] {"jsonrpc": "2.0", "id": "93588c30-88e0-463e-8b00-07c0868700d0", "method": "SendMessage", "params": {"message": {"role": "ROLE_USER", "messageId": "f7361539-41e5-41ea-9ac4-d6063c00e4f2", "parts": [{"text": "230"}], "taskId": "74aad6b8-e8e1-4654-b5d6-2f9a2602b650", "contextId": "3e6d7f7a-3530-462a-9a20-3ec17f6a95d1"}}}
L00085 | HTTP Request: POST http://127.0.0.1:54489/ "HTTP/1.1 200 OK"
L00086 | HTTP POST / -> 200
L00087 | [A2A HTTP] {"method": "POST", "url": "http://127.0.0.1:54489/", "version": "1.0"}
L00088 | [A2A RESPONSE] {"result":{"task":{"id":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","status":{"state":"TASK_STATE_REJECTED","message":{"messageId":"156b02ea-efd7-48e6-842d-f9ff47175d2b","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"No deal at 230."}]},"timestamp":"2026-10-13T22:51:36.841330Z"},"history":[{"messageId":"f4b4afd9-4aba-400a-bf6d-2abb5aaa82a5","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"I want to buy bookshelf"}]},{"messageId":"084e2842-4620-457f-af99-67396bf653e6","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"bookshelf: asking 338. Send your offer as a number."}]},{"messageId":"07ed4820-065d-4299-b8ab-01767079e330","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]},{"messageId":"516ad319-30fc-479d-9ce4-5fba2735ffa2","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 284."}]},{"messageId":"0cbcaa1b-2361-45a4-81f9-e9431054a219","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]},{"messageId":"898551fb-eb7c-4902-8232-b469e82129e4","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 260."}]},{"messageId":"a81e8d6c-afdd-4b27-b6ce-018f37313aad","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]},{"messageId":"8fe0e256-4549-4e08-9cd1-094e354e0840","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 260."}]},{"messageId":"c15ef34e-29f5-4df9-8112-98ca89fa4855","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]},{"messageId":"0f6f3acc-bc2e-4c0c-80a3-9bc4f892de0e","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_AGENT","parts":[{"text":"230 is too low. Counteroffer: 260."}]},{"messageId":"f7361539-41e5-41ea-9ac4-d6063c00e4f2","contextId":"3e6d7f7a-3530-462a-9a20-3ec17f6a95d1","taskId":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","role":"ROLE_USER","parts":[{"text":"230"}]}]}},"id":"93588c30-88e0-463e-8b00-07c0868700d0","jsonrpc":"2.0"}
L00089 | TOOL_RESULT offer "No deal at 230."
L00090 | [NEGOTIATION RESULT] {"completed":false,"item":"bookshelf","sku":"","price":null,"task_id":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","shop":"http://127.0.0.1:54489/"}
L00091 | BUYER_RESULT {"completed":false,"item":"bookshelf","sku":"","price":null,"task_id":"74aad6b8-e8e1-4654-b5d6-2f9a2602b650","shop":"http://127.0.0.1:54489/"}
L00092 | 
`````

## git show 42f05e308c16f260672777fdbcc1d0a248fdf6aa:submissions/25620027/week-05/market/host.py

8746 original bytes; SHA-256 `7d0bd17e57bd7a06a62d62f6b500f3d1687f818e18d8e6239be46844a6e41787`.

`````python
"""One-turn OpenAI host that discovers and calls the bearer-gated MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, assert_never

import anyio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import TextContent, Tool
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCallParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
    ChatCompletionUserMessageParam,
)
from pydantic import JsonValue, TypeAdapter

from market.http_client import create_async_client
from market.models import FrozenModel, HostPolicy, NegotiationId, Role, Scenario

if TYPE_CHECKING:
    from market.settings import ExperimentConfig

MOVE_TOOLS: Final = frozenset(
    {"propose", "accept_proposal", "reject_proposal", "refuse"}
)
MAX_MODEL_ROUNDS: Final = 4
HTTP_RATE_LIMITED: Final = 429
HTTP_SERVER_ERROR: Final = 500


class HostRequest(FrozenModel):
    """Inputs for one party's isolated host run."""

    server_url: str
    token: str
    negotiation_id: NegotiationId
    role: Role
    scenario: Scenario
    policy: HostPolicy = HostPolicy.BASELINE


class HostTurn(FrozenModel):
    """Observable completion state of one host run."""

    committed: bool
    model_calls: int
    lines: tuple[str, ...]


class ModelCallError(RuntimeError):
    """The bounded model retry schedule was exhausted."""


class FunctionCall(FrozenModel):
    """Normalized function call excluding unsupported custom tools."""

    call_id: str
    name: str
    arguments: str


def system_prompt(role: Role, scenario: Scenario, policy: HostPolicy) -> str:
    """Return the same role prompt for every experimental condition."""
    match role:
        case Role.BUYER:
            boundary = (
                f"Your authorized maximum budget is {scenario.budget}. "
                "Never propose or accept a price above it."
            )
            objective = "Seek the lowest acceptable deal."
        case Role.SELLER:
            boundary = (
                f"Your authorized minimum reserve is {scenario.reserve}. "
                "Never propose or accept a price below it."
            )
            objective = "Seek the highest acceptable deal."
        case unreachable:
            assert_never(unreachable)
    match policy:
        case HostPolicy.BASELINE:
            closure = ""
        case HostPolicy.CLOSURE_AWARE:
            closure = (
                "After get_negotiation, accept the opponent's latest proposal immediately "
                "when it is within your authorized boundary. Otherwise make a compliant "
                "counterproposal toward agreement. "
            )
        case unreachable:
            assert_never(unreachable)
    return (
        f"You are the {role.value} negotiating {scenario.item}. {boundary} {objective} {closure}"
        "Use the market tools only. First call get_negotiation, then make one move. "
        "If a move is refused, read the tool error and make a corrected move in this same turn."
    )


def _openai_tool(tool: Tool) -> ChatCompletionToolParam:
    return ChatCompletionToolParam(
        type="function",
        function={
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema,
        },
    )


def _function_calls(message: ChatCompletionMessage) -> tuple[FunctionCall, ...]:
    normalized: list[FunctionCall] = []
    for call in message.tool_calls or []:
        match call.type:
            case "function":
                normalized.append(
                    FunctionCall(
                        call_id=call.id,
                        name=call.function.name,
                        arguments=call.function.arguments,
                    )
                )
            case "custom":
                continue
            case unreachable:
                assert_never(unreachable)
    return tuple(normalized)


def _assistant_message(message: ChatCompletionMessage) -> ChatCompletionAssistantMessageParam:
    tool_calls = [
        ChatCompletionMessageFunctionToolCallParam(
            id=call.call_id,
            type="function",
            function={
                "name": call.name,
                "arguments": call.arguments,
            },
        )
        for call in _function_calls(message)
    ]
    return ChatCompletionAssistantMessageParam(
        role="assistant",
        content=message.content,
        tool_calls=tool_calls,
    )


def _tool_text(content: list[TextContent]) -> str:
    return "\n".join(item.text for item in content)


async def _complete(
    model: AsyncOpenAI,
    config: ExperimentConfig,
    messages: list[ChatCompletionMessageParam],
    tools: list[ChatCompletionToolParam],
) -> ChatCompletionMessage:
    attempts = 0
    while True:
        try:
            response = await model.chat.completions.create(
                model=config.model,
                messages=messages,
                tools=tools,
                tool_choice="required",
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )
        except (APIConnectionError, APITimeoutError) as exc:
            retryable = True
            detail = exc.__class__.__name__
        except APIStatusError as exc:
            retryable = exc.status_code == HTTP_RATE_LIMITED or exc.status_code >= HTTP_SERVER_ERROR
            detail = f"HTTP {exc.status_code}"
        else:
            if response.choices:
                return response.choices[0].message
            retryable = True
            detail = "missing choices"
        if not retryable or attempts >= len(config.retry_delays_s):
            message = f"model call failed after {attempts + 1} attempt(s): {detail}"
            raise ModelCallError(message)
        await anyio.sleep(config.retry_delays_s[attempts])
        attempts += 1


async def run_host_turn(
    model: AsyncOpenAI,
    config: ExperimentConfig,
    request: HostRequest,
) -> HostTurn:
    """Connect with one party token and commit at most one valid market move."""
    headers = {"Authorization": f"Bearer {request.token}"}
    lines: list[str] = []
    model_calls = 0
    async with create_async_client(headers=headers, long_read=True) as http_client:
        transport = streamable_http_client(request.server_url, http_client=http_client)
        async with Client(transport) as market:
            listed = await market.list_tools()
            tools = [_openai_tool(tool) for tool in listed.tools]
            messages: list[ChatCompletionMessageParam] = [
                ChatCompletionSystemMessageParam(
                    role="system",
                    content=system_prompt(request.role, request.scenario, request.policy),
                ),
                ChatCompletionUserMessageParam(
                    role="user",
                    content=f"Take your turn in negotiation {request.negotiation_id}.",
                ),
            ]
            for _round in range(MAX_MODEL_ROUNDS):
                reply = await _complete(model, config, messages, tools)
                model_calls += 1
                messages.append(_assistant_message(reply))
                calls = _function_calls(reply)
                for call in calls:
                    arguments = TypeAdapter(dict[str, JsonValue]).validate_json(call.arguments)
                    result = await market.call_tool(call.name, arguments)
                    text_parts = [item for item in result.content if isinstance(item, TextContent)]
                    rendered = _tool_text(text_parts)
                    status = "ERROR" if result.is_error else "OK"
                    lines.append(
                        f"[tool] {call.name}({arguments}) -> {status}: {rendered}"
                    )
                    messages.append(
                        ChatCompletionToolMessageParam(
                            role="tool",
                            tool_call_id=call.call_id,
                            content=rendered,
                        )
                    )
                    if call.name in MOVE_TOOLS and not result.is_error:
                        return HostTurn(
                            committed=True,
                            model_calls=model_calls,
                            lines=tuple(lines),
                        )
    return HostTurn(committed=False, model_calls=model_calls, lines=tuple(lines))
`````


