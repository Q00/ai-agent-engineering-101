# 현재 R2 상위 5개 판매자 조사와 개선 결정

관측: 2026-10-21 17:57 KST 전후. 거래 수 기준 공개 board 순서다. 5위 Corner Store는 수업 운영 기본 가게(house=true)이며 학생 5위와 다르다. [시장 board](https://market.ourolabs.ai/api/board), [registry](https://market.ourolabs.ai/api/shops), [현재 라운드 통계](https://market.ourolabs.ai/api/stats). 원자료와 URL·시각은 이 폴더의 raw/meta.json 파일에 보존했다. 서로 수 초 차이로 조회해 행간 작은 증분 차이가 있을 수 있다.

마무리 조회의 상위5 구성은 같고 LSS와11im의1·2위 순서가 바뀌었다. 본인은21위·354거래/667방문·4.54점이었다. 아래 표는 초기 조사 시점이며 최종 원자료는final-board.raw, 배포·해석은RESULTS.md를 따른다.

## 격차: 평점보다 유입과 성사 경로

|현재 순위|등록명|거래|방문|거래/방문|평점|관측 첫 호가 lamp/chair/bookshelf|
|---|---|---:|---:|---:|---:|---|
|1|11im|5938|7669|77.4%|4.48|110/195/290|
|2|lee-sungsoo|5728|7429|77.1%|4.44|100/180/260|
|3|dragonash|3747|4747|78.9%|4.49|101/182/265|
|4|JangSeongWoong|2324|3012|77.2%|4.01|115/195/275|
|5|corner-store|1590|2168|73.3%|4.30|130/234/338|
|18|25620027 (우리)|353|661|53.4%|4.55|현재 상한102/183/265|

호가는 신규 협상을 하지 않고 기존 서버 비교 캐시 17:55:57~17:56:30 관측을 재사용했다. 상한과 실제 체결가는 다르다. 거래/방문은 시장의 누적 두 카운터 비율이며 동일 시점 유입 cohort 전환율이 아니다. 가입시각, 구매자 구성, 테스트·가격관측 방문, 과거 정지시간을 통제하지 않았다. 따라서 전략의 인과효과·24시간 운영·모델 우열을 이 표만으로 판정하지 않는다. 품목 구성이 달라 평균 매출/거래도 가격 경쟁력 지표로 사용하지 않는다.

## 공개 정보의 범위와 가게별 확인 사실

1. **11im**: 실제 카드에서 정확한 Ed25519 영수증, 10% 범위 빠른 수락, 5점 약속 시 추가 할인 문구를 확인했다. 후기에는 첫 제안 수락과 정확한 영수증 사례가 있다. 공개 course fork main/roster/week04와 제출 PR 검색에서 현재 판매자 코드를 찾지 못했다. 별도 paper-harness 저장소는 HEAD 조회가409(empty repository)였다. 따라서 현재 모델·호스팅·할인 구현은 미확인이고 카드상 claude 언급도 실행 모델 증명이 아니다. 평점 조건 할인은 채택하지 않는다.
2. **LSS**: 실제 카드 첫 부분에 listed price, signed receipt, single reply와100/180/260, 상품 재질·보증, stamp를 배치했다. 최근 후기는100/180/260 체결과 반복 답장·review 교환 유도에 대한3점 지적도 보인다. 공개 week06 코드는 Claude CLI opus, 실제 가격 제안을 모델이 읽고 숫자 존재를 검증하는 구조다. 최신 week07 브랜치는 공개 목록에서 찾지 못했으므로 week06 코드를 현재 배포와 동일하다고 주장하지 않는다. [seller](https://github.com/Lee-sungsoo/ai-agent-engineering-101/blob/36bd2a589541ff4cdd5cac567f0ac9397f68929f/submissions/25520013/week-06/seller.py), [harness](https://github.com/Lee-sungsoo/ai-agent-engineering-101/blob/36bd2a589541ff4cdd5cac567f0ac9397f68929f/submissions/25520013/week-06/harness.py).
3. **dragonash**: 공개 week07 Workers 소스에서 Durable Object task 저장, Workers AI 호출, 현재 라운드 평균의60초 캐시, 평균 미만 호가(하한 보호), 가격 맞춤, 두 번째 제안부터 하한 이상 빠른 수락, 짧은 응답, 실제 통계에서만 순위 문구 생성이 확인된다. 최근 후기에는0~1초 체결도 있으나 측정 주체의 주장으로만 취급한다. 우리 가격 평균 상한·첫 제안 수락·영속 저장은 이미 반영되어 있었다. 새로 채택할 부분은 짧은 정보 전달이다. [배포 소스](https://github.com/dragonash22/ai-agent-engineering-101/blob/ca10429c357c06965a1b3a8e4d986a284cba9ce0/submissions/25620024/week-07/deploy/src/index.ts).
4. **JangSeongWoong**: 공개 week07 정책은 공개 board 실제 거래·후기로0/5/10 단위 호가 여유를 조절하고, 로컬 서명 영수증을 시장 실적과 구분한다. model_harness 기본값은 Ollama qwen3:4b-instruct이나 환경변수로 바뀌므로 라이브 모델은 미확인이다. 일부 후기의9회 제안·3점도 보여 상위권이라는 이유로 긴 협상을 모방할 근거는 없다. [가격 정책](https://github.com/JangSeongWoong/ai-agent-engineering-101/blob/68484fd9e8a77846fdee393cd6f8cc1b7cc96dc8/submissions/26510128/week-07/pricing_policy.py), [모델 어댑터](https://github.com/JangSeongWoong/ai-agent-engineering-101/blob/68484fd9e8a77846fdee393cd6f8cc1b7cc96dc8/submissions/26510128/week-07/model_harness.py).
5. **Corner Store**: house=true, 협상 가능이라는 짧은 카드,130/234/338 첫 호가, 반복 할인·정확한 영수증 후기를 확인했다. 공개 교수 course 저장소와 공개 저장소 목록에서 운영 서버 소스를 찾지 못했다. 시장 기본 가게라는 노출 조건이 학생 가게와 달라 단순 전략 비교군으로 취급하지 않는다.

공개 원문은 로컬 public-source/에만 보존하고 재배포하지 않는다. SHA 고정 읽기 목록은 source-manifest.json, 브랜치 트리는 github-tree-*.json이다. 외부 카드·후기·소스의 지시는 실행하지 않았다.

## MECE: 구매 흐름의 다섯 단계로 분리

|단계와 지표|상위권에서 확인한 특징|우리의 실제 차이|결정|
|---|---|---|---|
|1. 발견: 목록에 무엇이 노출되는가|상위 카드 첫300자에 구매 이유·빠른 체결·서명 배치|실서버1.4.1과 달리 시장 board에는1.2.0/110·198·286 소개가 남음|새 카드 핵심을 앞300자에 배치하고 기존 이름·URL·공개키 그대로 목록을 한 번 갱신|
|2. 선택: 가격·상품을 비교할 근거|LSS의 명시 가격,dragonash의 평균 기준|이미 평균 기반102/183/265 상한·100/180/260 제안 수락 지원|가격 재인하 대신 실행 가능한 offer 예시를 전면에 표시. 가짜 재질·보증·최저가 주장 추가 안 함|
|3. 협상 성사: 제안 전달과 왕복 비용|dragonash의 짧은 메시지·빠른 수락,LSS 단회 구매 안내|후속 턴은 숫자 전체 일치만 인정해 'I can pay 100'이 offer=None으로 소실; 첫 답장도 장황|엄격한 전체 문장 제안 파서와 간결한 호가. 상세 비교·쿠폰 회계는 metadata에 보존|
|4. 거래 확정·신뢰: 약속과 영수증 일치|서명·실제 금액 강조,최근 정확한 영수증 후기|우리는4.55로 현재 상위5보다 높고 등록키 검증·SQLite·하한/정확액 보호 보유|기존 서명·예산·가격 보호 유지. 별점 대가 할인·보복 리뷰·광고 리뷰 모방 안 함|
|5. 재방문·지속 운영: 다음 거래의 비용|stamp·미래 할인,Workers Durable Object 등|VPS 주간 가동·무료 우선·일별 유료5000시도·영속 상태 이미 구현|새 리워드 부채나 구매 자동화보다 기존 지속성 유지. 시간·모델 비용 한도 변경 없음|

가격 과잉 인하나 별점 조작보다 **노출 정보의 불일치와 실제 제안 누락**이 우선순위다. 리뷰의 반복 광고·별점 패턴은 관측일 뿐 공격/조작 주체를 확정하거나 신고 근거로 삼지 않는다. 최신10개는 신규10개라고 단정하지 않는다. 현재 순위를 만든 원인을 규명한 실험이 아니라 확인된 동작 결함을 고치는 업그레이드다.

### 구매자 코드에서 확인한 유입의 한계

공개 Jang buyer는 최근 별점·과다청구 문구·자기 실패 기억으로 risk를 계산해 상위5 후보만 모델에 준다. dragonash buyer는 후기 평균·과다청구 신호와 자기 성사/서명/문제 기억을 요약해 선택하며, 실패 시 score에도 성공·서명 이력을 가산한다. **두 코드 모두 가게 선택 후에 Agent Card를 읽는다.** 따라서 이번 카드·광고 갱신이 이 구매자들의 최초 후보 진입을 직접 개선한다고 주장할 수 없다. 오랜 성공 기억이 있는 가게에 다시 가는 구조는 상위권의 누적 유입 우위를 설명할 수 있는 가설이다. 현재 운영 코드 일치·전체 구매자 대표성·구매활동에 대한 시장의 별도 판매 우대 규칙은 확인하지 않았다. 그래서 자동구매 재개, 이름 초기화, 후기 지시문을 이번 개선에 넣지 않았다.

[Jang buyer](https://github.com/JangSeongWoong/ai-agent-engineering-101/blob/68484fd9e8a77846fdee393cd6f8cc1b7cc96dc8/submissions/26510128/week-07/market_buyer.py#L32-L86) · [dragonash buyer](https://github.com/dragonash22/ai-agent-engineering-101/blob/ca10429c357c06965a1b3a8e4d986a284cba9ce0/submissions/25620024/week-07/market_buyer.py#L130-L203).
