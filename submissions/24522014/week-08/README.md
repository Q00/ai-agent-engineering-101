# W07 중간 평가 재현 자료

보고서는 [REPORT.md](REPORT.md), 실험은 [compare_reputation.py](compare_reputation.py)다. 다섯 종류의 관측과 두 가게 이름 순서를 조합한 10개 입력에서 현재 가게 선택 함수를 비교한다. 실제 시장 거래나 모델 호출을 발생시키지 않는다.

## 실행

저장소를 받은 뒤 루트에서 Python 3로 다음 한 줄을 실행한다. 별도 패키지 설치나 인증은 필요 없다.

```powershell
python submissions/24522014/week-08/compare_reputation.py
```

최소 실행 구성은 아래 두 파일이며 상대 폴더 구조를 유지해야 한다.

```text
submissions/24522014/
  week-07/buyer_memory.py
  week-08/compare_reputation.py
```

출력 JSON을 `logs/reputation-comparison-20261102.json`과 비교한다. 비교할 때 파일의 BOM과 줄바꿈 대신 JSON 값 전체를 비교한다. 관측 시각 필드는 실행 시각이 아니라 실험에 고정한 입력 시각이다. 환경과 해시는 `logs/submission-validation-20261102T052153Z.json`에 있다. 기존 로그를 덮어쓰지 말고 새 실행 결과는 다른 파일에 저장한다.

## 제출 전 남은 확인

- 다른 Python 환경과 외부 패키지를 사용하지 않는 임시 폴더 실행에서 10행 일치를 확인했다. 다른 컴퓨터에서는 아직 실행하지 않았다.
- 현재 저장소에는 `scripts/check_week08.py`와 `weeks/week-08/README.md`가 없어 공식 W08 검사 통과를 주장하지 않는다. 현재 중간 평가 안내는 루트 `week-08.html`이다.
- 보고서의 배경은 W07 과제 명세에 근거한다. 중간 평가 안내는 읽기 목록의 원 논문·스펙과 실험의 대응도 요구하므로 해당 자료를 읽고 배경과 논의를 보완할 필요가 있다.
- 합성 실험은 선택 규칙의 동작 확인이며 실제 시장 인과관계나 모델 성능 측정으로 해석하지 않는다. 작성자는 조건·결과와 한계를 검토하고 자신의 설명과 일치하는지 확인해야 한다.
