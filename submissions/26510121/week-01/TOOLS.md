# write_note: experimental descriptions

현재 설명은 `Append a note to settlement.txt.`이다. 도구의 행위와 저장 대상을 짧게 전달하는 기준 설명으로 선택했다. 도구는 content 문자열을 받아 제출 폴더의 settlement.txt에 UTF-8로 덧붙이고 성공 또는 실패를 반환한다. 계산과 요약은 수행하지 않으며 기존 기록도 덮어쓰지 않는다. 경로를 인자로 받지 않는 것은 이번 정산 작업에 필요한 기록 파일 하나로 쓰기 범위를 제한하기 위해서다. 사용자와 함께 선택한 설계를 바탕으로 AI 코딩 도우미가 구현했다. 이 문구가 최선이라는 결론은 아직 내리지 않았으며, 실제 실행 후 저장 요청이 있을 때만 사용하도록 명시한 설명 B와 비교할 예정이다.

위 문단은 설명 A를 선택했던 당시의 기록이다. 현재 코드는 아래 설명 B를 실험하기 위한 상태이며 최종 선택은 아직 하지 않았다.

## 설명 B의 의도

2026-09-07 첫 비교 결과: 설명 A에서는 계산식 없이 결과만 저장했으나, B에서는 calculator를 두 번 호출하고 계산식과 결과를 모두 저장했다. 원본 증거는 logs/three-tools-20260907-131227-429906.txt와 logs/description-b-20260907-131712-013300.txt이다. 이 결과와 명확한 사용 지침을 근거로 현재 B를 유지한다. 각 한 번의 실행이므로 효과의 일반화나 인과관계 확정은 하지 않으며, 저장 요청이 없는 경우의 동작은 아직 미검증이다. 아래의 '아직 남아 있다'는 구현 당시 계획이며 이번 저장 요청 실행 결과는 이 문단에 추가했다.

설명 A 첫 실행에서 파일 저장에는 성공했지만 파일에 계산식이 빠졌다. B에는 사용자가 저장을 요청할 때만 사용할 것, 기존 내용을 유지하며 UTF-8로 추가할 것, 비용 항목뿐 아니라 계산식과 결과 및 1인당 부담액을 content에 포함할 것을 명시했다. 또한 도구 자체는 계산이나 검증을 하지 않는다는 경계를 설명했다. 이는 모델에 전달하는 사용 지침이며 도구가 내용의 정확성을 자동 보장한다는 뜻은 아니다. calculator 사용을 강제하는 프롬프트는 추가하지 않았다. 저장 조건과 내용 지침을 함께 바꿨으므로 차이가 생겨도 어느 한 문장 때문이라고 단정할 수 없다. B의 실제 결과 및 저장 요청이 없는 경우의 검증은 아직 남아 있다.

```text
Use only when the user requests saving or recording results. Append the supplied text to settlement.txt in the agent directory as UTF-8, preserving existing content. For expense settlements, include the expense breakdown, explicit arithmetic expressions with their results, and the per-person share in content. This tool does not calculate or verify numbers. It returns a saved confirmation or an error.
```
