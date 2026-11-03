"""사이트별 파서 분기. 등록 사이트가 아니면 범용 파서로 처리한다."""

from __future__ import annotations

from app.models import Posting
from app.collection.urls import CATCH, GENERIC, GREETING, INCRUIT, JASOSEOL, JOBKOREA, JUMPIT, SARAMIN, WANTED, NormalizedURL
from app.parsers.generic import parse_generic
from app.parsers.sites import saramin
from app.parsers.sites.catch import parse_catch
from app.parsers.sites.greeting import parse_greeting
from app.parsers.sites.incruit import parse_incruit
from app.parsers.sites.jasoseol import parse_jasoseol
from app.parsers.sites.jobkorea import parse_jobkorea
from app.parsers.sites.jumpit import parse_jumpit
from app.parsers.sites.wanted import parse_wanted


def parse_pages(target: NormalizedURL, pages: dict[str, str]) -> Posting:
    """pages: {'main': 공고 HTML, 'body': 본문 HTML(있으면)}"""
    main = pages.get("main") or ""
    body = pages.get("body")
    site = target.site
    if site is SARAMIN:
        return saramin.parse(main, target.url, target.ident)
    if site is JOBKOREA:
        return parse_jobkorea(main, body, target)
    if site is INCRUIT:
        return parse_incruit(main, body, target)
    if site is WANTED:
        return parse_wanted(main, target)
    if site is JUMPIT:
        return parse_jumpit(main, target)
    if site is JASOSEOL:
        return parse_jasoseol(main, target)
    if site is CATCH:
        return parse_catch(main, body, target)
    if site is GREETING or (site is GENERIC and "getOpeningById" in main):
        return parse_greeting(main, target)
    return parse_generic(main, target, body)
