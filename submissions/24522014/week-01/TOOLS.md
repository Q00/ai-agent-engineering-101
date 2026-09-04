# TOOLS.md

## fetch

> "Fetch a public web page or API response over http/https and return the first few thousand characters of its text. Local, private, and non-http URLs are rejected."

모델은 `fetch`의 구현을 볼 수 없고 이 한 줄만 보고 호출 여부와 인자를 정하기 때문에, 설명에는 코드를 읽어야만 알 수 있는 세 가지를 명시적으로 담았다. 첫째, **무엇을 할 수 있는지**를 "public web page or API response"로 좁혀서, 브라우저처럼 로그인·클릭·폼 제출을 대신해 줄 것이라는 과대 기대를 미리 차단했다. 둘째, **무엇을 돌려주는지**를 "first few thousand characters of its text"로 밝혔다. 반환값이 잘린다는 사실을 모르면 모델은 짧은 응답을 페이지 전체로 착각하고 "이 사이트에는 X가 없다"는 식의 잘못된 결론을 내리며, 이미지·PDF 같은 바이너리를 기대하고 호출하는 낭비도 생긴다. 셋째, **거부 조건**을 "Local, private, and non-http URLs are rejected"로 앞세웠다. 이 툴의 방어 로직(스킴 검사, 사설·루프백 IP 차단, 포트 화이트리스트, 리다이렉트 재검증)은 모두 `denied:` 문자열로 되돌아오는데, 모델이 그 경계를 미리 알면 `file://`이나 `localhost`를 시도해 스텝을 버리는 대신 처음부터 유효한 URL만 넘기고, 거부 메시지를 받았을 때도 무작정 재시도하지 않고 사용자에게 이유를 설명할 수 있다. 요컨대 설명문은 요약이 아니라 계약이며, 능력·반환 형태·실패 조건을 한 문장 안에 담는 것이 툴 인터페이스 설계의 핵심이다.

## clock

> "Get the current date, time, weekday, and UTC offset. Defaults to Asia/Seoul if no timezone is given. Call this instead of guessing today's date."

`clock`의 설명에서 가장 중요한 문장은 마지막 한 줄, **"Call this instead of guessing today's date"** 다. 모델은 학습 데이터에 박힌 시점을 현재로 착각한 채 툴을 부르지 않고 날짜를 답해버리는 경향이 강한데, 이 툴이 존재한다는 사실만으로는 그 습관이 교정되지 않기 때문에 호출해야 하는 상황을 명령형으로 못박았다. 반환 항목을 "date, time, weekday, and UTC offset"으로 일일이 나열한 것도 같은 이유다. 모델이 "요일"이나 "시차"를 이 툴로 얻을 수 있다고 인식하지 못하면 시각만 받아놓고 요일은 스스로 계산하려 들거나, 두 지역의 시차를 물었을 때 툴을 두 번 부르는 대신 암기한 오프셋을 꺼내 쓴다 — 서머타임이 걸리면 그대로 틀린다. 기본값을 "Defaults to Asia/Seoul"로 명시한 것은 인자를 선택형으로 두면서 생기는 모호함을 없애기 위해서다. 사용자가 지역을 언급하지 않았을 때 모델이 인자를 비워도 되는지 확신하지 못하면 불필요하게 되묻거나 임의의 타임존을 지어내는데, 기본 동작을 미리 알려주면 "지금 몇 시야?" 같은 질문에 곧장 인자 없이 호출한다. 즉 `fetch`의 설명이 *하지 말아야 할 것*(로컬·비 http 주소)을 그었다면, `clock`의 설명은 *반드시 해야 할 것*(추측 대신 호출)을 지시한다. 툴 설명은 기능 명세인 동시에 모델의 기본 행동을 교정하는 지점이라는 뜻이다.

## write_note

> "Save a short text note to a file in the working directory. Appends by default; set append=false to replace the file's contents, which cannot be undone. Only .txt, .md, .log, .csv, .json filenames are accepted, and only inside the working directory."

`write_note`는 지금까지의 툴 중 유일하게 **부작용이 남는** 툴이고, 설명문의 무게중심도 거기에 있다. `calculator`나 `clock`은 잘못 불러도 스텝 하나를 버릴 뿐이지만 잘못된 쓰기는 사용자의 파일을 지운다. 그래서 "Appends by default"로 안전한 쪽이 기본값임을 먼저 알리고, 덮어쓰기는 `append=false`라는 명시적 행동으로만 도달하게 한 다음, 거기에 "which cannot be undone"이라는 결과까지 붙였다 — 모델은 인자의 존재만 보고는 그 인자가 파괴적인지 알 수 없으므로, 되돌릴 수 없다는 사실을 설명문에 적어야 비로소 덮어쓰기 전에 사용자에게 확인을 구하는 행동이 나온다. 허용 확장자와 작업 디렉터리 제한을 나열한 것은 `fetch`에서 거부 조건을 앞세운 것과 같은 의도로, 모델이 `.py`나 상위 경로를 시도해 스텝을 낭비하는 대신 처음부터 유효한 인자를 넘기게 하려는 것이다. 반대로 "a few thousand characters at most"를 `content` 인자 설명에 둔 이유는 이게 금지가 아니라 **용도의 신호**이기 때문이다. 이 툴은 메모용이지 보고서 저장용이 아니며, 모델이 장문을 한 번에 밀어 넣으려다 거부당하는 대신 애초에 요약해서 넘기도록 유도한다. 정리하면 파괴적 인자에는 결과를, 경계에는 조건을, 크기에는 의도를 적는 것이 부작용 있는 툴의 설명 원칙이다.
