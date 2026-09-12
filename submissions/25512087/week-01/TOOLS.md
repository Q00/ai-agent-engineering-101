# Tool Description

## fetch_url

처음엔 단순히 웹에 있는 컨텐츠를 받아 사용자에게 리턴하는 형태의 tool 구현을 목표로 http, https 의 정보를 받아 작성하게끔 description을 작성했습니다.

함수 작성 과정에서 SSL 인증 문제로 example.com 의 웹 정보를 불러오지 못함을 확인하고, 이를 수정하기 위해 certifi 사용해서 SSL 인증 문제를 해결했습니다.

테스트로 서울 날씨를 알려달라고 요청했는데, 날씨 쿼리를 어떻게 처리하라는 점이 description에 분명하게 나타나지 않아 tool calling에 실패한 것을 확인했고(logs/weather_1.txt), 로그 확인 결과 다양한 사이트를 조회하는 것을 알게 되어, 별도의 API Key 없이 불러올 수 있는 open-meteo를 사용하도록 명시해 날씨 관련 쿼리를 보다 확실하게 처리하도록 작성했습니다.

이후 test(logs/weather_2.txt, weather3.txt) 에서 open-meteo를 사용하는 것을 확인했습니다.

날씨 외의 정보를 처리하는 쿼리를 테스트하자, api Key가 필요한 url에 접근해 정보를 가져오지 못하는 로그를 확인했습니다.

이를 기반으로 별도의 API key 없이 웹 정보를 가져오거나, 답변을 어떠한 형태로 제공하기 위해서 우선적으로 탐색할 url을 작성하거나, 이를 처리할 방법이 필요하다는 것을 알게 되었습니다.