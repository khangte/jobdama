"""수집 실행기: 가짜 수집기·가짜 노션으로 사이트 순회, 판정 기록, 결과 파일을 검증한다 (네트워크 없음)."""

import asyncio
import json

import pytest

from app.collect_runner import AlreadyRunning, CollectRunner, NotionNotReady, load_last
from app.collection.collect_config import CONFIG_PROBLEM, CollectConfig, save_config
from app.collection.fetcher import FetchError
from app.collection.listing import ListingSource
from app.collection.urls import JOBKOREA, SARAMIN
from app.service import JobService
from tests.helpers import load
from tests.test_service import FakeStore

SA_URL = "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=55434315"
JK_URL = "https://www.jobkorea.co.kr/Recruit/GI_Read/49874788"
JK_BODY_URL = "https://www.jobkorea.co.kr/Recruit/GI_Read_Comt_Ifrm?Gno=49874788"
SA_LIST, JK_LIST = "https://www.saramin.co.kr/list", "https://www.jobkorea.co.kr/list"
DETAILS = {SA_URL: load("saramin_55434315.html"), JK_URL: load("jobkorea_49874788.html"),
           JK_BODY_URL: load("jobkorea_49874788_body.html")}
SOURCES = (ListingSource(SARAMIN, (SA_LIST,)), ListingSource(JOBKOREA, (JK_LIST,)))


def saramin_list(*ids) -> str:
    """사람인 목록 모양의 인라인 HTML."""
    return "".join(f'<a href="/zf_user/jobs/relay/view?rec_idx={i}&x=y">공고</a>' for i in ids)


LISTS = {SA_LIST: saramin_list(55434315), JK_LIST: '<a href="/Recruit/GI_Read/49874788?logpath=1">공고</a>'}


class MapFetcher:
    """URL → 본문(또는 예외)으로 응답하는 가짜 수집기. 없는 URL은 HTTP 404."""

    name = "httpx"

    def __init__(self, mapping, delay=0.0):
        self.mapping, self.delay, self.urls = mapping, delay, []

    async def fetch(self, url, site):
        self.urls.append(url)
        await asyncio.sleep(self.delay)
        body = self.mapping.get(url)
        if body is None:
            raise FetchError("HTTP 404")
        if isinstance(body, Exception):
            raise body
        return body, url


def make(tmp_path, mapping=None, sources=SOURCES, screen=lambda p: (p, None), store=None, delay=0.0, cfg=None):
    """(runner, store, service). cfg가 있으면 설정 파일로 미리 저장해 둔다."""
    store = store or FakeStore()
    service = JobService(None, store, notion_ready=True)
    fetcher = MapFetcher({**DETAILS, **LISTS, **(mapping or {})}, delay)
    config_path = tmp_path / "collect_settings.json"
    if cfg:
        save_config(config_path, cfg)
    runner = CollectRunner(service, fetcher, tmp_path / "data" / "last.json", config_path, sources=sources, screen=screen)
    return runner, store, service


async def run_all(runner, dry_run=False):
    runner.start("screen", dry_run)
    await runner.wait()
    return runner.snapshot()


async def test_run_saves_and_persists(tmp_path):
    runner, _, service = make(tmp_path)
    run = await run_all(runner)
    assert [i["status"] for i in run["items"]] == ["저장 완료", "저장 완료"]
    saved = json.loads((tmp_path / "data" / "last.json").read_text(encoding="utf-8"))
    assert saved["sites"][0]["saved"] == 1 and saved["finished_at"] and saved["items"] == run["items"]
    assert not list((tmp_path / "data").glob("*.tmp")) and not service.jobs


async def test_skip_reason_recorded(tmp_path):
    runner, store, _ = make(tmp_path, screen=lambda p: (p, "근무지 제외: 부산"))
    run = await run_all(runner)
    assert all(i["group"] == "skipped" and i["message"] == "근무지 제외: 부산" for i in run["items"])
    assert store.create_calls == 0


async def test_dry_run_no_writes(tmp_path):
    runner, store, _ = make(tmp_path)
    run = await run_all(runner, dry_run=True)
    assert store.create_calls == 0 and store.company_creates == 0
    assert {i["status"] for i in run["items"]} == {"저장 대상"} and run["dry_run"] is True


async def test_listing_failure_isolated(tmp_path):
    runner, _, _ = make(tmp_path, {SA_LIST: FetchError("HTTP 403")})
    run = await run_all(runner)
    assert run["sites"][0]["error"] == "HTTP 403" and run["sites"][1]["saved"] == 1


async def test_second_page_failure_keeps_first_page(tmp_path):
    page2 = "https://www.jobkorea.co.kr/list?Page_No=2"
    runner, _, _ = make(tmp_path, {page2: FetchError("시간 초과")}, sources=(ListingSource(JOBKOREA, (JK_LIST, page2)),))
    run = await run_all(runner)
    assert run["sites"][0]["error"] == "시간 초과" and run["sites"][0]["saved"] == 1


async def test_empty_listing_is_site_error(tmp_path):
    runner, _, _ = make(tmp_path, {SA_LIST: "<html>목록 구조가 바뀜</html>"})
    run = await run_all(runner)
    assert run["sites"][0]["error"] == "공고 링크 0건" and run["sites"][0]["candidates"] == 0


async def test_block_stops_site(tmp_path):
    mapping = {SA_LIST: saramin_list(1, 2, 3)}
    for i in (1, 2, 3):
        mapping[f"https://www.saramin.co.kr/zf_user/jobs/view?rec_idx={i}"] = FetchError("봇 확인·차단 화면")
    runner, _, _ = make(tmp_path, mapping, sources=SOURCES[:1])
    run = await run_all(runner)
    assert len(run["items"]) == 1 and run["sites"][0]["error"].startswith("차단·연속 실패로 중단")


