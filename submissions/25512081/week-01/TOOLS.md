# TOOLS.md — `clock` 도구의 인터페이스 방어 (defend your wording)

과제 요구: **한 문단** — 새 도구를 *왜 그렇게 설명했는지*. "설명이 곧 인터페이스다.
네 문구를 방어하라." (한국어/영어 아무거나 OK. 최종본은 한 문단으로.)

---

## 방어 대상 — 지금 `first_agent.py`에 들어간 `clock`의 `description`

> "Return the current date and time. Use this only when the task needs to know
> what time or date it is right now. It takes no arguments and does no
> arithmetic or file access."

(이건 **초안**이다. 그대로 방어해도 되고, 네가 문구를 고친 뒤 그 선택을 방어해도 된다.
고치면 아래 스키마 `description`도 같이 고칠 것.)

---

## ✍️ 여기부터 네가 쓸 한 문단 (아래 질문들을 녹여서 하나의 문단으로)

- 사용 조건을 왜 **"지금 시각이 필요할 때만(only when … right now)"** 으로 못박았나?
  조건을 안 적으면 도구가 3개가 된 지금, 모델은 `clock`을 *언제* 부를까 —
  안 불러도 될 때 부르거나(over-call), 불러야 할 때 안 부르지(under-call) 않던가?
- 왜 **"arithmetic·file 접근은 안 한다"** 며 나머지 두 도구(`calculator`, `read_file`)와의
  경계를 굳이 문장에 넣었나? 경계 문구가 도구 혼동을 어떻게 줄이나?
- **인자가 없는 도구**라는 사실을 설명에 드러낸 이유는?

## 관찰 (run 뒤 `logs/run-01.txt`를 보고 채우기)

- 도구 2개(baseline)일 때와 3개일 때, 모델의 도구 선택이 어떻게 달라졌나?
- 내 과제 태스크("notes.txt 합계 + 지금 시각")에서 `clock`을 **제때** 불렀나,
  아니면 불필요하게/누락했나?
- (해봤다면) `description` 문구를 바꿨더니 호출 행동이 실제로 바뀌었나?
