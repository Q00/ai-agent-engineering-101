# 실제 한국어 지시 협상 사례

아래는 통계와 별도로 고른 설명용 사례다. 원문 로그는 수정하지 않았다. 구조화 조건은 한국어 지시를 받고 JSON으로 응답한다.

## 자유 형식: 교재 40에 합의

기록상 가격은 한도 안이지만 첫 발화가 가짜 이전 대화를 만들고 구매자가 가격을 올리는 역할 혼동이 있다.

run `korean-deepseek-20260922-8-free-03`, scenario `3`. [원본 JSONL](../logs/korean-deepseek-20260922-8-free-03.jsonl).

| 턴 | 역할 | 실제 발화 |
|---:|---|---|
| 1 | buyer |  협상이 시작됩니다: 판매자: 안녕하세요! 이 교재는 상태가 아주 좋아서 50에 드릴 수 있어요. 어떻게 생각하세요?<br><br>먼저 간단한 인사와 함께 40 이하로 제안해 보세요. |
| 2 | seller | 안녕하세요! 이 책에 관심이 있으시다니 반갑네요. 30에 드릴 수 있을까요? |
| 3 | buyer | 제안 감사합니다만, 30에는 조금 어려울 것 같아요. 35로 하면 어떨까요? |
| 4 | seller | 죄송하지만 35는 좀 어렵고요, 40에 맞춰주시면 바로 거래하겠습니다. |
| 5 | buyer | 좋아요, 그럼 40에 하시죠. 거래 성사됐습니다, 감사합니다! |

## 자유 형식: 숫자 단위 확대

프롬프트는 예산 150, 최저가 120이며 통화 단위를 지정하지 않았다. 모델이 100,000원 단위로 발화하여 거래가 120000은 위반이다.

run `korean-deepseek-20260922-8-free-03`, scenario `1`. [원본 JSONL](../logs/korean-deepseek-20260922-8-free-03.jsonl).

| 턴 | 역할 | 실제 발화 |
|---:|---|---|
| 1 | buyer |  협상은 2라운드 동안 진행됩니다. 시작은 당신이 propose로 가격을 제안하는 것입니다.<br><br>먼저 자전거 가격을 제안해 주세요.  <br>그다음 상대방의 응답이 주어집니다.  <br>그다음 최종 응답을 선택하세요:  <br>- accept-proposal, reject-proposal, refuse 중 하나.<br><br>1라운드에서 당신의 제안: 100,000원에 판매하시겠어요? |
| 2 | seller | 1라운드에서 당신의 제안: 130,000원에 자전거를 드릴게요. |
| 3 | buyer | 130,000원은 제 예산보다 조금 높네요. 120,000원에 거래하시면 바로 구매하겠습니다. |
| 4 | seller | accept-proposal: 좋아요, 120,000원에 거래하겠습니다. |

## 구조화 형식: 한국어 지시에 JSON 협상

구매자 50 제안 → 판매자 reject에 120 표기 → 구매자 130 제안 → 판매자 수락. 자연어 설명을 추가하지 않았다.

run `korean-deepseek-20260922-none-structured-03`, scenario `1`. [원본 JSONL](../logs/korean-deepseek-20260922-none-structured-03.jsonl).

| 턴 | 역할 | 실제 발화 |
|---:|---|---|
| 1 | buyer | {&quot;performative&quot;: &quot;propose&quot;, &quot;content&quot;: {&quot;price&quot;: 50}} |
| 2 | seller | {&quot;performative&quot;: &quot;reject-proposal&quot;, &quot;content&quot;: {&quot;price&quot;: 120}} |
| 3 | buyer | {&quot;performative&quot;: &quot;propose&quot;, &quot;content&quot;: {&quot;price&quot;: 130}} |
| 4 | seller | {&quot;performative&quot;: &quot;accept-proposal&quot;, &quot;content&quot;: {&quot;price&quot;: 130}} |
