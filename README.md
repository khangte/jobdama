# jobdama

채용공고 링크를 붙여넣으면 노션 "채용 지원 관리 DB"에 페이지를 만들어 주는 로컬 웹이다.
공고 선별, 직무 분류, 직무적합성 판정은 하지 않는다.

## 지원 사이트

| 사이트                | 받는 링크                                                             | 수집 방법                                                           | 상태      |
| --------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------- | --------- |
| 사람인                | `/zf_user/jobs/view?rec_idx=`, `/zf_user/jobs/relay/view?...rec_idx=` | 공고 페이지                                                         | 확인됨    |
| 잡코리아              | `/Recruit/GI_Read/{번호}`                                             | 공고 페이지 + 본문 페이지(`GI_Read_Comt_Ifrm?Gno=`)                 | 확인됨    |
| 원티드                | `/wd/{번호}`                                                          | 공고 페이지 내장 데이터                                             | 확인됨    |
| 인크루트              | `/jobdb_info/jobpost.asp?job={번호}`                                  | 공고 페이지 + 본문 페이지(`jobpostcont.asp`)                        | 확인됨    |
| 점핏                  | `/position/{번호}`                                                    | 공고 페이지                                                         | 확인됨    |
| 그리팅                | `{회사}.career.greetinghr.com/ko/o/{번호}`                            | 공고 페이지 내장 데이터                                             | 확인됨    |
| 자소설닷컴            | `/recruit/{번호}`                                                     | 공고 페이지 내장 데이터 + JSON-LD                                   | 확인됨    |
| 캐치                  | `/NCS/RecruitInfoDetails/{번호}`                                      | 공고 페이지 JSON-LD + 본문 페이지(`/controls/recruitDetail/{번호}`) | 확인됨    |
| 기업 자체 채용 사이트 | 위에 없는 모든 http(s) 주소                                           | 범용 파서(JSON-LD, 메타, 본문 제목) + Playwright                    | 일부 확인 |

- "확인됨"은 2026-11 실제 공고 HTML로 파서를 검증했다는 뜻이다(`tests/fixtures`).
- 자소설닷컴·캐치는 브라우저로 렌더링하면 추천 공고 정보가 섞여서 Playwright로 다시 열지 않는다(httpx가 실패할 때만 사용).
- 캐치·자소설닷컴은 본문이 이미지인 공고가 많아 "부분 저장"이 정상이다.
- 기업 자체 채용 사이트는 공고가 아닌 페이지를 저장하지 않도록, JSON-LD 공고 정보·본문 구간·
  주소나 제목의 채용 관련 단어(채용, 모집, 공고, recruit, career, job 등) 중 하나가 있어야 저장한다.
- 자체 채용 사이트는 localhost·사설 IP로 연결되는 주소를 요청하지 않는다.
- 봇 확인 화면(Cloudflare, Vercel 등)이 뜨면 수집 실패로 처리한다. 우회는 하지 않는다.

## 도커로 실행 (권장)

Docker Desktop(WSL 연동)이 켜져 있으면 컨테이너가 자동으로 다시 뜬다(`restart: unless-stopped`).
접속 주소는 **http://localhost:8765** 다.

```bash
cd ~/projects/jobdama
cp .env.example .env && nano .env      # 처음 한 번: NOTION_TOKEN, data source ID 입력
docker compose up -d --build           # 빌드 + 실행, 코드 업데이트 후에도 동일 (처음엔 Chromium 포함 몇 분 걸림)
docker compose logs -f                 # 로그 보기 (Ctrl+C로 빠져나옴)
docker compose stop                    # 끄기
```

진단 명령은 컨테이너 안에서 실행하고, 결과는 `./snapshots`에 생긴다.

```bash
docker compose exec jobdama python -m app.snapshot "공고 링크"
```

도커 없이 직접 실행하는 방법은 아래 2~3번을 따른다(포트 8000).

## 1. 노션 준비 (한 번만)

1. https://app.notion.com/developers/connections 에서 new connection을 만든다. Authentication method은 API token을 선택한다.
2. 발급된 토큰(`ntn_...`)을 복사한다.
3. 복사한 토큰을 `.env`에 `NOTION_TOKEN=` 값으로 붙여넣는다.
4. 노션에서 연결할 DB가 포함된 페이지를 열고, 오른쪽 위 `···` → 더보기 → 연결 → 연결 추가하기에서 방금 만든 connection을 추가한다.
   권한은 하위로 이어지므로 두 DB가 들어 있는 페이지 하나에만 추가하면 된다.
