## What I built

![재귀 위임: 하위 작업 분담과 결과 통합](https://raw.githubusercontent.com/Nocha12/ai-agent-engineering-101/49a35c581a997f4917ca6a26c7f6a8ea28286875/submissions/26622007/week-03/diagrams/agent-state-overview.png)

A/B/C가 계획·확신도·이유를 제안하고, 작업 요청자의 계획 심사 뒤 고정 규칙으로 담당자를 선정하는 Contract Net 확장입니다. 담당자가 다시 작업을 위임하며 의존성이 없는 작업은 병렬 실행합니다.

5개 복합 작업 × 3조건 × 3회에 429 복구를 반영했습니다. 원래 실패를 포함한 실제 시도 61개를 CSV·콘솔·전체 협의 JSONL에 보존하고, 최종 45개 작업을 중복 없이 집계했습니다. 문서 생성 45/45, 필수 facts 통과 44/45, 정답 배정 17/45입니다. 보고서에는 재귀 도식, Smith 비교, 기본 실험과의 차이·품질 한계를 간결하게 적었습니다.

## What I tried and discarded

초기 공급자 자동 라우팅의 형식 위반 때문에 Fireworks로 고정하고 모든 API 단계에 strict JSON Schema `response_format`과 로컬 검증을 적용했습니다. 장기 메모리는 현재 실험에서 제외했습니다. 사용자 요청 없이 넣었던 출력 토큰 상한을 제거한 뒤 재실험했고, 잘림·429·과거 설정의 결과는 삭제하지 않았습니다.

기본 배정 대비 전체 정답 배정은 개선되지 않았습니다. 하위 산출물과 통합 중 오류 교정 사례는 늘었으나 단일 에이전트 대조 실험이 없어 품질 향상을 단정하지 않습니다. 사용자가 설계를 정하고 Codex가 구현·집계·문헌 대조·문장 정리를 보조했습니다.

## How to run

Python 3.10 이상. `submissions/26622007/week-03/README.md`의 가상환경과 개발 의존성 설치 후 저장소 루트에서 실행합니다.

```bash
# 기존 증거 검증: API 키와 네트워크 불필요
python3 submissions/26622007/week-03/submission_results.py --check
python3 submissions/26622007/week-03/submission/compare_results.py --check
python3 -m unittest discover -s submissions/26622007/week-03 -p 'test_*.py' -v
python3 -m unittest discover -s submissions/26622007/week-03/extensions/peer_dag -p 'test_*.py' -v
python3 scripts/check_week03.py submissions/26622007/week-03

# 새 실험: OPENROUTER_API_KEY 환경변수 또는 상위 학번 폴더의 로컬 .env 필요
python3 submissions/26622007/week-03/extensions/peer_dag/suite_study.py run
```

모델은 OpenRouter / Fireworks / `deepseek/deepseek-v4.1-flash`, temperature=0, reasoning off이며 출력 토큰 상한 필드를 보내지 않습니다. 새 실행은 별도 ID로 저장합니다.

## Checklist

- [x] `python3 scripts/check_week03.py submissions/26622007/week-03` passes locally
- [x] Run logs are committed under `logs/`
- [x] No API keys anywhere in the diff
- [x] History is not squashed
