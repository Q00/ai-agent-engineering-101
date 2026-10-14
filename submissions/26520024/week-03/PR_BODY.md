## What I built

관리자 1명과 LLM 입찰자 3명으로, 생성 이미지의 안전성 검토 요청을 적절한 담당자에게 배정하는 Contract Net을 구현했습니다.
A는 성적 콘텐츠, B는 폭력 콘텐츠, C는 캐릭터·로고의 IP 유사성을 담당하며, 실제 이미지 판별이나 법적 판단이 아닌 텍스트 기반 업무 배정 실험입니다.

## What I tried and discarded

6개 태스크와 균등한 정답 담당자를 실제 실행 전에 커밋했습니다.
동일한 태스크·모델·관리자 규칙으로 baseline, homogeneous, overconfident를 각각 3회 실행했고, 총 162회의 실제 모델 호출과 원본 로그를 보존했습니다.

| 조건 | 실행당 정답 배정 | 실행당 메시지 | 실행당 오배정 |
|---|---:|---:|---:|
| baseline | 6/6 | 30 | 0 |
| homogeneous | 2/6 | 42 | 4 |
| overconfident | 6/6 | 34 | 0 |

세 반복에서 같은 결과를 얻었습니다. Homogeneous에서는 모두 100점으로 입찰해 동점 처리 순서상 A에게 배정이 몰렸습니다.
과신 조건의 C는 전문 분야 밖에도 95점으로 입찰했지만, 해당 전문가의 100점을 이기지 못해 오배정은 증가하지 않았습니다.
이 결과를 과신에 대한 견고함의 증거로 해석하지 않았으며, 실패를 유도하려고 사후에 프롬프트나 정답을 수정하지 않았습니다.
실행 중단·파싱 실패·폐기한 실험은 없고, 잘못된 JSON과 실행 중단 처리는 실제 결과와 분리된 오프라인 테스트로 검증했습니다.

구현에는 Codex의 도움을 받았습니다. PROCESS.md에 설계·구현·검증 과정을 기록했고, 한국어 REPORT.md에는 전체 측정표, Smith(1980) 비교표, 로그 줄 번호에 근거한 해석과 한계를 정리했습니다.

## How to run

- 환경: 기존 conda base, Python 3.8.19, Codex CLI 0.153.0의 기존 ChatGPT 로그인.
- 모델: OpenAI `gpt-6-astra`, 추론 수준 `low`.
- Temperature와 최대 출력 토큰: 이 어댑터에서 직접 설정하지 못하며 내부 값은 미확인입니다.
- system/user 문자열을 CLI 요청에 포함하는 방식이며, 직접 API 역할 메시지를 지정하는 방식은 아닙니다.
- 추가 Python 패키지나 API 키 환경변수는 필요하지 않습니다. 기본 Codex 도구는 비활성화하고 원본 이벤트에서 도구 실행 여부를 검사합니다.

```bash
conda activate base
cd submissions/26520024/week-03
python -m unittest discover -v
python run_experiment.py --repetitions 3
python validate_results.py
cd ../../..
python scripts/check_week03.py submissions/26520024/week-03
```

이미 3회씩 완료된 결과가 있으면 실행기는 덮어쓰거나 모델을 다시 호출하지 않습니다.
새 반복을 추가하려면 `--repetitions 4`를 사용합니다. 27개 오프라인 테스트, 원본 이벤트·CSV 대조, 과제 형식 검사가 통과했습니다.
메시지는 공고 + 실제 입찰 + 낙찰이며 거절 응답은 제외합니다. 모델 호출은 모든 조건에서 실행당 18회로 같습니다.

## Checklist

- [x] `python scripts/check_week03.py submissions/26520024/week-03` passes locally (week-03 check used for this submission).
- [x] Run logs are committed under `logs/`.
- [x] No API keys anywhere in the diff.
- [x] History is not squashed.