async def test_three_consecutive_failures_stop_site(tmp_path):
    ids = range(1, 6)
    mapping = {SA_LIST: saramin_list(*ids)}
    for i in ids:
        mapping[f"https://www.saramin.co.kr/zf_user/jobs/view?rec_idx={i}"] = FetchError("HTTP 500")
    runner, _, _ = make(tmp_path, mapping, sources=SOURCES[:1])
    run = await run_all(runner)
    assert len(run["items"]) == 3 and run["sites"][0]["candidates"] == 5 and run["sites"][0]["failed"] == 3


async def test_cross_site_duplicate_once(tmp_path):
    other = "https://www.saramin.co.kr/list2"
    sources = (ListingSource(SARAMIN, (SA_LIST,)), ListingSource(SARAMIN, (other,)))
    runner, _, _ = make(tmp_path, {other: saramin_list(55434315)}, sources=sources)
    run = await run_all(runner)
    assert len(run["items"]) == 1 and run["sites"][1]["candidates"] == 0


async def test_candidate_cap(tmp_path):
    runner, _, _ = make(tmp_path, {SA_LIST: saramin_list(*range(100, 125))}, sources=SOURCES[:1])
    run = await run_all(runner)
    assert run["sites"][0]["candidates"] == 20


async def test_notion_disconnected_mid_run_finishes(tmp_path):
    holder = {}

    def screen(p):
        holder["service"].notion_ready = False  # 첫 공고 처리 중 연결이 해제된 상황
        return p, None

    runner, _, service = make(tmp_path, screen=screen)
    holder["service"] = service
    run = await run_all(runner)
    assert [i["status"] for i in run["items"]] == ["저장 완료", "저장 실패"]
    assert run["finished_at"] and load_last(tmp_path / "data" / "last.json")["items"] == run["items"]


async def test_start_twice_raises(tmp_path):
    runner, _, service = make(tmp_path, delay=0.05)
    runner.start("screen", False)
    with pytest.raises(AlreadyRunning):
        runner.start("screen", False)
    await runner.aclose()
    service.notion_ready = False
    with pytest.raises(NotionNotReady):
        runner.start("screen", False)


def test_load_last_missing_or_corrupt(tmp_path):
    assert load_last(tmp_path / "none.json") is None
    for name, text in (("bad.json", "{"), ("list.json", "[]"), ("items.json", '{"items": []}'),
                       ("nostart.json", '{"items": [], "sites": []}'),
                       ("nosites.json", '{"items": [], "started_at": "x"}')):
        (tmp_path / name).write_text(text)
        assert load_last(tmp_path / name) is None


async def test_restart_reads_last(tmp_path):
    runner, _, service = make(tmp_path)
    run = await run_all(runner)
    again = CollectRunner(service, MapFetcher({}), tmp_path / "data" / "last.json", tmp_path / "c.json", sources=())
    assert again.snapshot()["items"] == run["items"]


async def test_cancel_keeps_previous_file(tmp_path):
    runner, _, _ = make(tmp_path, delay=0.05)
    path = tmp_path / "data" / "last.json"
    path.parent.mkdir()
    path.write_text('{"previous": true}')
    runner.start("screen", False)
    await asyncio.sleep(0.02)
    await runner.aclose()
    assert path.read_text() == '{"previous": true}' and not runner.running


async def test_runner_uses_saved_config(tmp_path):
    runner, _, _ = make(tmp_path, sources=None, cfg=CollectConfig(sites=["jumpit"]))
    await run_all(runner)
    assert runner._fetcher.urls and all(u.startswith("https://jumpit-api.saramin.co.kr/") for u in runner._fetcher.urls)


async def test_max_per_site_from_config(tmp_path):
    mapping = {SA_LIST: saramin_list(*range(100, 105))}
    runner, _, _ = make(tmp_path, mapping, sources=SOURCES[:1], cfg=CollectConfig(max_per_site=2))
    run = await run_all(runner)
    assert run["sites"][0]["candidates"] == 2 and len(run["items"]) == 2


async def test_update_during_run_applies_next(tmp_path):
    runner, _, _ = make(tmp_path, sources=None, delay=0.01, cfg=CollectConfig(sites=["saramin"]))
    runner.start("screen", True)
    runner.update_config(CollectConfig(sites=["incruit"]))
    await runner.wait()
    assert runner._fetcher.urls and all("saramin.co.kr" in u for u in runner._fetcher.urls)
    runner._fetcher.urls.clear()
    await run_all(runner, True)
    assert runner._fetcher.urls and all("incruit.com" in u for u in runner._fetcher.urls)


def test_update_config_write_failure_keeps_old(tmp_path, monkeypatch):
    runner, _, _ = make(tmp_path)
    old = runner.config

    def boom(path, cfg):
        raise OSError("disk full")

    monkeypatch.setattr("app.collect_runner.save_config", boom)
    with pytest.raises(OSError):
        runner.update_config(CollectConfig(max_per_site=5))
    assert runner.config is old


def test_corrupt_config_problem(tmp_path):
    (tmp_path / "collect_settings.json").write_text("{broken", encoding="utf-8")
    runner, _, _ = make(tmp_path)
    assert runner.config_problem == CONFIG_PROBLEM and runner.config == CollectConfig()
    runner.update_config(CollectConfig())
    assert runner.config_problem is None
    assert json.loads((tmp_path / "collect_settings.json").read_text(encoding="utf-8"))["keyword"] == "python"


async def test_items_carry_company(tmp_path):
    """결과 항목이 확정 회사명을 싣는다(화면 마크용)."""
    runner, _, service = make(tmp_path)
    run = await run_all(runner)
    assert all("company" in i for i in run["items"]) and any(i["company"] for i in run["items"])
