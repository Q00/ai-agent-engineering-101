# TOOLS.md

세 번째 도구로 `write_note`를 추가했다. 작업 폴더 안의 파일에 텍스트를
저장하는 도구이고, `read_file`과 같은 경로 제한을 걸었다.

설명은 처음에 "Save text to a file in the working directory."로 썼다.
기존 두 도구와 같은 톤으로, 무엇을 하는지만 담고 언제 쓰라는 지시는
넣지 않았다. 이 상태로 기존과 동일한 목표("notes.txt를 읽고 숫자를
더하라")를 주고 실행했더니, 모델은 `read_file`과 `calculator`만 부르고
`write_note`는 부르지 않았다.

설명을 "Save the result to a file. Use this whenever you produce a final
answer."로 바꿔 저장을 유도해 봤지만 행동은 같았다. 도구가 목록에
있다는 사실만으로는 호출되지 않고, 설명으로 유도해도 사용자 목표에
없는 동작은 하지 않았다.

그래서 최종 설명은 유도 문구를 뺀 첫 번째 버전으로 확정했다. 유도
문구는 행동을 바꾸지 못하면서 "이 도구는 항상 써야 한다"는 잘못된
인상만 남기기 때문이다. 도구 설명은 그 도구가 무엇을 하는지만
정확히 적고, 언제 쓸지는 모델의 판단에 맡기는 편이 낫다는 결론이다.


## 실행 방법

- Python 3.10+, `pip install anthropic`
- 모델: claude-sonnet-4-5
- 환경변수 `ANTHROPIC_API_KEY` 설정
- `submissions/25512092/week-01/` 폴더 안에서 `python first_agent.py`
  (read_file이 작업 폴더 밖을 차단하므로 실행 위치가 중요하다)