5. 채용 지원 관리 DB의 링크를 복사한다. 관심 기업 리스트 DB는 `기업명` relation이 가리키는 DB로 자동으로 찾는다.
6. 복사한 링크를 `.env`에 `JOB_DATA_SOURCE_ID=` 값으로 붙여넣는다.

## 2. 설치 (WSL, 한 번만)

```bash
# uv가 없으면 설치
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

cd jobdama
uv sync                                       # Python 3.12가 없으면 uv가 함께 받는다
uv run playwright install --with-deps chromium  # sudo 비밀번호를 물을 수 있다

cp .env.example .env
nano .env    # NOTION_TOKEN= 뒤에 토큰 붙여넣기
```

`.env`에 채용 지원 관리 DB의 링크 또는 ID(`JOB_DATA_SOURCE_ID`)도 적는다.
Playwright 설치를 건너뛰려면 `.env`에서 `USE_PLAYWRIGHT=false`로 둔다. 이 경우 httpx만 쓴다.

## 3. 실행

```bash
cd jobdama
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows 브라우저에서 `http://localhost:8000`을 연다. 종료는 터미널에서 `Ctrl+C`.

화면 상단에 빨간 안내가 보이면 노션 설정 문제다(토큰 없음, integration 미연결, 속성 이름 불일치).
고친 뒤 "다시 점검"을 누른다.

## 4. 사용

- 입력란에 링크를 붙여넣으면(Ctrl+V) 바로 접수되고 입력란이 비워진다. 연달아 붙여넣어도 된다.
- 직접 타이핑했다면 Enter.
- 여러 링크를 한 번에 복사해 붙여넣으면 각각 따로 접수된다.

결과 상태

| 상태      | 의미                                                                                         |
| --------- | -------------------------------------------------------------------------------------------- |
| 저장 완료 | 새 페이지 생성. 본문 구간도 발췌함                                                           |
| 부분 저장 | 제목은 확인했지만 본문 구간을 못 찾음(이미지형 공고 등). 페이지 상단에 미확인 안내           |
| 이미 있음 | 같은 공고 URL이 DB에 있어 저장하지 않음. 기존 페이지 링크 표시                               |
| 수집 실패 | 공고 제목을 확인할 수 없음(차단, 봇 확인 화면, 404, 다른 공고로 이동, 공고가 아닌 페이지 등) |
| 저장 실패 | 노션 조회·기업 생성·페이지 생성 실패                                                         |
| 입력 오류 | 지원 사이트의 공고 상세 링크가 아님(목록·검색 페이지 등), 내부 주소                          |

결과 목록은 서버 메모리에만 있어서 서버를 다시 켜면 비워진다. 저장된 노션 페이지는 그대로다.

## 5. 저장 내용

| 노션 속성       | 값                                                                                                   |
| --------------- | ---------------------------------------------------------------------------------------------------- |
| 공고명          | `[기업명] 공고명`. 공고명 앞에 같은 기업명이 있으면 지운다                                           |
| 기업명          | 관심 기업 리스트 DB에서 같은 기업을 찾아 연결. 없으면 기업명과 상태(⚪️ 리서치 중)만 넣어 새로 만든다 |
| 마감일          | 상시·채용시 마감이면 비움                                                                            |
| 경력            | 신입 / 신입/경력 / 경력무관 / 1년 이상 / 2년 이상 / 3년 이상. 확인 못 하면 비움                      |
| URL             | 사이트별 정규화 주소(지원 사이트 표 참고)                                                            |
| 지원 단계       | 📌 시작 전                                                                                           |
| 직무, 산업군 등 | 비움. 노션에서 직접 고른다                                                                           |

페이지 본문: 공고 요약(기업, 출처, 근무지, 경력 조건, 고용 형태, 마감일, 수집일), 주요 업무, 자격 요건, 우대 사항, 전형 절차, 원문 링크.
요약하지 않고 원문 구간을 발췌하며, 못 찾은 항목은 "공고에서 확인 불가"로 적는다.

## 6. 동작 규칙

- 처리 순서: URL 검증·정규화 → 같은 URL 조회 → 수집 → 필드 추출 → 기업 조회·생성 → 페이지 생성
- 같은 URL은 조회부터 생성까지 한 번에 하나씩 처리한다. 같은 링크를 연달아 붙여넣어도 페이지는 하나만 생긴다.
- 같은 URL 조회 시 예전에 다른 형식으로 저장된 URL(추적 파라미터가 붙은 주소, 사람인 `relay/view` 등)도 같은 공고 번호면 같은 공고로 본다.
  자체 채용 사이트는 정규화 주소가 정확히 같을 때만 같은 공고로 본다.
