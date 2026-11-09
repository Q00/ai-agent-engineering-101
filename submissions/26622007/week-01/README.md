# Week 01 — 길이 단위를 통일해서 계산하기

## 문제와 도구

`notes.txt`의 2 m, 35 cm, 10 inch 부품을 이어 붙인 총길이를 cm로 계산한다.
`read_file`은 입력을 읽고, `convert_units`는 길이 단위를 바꾸며, 기존 `calculator`는 사칙연산을 한다.
모델에게 도구 호출 순서나 정답을 주지 않는다. 도구 설명과 입력을 보고 모델이 선택한다.

`convert_units(value, from_unit, to_unit)`은 `value × 출발 단위의 미터 환산값 ÷ 목표 단위의 미터 환산값`으로 계산한다.
예를 들어 `convert_units(10, "inch", "cm")`는 `25.4 cm`를 반환한다.
지원 단위는 `mm`, `cm`, `m`, `km`, `inch`, `ft`이며 대소문자를 구분한다.
0과 음수도 수치로 변환한다. 지원하지 않는 단위와 유한하지 않은 숫자는 오류로 돌려준다.
환산 상수는 Decimal로 계산한다. 순환소수가 생기는 변환은 기본 Decimal 정밀도인 28자리로 계산되므로 모든 변환이 무한 정밀도로 정확한 것은 아니다.

## 실행 환경

- Python 3.12.10, `openai==2.40.0`으로 검증한다.
- 제공자: OpenRouter, 기본 모델: `nvidia/nemotron-3.5-lightning:free`.
- temperature=0, 최대 모델 호출 8회, 호출당 제한 시간 45초, SDK 자동 재시도 0회.
- 시스템 프롬프트는 추가하지 않았다. 기본 요청은 `first_agent.py`의 `TASK`이며 도구 스키마는 같은 파일의 `TOOLS`에 있다.
- 무료 모델의 제공 여부나 용량이 달라질 수 있다. 같은 설정도 모델 출력의 완전한 재현을 보장하지 않는다.

아래 명령은 이 주차 폴더에서 실행한다. API 키는 환경변수에만 설정한다.
기존 프로젝트 전용 OpenRouter 키를 `OPENAI_API_KEY`로 설정하고, 키 값이나 `.env`는 제출하지 않는다.

```bash
python -m pip install -r requirements.txt
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
# OPENAI_API_KEY에는 본인 프로젝트의 OpenRouter API 키를 환경변수로 설정
mkdir -p logs
set -o pipefail
python -u first_agent.py 2>&1 | tee "logs/$(date +%Y%m%d-%H%M%S)-three-tools.txt"
```

원래 두 도구만 제공하는 비교 실행:

```bash
python -u first_agent.py --baseline 2>&1 | tee "logs/$(date +%Y%m%d-%H%M%S)-baseline.txt"
```

파일명은 매번 새로 정한다. 실행 로그를 덮어쓰거나 수정하지 않는다.
baseline은 같은 코드에서 `convert_units`를 모델에게 제공하지 않고 실행 허용 목록에서도 제외한다.
모델은 단위 환산식을 스스로 만들어 calculator를 사용할 수도 있다.

## 검증

```bash
# 이 주차 폴더에서 실행: API를 호출하지 않는 도구 검증
python -m unittest -v test_convert_units.py
python -m py_compile first_agent.py test_convert_units.py
```

저장소 루트에서 구조 검사를 실행한다.

```bash
python scripts/check_week01.py submissions/26622007/week-01
```

오프라인 정답은 200 cm + 35 cm + 25.4 cm = **260.4 cm**다.
오프라인 테스트와 구조 검사는 실제 모델이 도구를 적절히 선택했다는 증거를 대신하지 않는다.
실제 실행과 관찰은 `logs/`, 시도와 변경 이유는 `PROCESS.md`를 확인한다.

## 실제 관찰 요약

2026-09-16에 각 조건을 1회씩 실제 실행했다. 두 조건 모두 260.4 cm를 반환했다.
baseline은 `read_file → calculator`, 세 도구 조건은 `read_file → convert_units → convert_units → calculator` 순서였다.
새 도구를 제공하자 모델이 환산을 Python에 맡겼고, 이미 cm인 항목은 다시 변환하지 않았다.
모델 호출은 3회/5회, 총 입력+출력 토큰은 1944/4603이었다. 1회 관찰이므로 일반적인 성능 우열로 해석하지 않는다.
