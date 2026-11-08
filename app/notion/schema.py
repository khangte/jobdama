"""노션 DB 스키마에서 값을 넣을 열을 찾는 규칙."""

from __future__ import annotations

from typing import NamedTuple


class Role(NamedTuple):
    """노션 DB에서 값을 넣을 열을 찾는 규칙. 스키마에는 이름·형식·옵션만 있어 용도는 이름으로 짐작한다."""
    label: str
    types: tuple[str, ...]  # 받을 수 있는 노션 형식, 앞일수록 우선
    keywords: tuple[str, ...] = ()  # 이름(소문자, 공백 제거)에 들어 있으면 후보
    required: bool = False  # 못 찾으면 저장을 막는다. 아니면 비우고 경고만
    by_type: bool = True  # 첫 형식인 열이 하나뿐이면 이름을 보지 않고 쓴다


JOB_ROLES = {
    "title": Role("공고명", ("title",), required=True),
    "company": Role("기업", ("relation", "rich_text", "select"), ("기업", "회사", "company"), required=True),
    "url": Role("URL", ("url", "rich_text"), ("url", "링크", "link"), required=True),
    # 날짜·선택 열은 여러 용도로 쓰여(지원일, 직무 등) 하나뿐이어도 이름이 맞아야 쓴다
    "deadline": Role("마감일", ("date", "rich_text"), ("마감", "deadline", "due"), by_type=False),
    "career": Role("경력", ("select", "multi_select", "rich_text"), ("경력", "career", "연차", "experience"), by_type=False),
    "stage": Role("지원 단계", ("status", "select"), ("단계", "상태", "stage", "status")),
}
COMPANY_ROLES = {
    "title": Role("기업명", ("title",), required=True),
    "status": Role("상태", ("status", "select"), ("상태", "단계", "status", "stage"), by_type=False),
}
TYPE_NAMES = {"title": "제목", "rich_text": "텍스트", "url": "URL", "date": "날짜", "select": "선택",
              "multi_select": "다중 선택", "status": "상태", "relation": "관계형"}

Found = dict[str, tuple[str, dict]]  # 역할 -> (열 이름, 스키마의 속성 정보)


def _pick(props: dict, role: Role) -> tuple[tuple[str, dict] | None, str]:
    """역할에 맞는 열 하나와, 못 찾았으면 그 이유."""
    cands = [(n, p) for n, p in props.items() if p.get("type") in role.types]
    if role.by_type:
        first = [c for c in cands if c[1]["type"] == role.types[0]]
        if len(first) == 1:
            return first[0], ""
    hits = [c for c in cands if any(k in c[0].lower().replace(" ", "") for k in role.keywords)]
    for t in role.types:  # 이름이 맞는 열이 여러 개면 앞선 형식만 남긴다
        if same := [c for c in hits if c[1]["type"] == t]:
            hits = same
            break
    if len(hits) == 1:
        return hits[0], ""
    if hits:
        return None, f"후보가 여러 개({', '.join(n for n, _ in hits)})"
    types = "/".join(TYPE_NAMES[t] for t in role.types)
    if role.keywords:
        return None, f"이름에 {'·'.join(role.keywords)} 중 하나가 들어간 {types} 열이 없음"
    return None, f"{types} 열이 없음"


def resolve_props(props: dict, roles: dict[str, Role]) -> tuple[Found, list[str], list[str]]:
    """스키마의 열을 역할에 짝짓는다. (찾은 열, 저장을 막는 문제, 비워 두는 경고)"""
    found: Found = {}
    problems: list[str] = []
    warnings: list[str] = []
    for key, role in roles.items():
        hit, why = _pick({n: p for n, p in props.items() if n not in {f[0] for f in found.values()}}, role)
        if hit:
            found[key] = hit
        elif role.required:
            problems.append(f"{role.label} 열을 정하지 못함: {why}")
        else:
            warnings.append(f"{role.label} 열을 정하지 못해 비워 둠: {why}")
    return found, problems, warnings


def pick_option(prop: dict, preferred: str) -> str | None:
    """선택·상태 열에 넣을 옵션. 원하는 이름이 없으면 상태는 첫 그룹(할 일)의 첫 옵션, 선택은 첫 옵션."""
    conf = prop.get(prop["type"]) or {}
    names = {o.get("id"): o["name"] for o in conf.get("options", [])}
    if preferred in names.values():
        return preferred
    for group in conf.get("groups", []):
        for oid in group.get("option_ids", []):
            if oid in names:
                return names[oid]
    return next(iter(names.values()), None)
