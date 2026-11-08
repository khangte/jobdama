# 자동 수집 화면 로컬 핸드오프

Task 9의 화면 결정 사항이다. Task 10 구현은 이 문서와 `docs/superpowers/plans/2026-11-08-daily-collector-web.md`의 Phase 3을 따른다. Penpot 보드·컴포넌트 식별자는 [구성 기록](penpot-organization.json)에 있다.

## 확인할 시안

- 상태 전환 시안: [collect-screen.html](collect-screen.html) — `?state=empty`, `?state=running`, `?state=done`, `?state=disconnected`
- 완료 상태 캡처: [1440px 데스크톱](collect-desktop.png), [390px 모바일](collect-mobile.png)
- Penpot 보드 이름과 화면 대응: [README의 자동 수집 화면 표](README.md#자동-수집-화면)

로컬 시안은 실제 API를 호출하지 않는다. 캡처의 공고와 집계 숫자는 레이아웃을 확인하기 위한 예시다. 실행 중 화면도 실제로는 `CollectRunner.snapshot()`의 현재 값을 사용한다.

## 화면 구조

1. 메인과 같은 상단 바에 `공고 담기`(`/`)와 `자동 수집`(`/collect`) 링크를 둔다. 현재 링크는 `aria-current="page"`다. 모바일은 브랜드와 Notion 상태를 첫 줄, 메뉴 링크를 둘째 줄에 둔다.
2. 제목 `자동 수집`, 부제 `매일 정해진 사이트에서 조건에 맞는 공고만 골라 Notion에 담아요.` 다음에 상태 → 실행 → 사이트별 집계 → 공고별 결과 → 선별 조건 순서로 배치한다.
3. 기록이 없으면 상태와 공고별 결과 영역에 `아직 실행 기록이 없어요.` 및 `수집 실행을 누르거나 스케줄을 등록하세요.`를 표시한다. 집계는 숨긴다.
4. 실행 중에는 `수집 중이에요`를 표시하고 `수집 실행` 버튼을 비활성화한다. 처리 중 결과 카드는 메인의 결과 카드 구조를 재사용한다.
5. 완료 상태에는 마지막 실행 시각, 실행 방식(`화면`/`스케줄`), 미리보기 여부를 표시한다. 사이트별 집계 뒤에 결과 필터와 공고 카드를 둔다. 사이트 오류는 해당 행 아래 원문 한 줄로 보여준다. 제외 카드는 `message`를 숨기지 않고 노출한다.
6. Notion 미연결 상태에서는 `Notion을 연결해야 수집할 수 있어요.`와 `메인에서 연결하기`(`/`)를 보여주고 실행 버튼을 비활성화한다. 수집 화면에서 연결·해제 모달은 열지 않는다.

## 데이터와 상태 대응

| 화면 요소 | 서버 값 |
| --- | --- |
| 실행 중 | `app.state.collect.running` |
| 현재/마지막 실행 | `app.state.collect.snapshot()`; 없으면 기록 없음 |
| 실행 시각·방식·미리보기 | `run.started_at`, `run.trigger`, `run.dry_run` |
| 사이트 집계 | `run.sites[]`: `name`, `candidates`, `saved`, `exists`, `skipped`, `failed`, `error` |
| 공고 카드 | `run.items[]`: `title`, `status`, `css`, `group`, `message`, `site`, `notion_url`, `url` |
| 필터 개수 | `run.items[]`의 `group` 값으로 계산 |

- 필터는 `?status=all|saved|exists|skipped|failed`로 서버에서 적용한다. 잘못된 값은 `all`로 처리한다. 선택 탭에는 `aria-current="page"` 또는 동등한 현재 상태 표시를 준다.
- 미리보기 체크박스 문구는 `미리보기 (Notion에 저장하지 않음)`이다. 제출 성공 시 갱신, 409는 `이미 수집 중이에요.`, 503은 `Notion이 연결되지 않았어요.`를 `role="alert"`에 표시한다.
- `저장 안 함`(`skipped`) 배지는 기존 `--info`/`--info-bg`, `저장 대상`(`preview`) 배지는 기존 `--blue`/`--run-bg`를 사용한다. 새 색 토큰은 필요 없다.
- 데스크톱 집계는 `사이트 · 후보 · 저장 · 이미 있음 · 제외 · 실패` 표다. 390px 이하에서는 사이트별 카드 목록으로 바꾸고 가로 스크롤을 만들지 않는다. 카드에도 같은 여섯 값과 사이트 오류를 보여준다.
- 결과 카드의 제목, 메시지, 출처, 원문/Notion 링크 구조는 메인의 `_jobs.html`을 따른다. 상태값은 수집 결과의 `status`·`css`를 그대로 사용한다.
- 선별 조건은 `근무지: 서울·경기`, `경력: 신입~2년(3년 이상 제외)`, 계획서의 공고명 제외 단어와 본문 필수 신호 목록을 전부 표시한다. 끝 문구는 `조건은 app/collection/filters.py에서 바꿀 수 있어요.`다.

## 로컬 검증

Chromium에서 1440px의 네 상태와 390px의 기록 없음·실행 중·완료, 320px의 완료 화면을 렌더링했다. 모두 문서 가로 넘침이 없었고 두 메뉴와 Notion 상태가 보였다. 실행 중·미연결 상태의 실행 버튼은 비활성, 기록 없음·완료 상태는 활성으로 확인했다. 390px·320px 완료 화면의 집계는 표 대신 카드로 표시된다.

Penpot `jobdama` 파일에 데스크톱 4개·모바일 3개의 편집 가능한 보드, 결과 카드 Variant 4개, 신규 부품 Variant 4개를 반영했다. 메인과 수집 화면의 이동, 미연결 화면의 메인 연결 이동을 연결했다. 새 보드 경계·텍스트 경계·Variant·이동 대상 검사에서 오류 0개를 확인하고 `task9/collect-screen-final` 체크포인트를 저장했다. 전체 파일 내보내기 검사는 MCP의 `No matching clause` 오류로 수행되지 않았다.
