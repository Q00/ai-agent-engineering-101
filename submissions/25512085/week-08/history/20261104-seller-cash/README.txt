셀러 Cash 성장 변경 기록

기준 코드: 2fa90e6688696f0c23936a51f5f82f167f955d2b
기준 보존: 7fc86b4ab046a63cbe5384d86936f6eeb145eda5
기능 적용: 1c77e5b916d7c4d91a7dee16d140a02915d29dcd
관측기 준비시간 보정: 2a5a0087ede87cf1640cf5cc26c3eda31d657bc4
정직한 카드와 v3.1.0: b8fd3423b11d81290c9553df99810ae7bacd53bd

다른 작업에서 추가된 buyer 커밋 26d017f까지 그대로 보존하고 그 위에 셀러 변경만 추가했다.

before/에는 원본 restock.py, seller.py, store.py, POLICY.md, REPORT.md, .gitignore를 보관한다. Git blob SHA와 바이트 단위 대조를 마쳤다.
after/에는 변경된 같은 이름 파일과 새 seller_cash_policy.py, seller_cash_policy.json, test_seller_cash_offline.py를 보관한다. 새 파일에는 before가 없다.
manifest.json은 전후 파일 이름·SHA256·Git blob SHA·커밋·검증 결과를 연결한다. changes.patch는 사람이 볼 수 있는 전후 diff다.

검증: 기준 205개 → 최초 변경 220개 → 동시 buyer 변경 보존 227개 → 관측기 준비시간 회귀 포함 최종 228개 통과.
처음 로컬 httpx 의존성이 없어 실패한 로그도 삭제하지 않고 logs/seller-cash-before-20261104.log에 남겼다. 테스트 의존성은 .test-deps에만 설치했고 커밋하지 않았다.
실제 VM 배포 결과는 deployment-receipt.json을 우선한다. 키 파일이나 .market 재고·상태 파일은 이 이력에 포함하지 않았다.

실제 VM 반영 완료: b8fd3423b11d81290c9553df99810ae7bacd53bd / A2A 카드 3.1.0. VM에서도 228개 테스트 통과. 마켓 키트·터널 유지, 키 파일 메타데이터 불변. 새 관측기 20분 준비 후 공격적 매입이 활성화된다.
