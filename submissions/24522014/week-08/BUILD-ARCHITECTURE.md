# 아키텍처 그림 빌드

week-09 보완 후 SKU 가치·정산 가격 비교와 미판매 재고·보관비·게시판 요청 경로를 반영했다. 최신 빌드는 `logs/week09-architecture-build-20261103.log`, 검사는 `logs/week09-figure-audit-20261103T111900516560Z.json`이다. `audit_week09.py`로 한 페이지와 페이지 밖 텍스트 없음, 소스/PDF 해시를 확인했고 그림 미리보기를 검토했다. 최신 전체 오프라인 검사는 90개 통과다. 아래 79개 및 이전 해시는 당시 버전의 기록이다.

11월 3일 갱신: 날짜·검사 수를 현재 상태(79개 통과)로 바꿔 PDF를 다시 빌드했다. 로그는 `logs/architecture-build-20261103.log`, 최신 소스·PDF 해시와 페이지 검사는 `logs/preopen-submission-audit-20261103.json`이다. 아래 11월 2일 검증 기록은 이전 버전의 이력이다.

`architecture.tex`는 TikZ로 작성한 한 페이지 그림 원본이며 `architecture.pdf`가 제출용 결과다. 그림은 영어로 표기하고 한국어 설명은 REPORT.md의 2절에 둔다. 제공된 키트, 수업 seller를 수정한 부분, 자체 제약·메모리·툴을 구분한다. 실제 R3 성과를 입증하는 자료로 해석하지 않는다.

Tectonic 0.17.0 공식 Windows 배포판으로 빌드했다. 다운로드한 도구와 미리보기 라이브러리는 Git 제외 tools/에만 두었다. [공식 릴리스](https://github.com/tectonic-typesetting/tectonic/releases/tag/tectonic%400.17.0)를 참고한다. Tectonic이 설치된 환경에서 다음을 실행한다.

```powershell
tectonic architecture.tex
```

이번 로컬 실행에서는 tools/tectonic/tectonic.exe를 사용하고 tools/architecture-build로 출력한 뒤 PDF를 제출 경로로 복사했다. 첫 빌드는 페이지 오른쪽 라벨 폭 경고가 있어 수정했다. logs/architecture-build-20261102-first.log와 logs/architecture-build-20261102-final.log에 각 출력이 있다. 최종 빌드의 Fontconfig 설정 진단은 남아 있지만 PDF 생성은 성공했고, 미리보기에서 글자를 확인했다.

최종 검사는 한 페이지, 페이지 밖 텍스트 없음, 그림 미리보기 확인이다. 소스·PDF 해시와 엔진 정보는 logs/architecture-validation-20261102.json에 있다. 원본을 수정하면 PDF도 다시 빌드해야 한다. 그림의 모델 호출·정산 기억·공급·후기 흐름을 실제 코드와 비교해 검토하고, 구현이나 적용 상태가 달라지면 설명도 갱신한다.