- 수집은 httpx로 먼저 한다. 실패하면 Playwright로 한 번 더 시도한다.
  자체 채용 사이트처럼 화면을 JS로 그릴 수 있는 곳은 본문이 비어도 Playwright로 다시 연다.
  채용 포털(사람인, 잡코리아, 자소설닷컴, 캐치 등)은 이미지형 공고라 본문이 비어도 다시 열지 않는다.
- httpx는 연결이 중간에 끊기면 한 번 더 요청한다.
- 리디렉션은 매 단계 같은 사이트 호스트인지(자체 사이트는 공개 IP인지) 확인하고, 요청한 공고 번호와 다른 공고가 열리면 수집 실패로 처리한다.
  httpx는 리디렉션을 따라가기 전에 확인하고, 막힌 주소면 Playwright로 다시 시도하지 않는다.
  Playwright는 첫 주소만 요청 전에 확인하고, 리디렉션 뒤 주소는 요청이 나간 뒤에 확인한다. 서비스 워커는 막는다.
- 노션 페이지 생성 응답이 끊기면(시간 초과, 연결 끊김, 5xx) 같은 URL을 다시 조회하고, 없을 때만 한 번 재시도한다.

## 7. 설정 (.env)

| 키                     | 기본값                                       | 설명                                                       |
| ---------------------- | -------------------------------------------- | ---------------------------------------------------------- |
| NOTION_TOKEN           | (없음)                                       | integration 토큰                                           |
| JOB_DATA_SOURCE_ID     | (없음)                                       | 채용 지원 관리 DB (관심 기업 리스트는 기업명 relation 대상) |
| USE_PLAYWRIGHT         | true                                         | Playwright 재시도 사용 여부                                |
| PROP_TITLE 등          | 공고명, 기업명, 마감일, 경력, URL, 지원 단계 | 노션 속성 이름이 바뀌면 여기서 맞춘다 (app/config.py 참고) |

## 8. 진단 (값이 이상한 공고)

노션에는 쓰지 않고, 수집한 HTML을 저장하고 파싱 결과만 출력한다.

```bash
uv run python -m app.snapshot "공고 링크1" "공고 링크2"
```

`snapshots/` 폴더에 `<사이트>_<공고번호>_httpx.html`, `..._playwright.html`(본문 별도 페이지가 있으면 `_body.html`)이 생긴다.

## 9. 테스트

```bash
uv run pytest
```

`tests/fixtures`의 실제 공고 HTML로 파서를 검증하고, 가짜 수집기와 가짜 노션으로 저장 흐름을 검증한다.

사이트 HTML 구조가 바뀌어 파싱이 깨지면, 해당 공고 HTML을 `tests/fixtures`에 추가하고 `app/parsers/sites/`의 그 사이트 파서만 고친다.

## 구조

```
app/
  main.py           FastAPI 라우트, 시작 시 노션 점검
  service.py        작업 접수, 처리 흐름, URL 잠금, 생성 재시도
  collection/
    urls.py         사이트 목록, URL 검증·정규화, 중복 조회 조건
    fetcher.py      httpx / Playwright 수집기, 봇 확인 화면·내부망 차단
    collector.py    수집 순서(httpx → Playwright, 본문 별도 페이지)와 파서 호출
  parsers/
    dispatch.py     사이트별 파서 분기(등록 사이트가 아니면 범용 파서)
    sites/          사이트별 파서(saramin, jobkorea, incruit, wanted, jumpit, greeting, jasoseol, catch)
    generic.py      범용 파서(JSON-LD, 메타, dt/dd, 본문 제목)
    common.py       경력 분류, 마감일, 공고명 형식, 값 변환
    sections.py     본문 구간 발췌(주요 업무, 자격 요건, 우대 사항, 전형 절차)
    html.py         HTML·JSON-LD 도우미(줄 추출, 메타값, dt/dd, JSON-LD 필드)
  snapshot.py       진단 명령(HTML 저장, 파싱 결과 출력)
  notion_store.py   노션 조회·생성, 페이지 본문 블록
  templates/, static/  화면 (htmx는 static에 포함)
tests/
  test_service.py, test_notion_store.py, test_snapshot.py, test_main.py
                    app/의 같은 이름 모듈 검증
  collection/       app/collection/ 검증(test_urls, test_fetcher)
  parsers/          app/parsers/ 검증(test_sites는 사이트별 파서와 분기, test_saramin, test_generic 등)
  helpers.py        fixture 읽기 도우미
  fixtures/         실제 공고 HTML
```
