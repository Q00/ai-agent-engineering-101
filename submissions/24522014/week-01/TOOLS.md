# TOOLS.md

## fetch

> "Fetch a public web page or API response over http/https and return the first few thousand characters of its text. Local, private, and non-http URLs are rejected."

모델은 `fetch`의 구현을 볼 수 없고 이 한 줄만 보고 호출 여부와 인자를 정하기 때문에, 설명에는 코드를 읽어야만 알 수 있는 세 가지를 명시적으로 담았다. 첫째, **무엇을 할 수 있는지**를 "public web page or API response"로 좁혀서, 브라우저처럼 로그인·클릭·폼 제출을 대신해 줄 것이라는 과대 기대를 미리 차단했다. 둘째, **무엇을 돌려주는지**를 "first few thousand characters of its text"로 밝혔다. 반환값이 잘린다는 사실을 모르면 모델은 짧은 응답을 페이지 전체로 착각하고 "이 사이트에는 X가 없다"는 식의 잘못된 결론을 내리며, 이미지·PDF 같은 바이너리를 기대하고 호출하는 낭비도 생긴다. 셋째, **거부 조건**을 "Local, private, and non-http URLs are rejected"로 앞세웠다. 이 툴의 방어 로직(스킴 검사, 사설·루프백 IP 차단, 포트 화이트리스트, 리다이렉트 재검증)은 모두 `denied:` 문자열로 되돌아오는데, 모델이 그 경계를 미리 알면 `file://`이나 `localhost`를 시도해 스텝을 버리는 대신 처음부터 유효한 URL만 넘기고, 거부 메시지를 받았을 때도 무작정 재시도하지 않고 사용자에게 이유를 설명할 수 있다. 요컨대 설명문은 요약이 아니라 계약이며, 능력·반환 형태·실패 조건을 한 문장 안에 담는 것이 툴 인터페이스 설계의 핵심이다.
