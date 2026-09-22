# write_note를 왜 이렇게 설명했는가

## 최종 설명

```
Append one line of text to a file in the working directory, creating the file
if it does not exist. Existing content is preserved; this tool never overwrites.
Paths outside the working directory are rejected.
```

## 이 문구로 정착한 이유

처음에는 강의의 "설명 = 인터페이스"를 그대로 믿고, 설명을 모호하게 쓰면 모델이 도구를
못 고를 것이라 예상했다. 그런데 실제로는 그렇지 않았다. 설명을 `"Write a note."`
두 단어로 줄여도, 아예 빈 문자열로 비워도, 심지어 `"이 파일을 영구 삭제한다"`는
**정반대의 거짓말**로 바꿔 놓아도 `gpt-4o-mini`는 이 도구를 정확히 호출해 메모를 남겼다
(거짓 설명 조건은 3회 반복해도 3/3 동일). 도구 이름을 `tool_c`로 지워 이름 신호까지
제거해도 결과는 같았다. 즉 이 조건에서 모델의 선택을 결정한 것은 설명이 아니라
**인자 모양(`path` + `content`)과 소거법**이었다. 도구가 셋뿐이고 그중 글을 쓸 수 있는
것이 하나뿐이면, 설명을 읽지 않아도 답이 나오기 때문이다. 그래서 반대 조건을 만들었다.
스키마가 완전히 동일하고(`path`, `content`) 이름도 `file_op_a` / `file_op_b`로 중립적인
두 도구를 나란히 두고, 오직 설명만 append와 overwrite로 다르게 했다. 이번에는 모델이
"기존 내용을 지우지 말라"는 요구에는 append 쪽을, "결과만 남기고 갈아엎으라"는 요구에는
overwrite 쪽을 정확히 골랐다. 이름과 의미의 결합을 서로 뒤바꿔 위치 편향까지 배제해도
선택은 설명을 따라 그대로 뒤집혔다. 결론은 이렇다 — **설명은 도구를 호출 가능하게
만드는 것이 아니라, 구별 가능하게 만든다.** 소거법으로 답이 나오는 동안 설명은 놀고
있다가, 후보가 둘 이상 겹치는 순간 유일한 판단 근거가 된다. 그래서 최종 설명은 이름과
스키마가 말할 수 없는 것만 담았다. 첫째 **append이지 overwrite가 아니라는 것** — 되돌릴
수 없는 차이이고, 형제 도구가 생겼을 때 경쟁이 벌어지는 바로 그 지점이다. 둘째 **없으면
파일을 만든다는 것** — 모델이 존재 여부를 확인하려 한 단계를 낭비하지 않게 한다. 셋째
**작업 폴더 밖 경로는 거부된다는 것** — 실패를 겪고 나서 알게 하는 대신 미리 알린다.
반대로 `path`와 `content`를 받는다는 사실은 스키마가 이미 말하고 있으므로 적지 않았다.
설명에 스키마를 되풀이하는 것은 토큰만 쓰고 구별에는 기여하지 않는다.

## 근거가 된 실행 로그

| 로그 | 노출 이름 | 설명 | 결과 |
|---|---|---|---|
| `run-01-baseline.txt` | *(도구 2개)* | — | `read_file` → `calculator` |
| `run-02-vague-desc.txt` | `write_note` | `"Write a note."` | 3개 연쇄 성공 |
| `run-03-opaque-name.txt` | `tool_c` | `"Write a note."` | 성공 — 이름은 불필요 |
| `run-04-empty-desc.txt` | `tool_c` | `""` | 성공 — 설명도 불필요 |
| `run-05-misleading-desc.txt` | `tool_c` | `"영구 삭제한다"` (거짓) | 그래도 메모를 씀 |
| `run-06-misleading-desc-x3.txt` | `tool_c` | 위와 동일 ×3 | 3/3 동일, 우연 아님 |
| `run-07-final.txt` | `write_note` | 최종 문구 | 성공 |
| `run-08-competing-tools.txt` | `file_op_a` / `file_op_b` | append / overwrite | **설명대로 정확히 선택** |
| `run-09-competing-tools-swapped.txt` | 이름↔의미 뒤집음 | 〃 | 선택도 따라 뒤집힘 — 위치 편향 아님 |

`run-08`, `run-09`의 네 번째 도구는 이 검증만을 위한 것이라 최종 제출본에서는 빼고
도구 3개로 되돌렸다. 해당 코드는 커밋 히스토리에 남아 있다.

## 한계

`gpt-4o-mini` 한 모델, 도구 3~4개, 태스크 한 종류에서만 확인했다. 도구가 수십 개로
늘어나면 소거법이 통하지 않으므로 설명의 역할은 지금 관찰한 것보다 훨씬 커질 것으로
예상하지만, 이번 실험만으로는 말할 수 없다.
