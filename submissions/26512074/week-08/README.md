# Week 08 — 26512074 중간 평가 제출

현재 설계·실측 결과는 [REPORT.md](REPORT.md), 구조도는 [architecture.pdf](architecture.pdf)와 [architecture.tex](architecture.tex)에 있다.

## 읽는 순서

1. REPORT.md의 (1) 부품 설계, (2) 구조와 기본 Kit의 차이, (3) Round 3 결과.
2. logs/round3-final-board.json: 2026-11-17 23:58:29 KST의 마지막 유효 Class 기록. 정확한 23:59 확정 기록은 아니다.
3. logs/round3-results.json, round3-rejections.json, market-kit.log: 공식 누적·로컬 범위·거부·취소 구분과 원문 근거.
4. logs/week10-lab.json, strategy-evaluations.json: LAB 기준과 공동 평가.

## 재현

Python 의존성은 requirements.txt에 있다. 기본 오프라인 확인은 `python -m unittest tests.test_terminal_round3 tests.test_rank_velocity_v8.TimeExecutionTests tests.test_sergio_refusal tests.test_round3_contract tests.test_patch36 tests.test_seller_handoff`로 실행한다. 전체 제출 검증 명령은 logs/validation.txt를 참고한다. 테스트는 fixture를 사용하며 실제 결제를 하지 않는다.

도식은 `tectonic architecture.tex`로 빌드한다(Tectonic 0.17.0, Windows 맑은 고딕·Consolas 필요). PDF에는 글꼴이 포함돼 있어 열람 시 LaTeX 설치가 필요 없다.

최종 일반 거래는 credit-policy.json에 의해 자동 모델 호출이 비활성이다. 초기 배치 모델의 실제 기록만 logs/policy-generation.log에 별도 보존했다. 과거 충성·제휴·가상 길드·모델 planner 코드가 존재한다는 사실을 최종 활성 정책으로 해석하지 않는다.

계정 키·owner code·.env·실제 운영 .market DB는 제출하지 않는다. Round 3이 종료돼 이 제출 확인 과정에서는 시장 재등록·거래·전략 변경을 실행하지 않았다. 기존 Week 09 및 Round 4 준비 작업은 다른 checkout에 보존돼 있다.
