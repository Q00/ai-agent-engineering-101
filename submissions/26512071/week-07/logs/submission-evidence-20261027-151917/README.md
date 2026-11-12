# 최신 원본 증거 보관

- 보관 시각: 2026-10-27 15:19:17 KST
- 생성 명령: 저장소 루트에서 `python submissions/26512071/week-07/package_evidence.py`
- 모든 `.gz`는 원본 전체 바이트의 gzip 압축본이다. 원본 로그를 삭제·편집·잘라내거나 순환시키지 않았다.
- `manifest.json`에 원본 크기·SHA-256·압축 크기와 보고서 집계 시점의 바이트 prefix 해시를 기록했다. 압축을 다시 풀어 전체 해시가 일치함을 확인했고, 보고서가 참조한 로그 스냅샷이 보관본의 동일 prefix임도 확인했다.

## 파일 구분

`logs/market-kit.log`는 기존 커밋의 운영 기록을 그대로 유지한다. 이는 과제의 수정되지 않은 CI가 요구하는 경로다. 최신 전체 운영 로그는 이 폴더의 `market-kit.log.gz`에 있다. 원본이 약 345MB여서 일반 GitHub 단일 파일 제한을 초과하므로 전체를 약 26MB gzip으로 제출했다. 과거 로그를 최신인 것처럼 설명하거나 원본을 축약하지 않았다.

`memory.jsonl.gz`, `sales.jsonl.gz`, `talks.jsonl.gz`, `reviews-seen.json.gz`는 같은 보관 작업에서 읽은 원본이다. 앞의 세 파일은 보고서 집계 해시와 일치한다. gzip 사본은 Git 줄바꿈 변환과 무관하게 원래 바이트를 보존한다.

## 복원 예

저장소 루트에서 다음을 실행하면 최신 전체 운영 로그를 별도 파일로 복원한다. 실시간 기록 중인 `logs/market-kit.log`에 덮어쓰지 않는다.

```bash
python -c "import gzip,pathlib; p=pathlib.Path('submissions/26512071/week-07/logs/submission-evidence-20261027-151917'); (p/'market-kit.restored.log').write_bytes(gzip.decompress((p/'market-kit.log.gz').read_bytes()))"
```

집계 기준은 `logs/report-audit-20261027-150717/summary.json`이다. 운영이 이어지면 로컬 실시간 파일이 보관본보다 커질 수 있다.
