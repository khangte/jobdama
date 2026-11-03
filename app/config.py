import errno
import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: 빈 환경변수(export NOTION_TOKEN=)가 .env 값을 가리지 않게
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    notion_token: str = ""
    job_data_source_id: str = ""
    company_data_source_id: str = ""

    # 채용 지원 관리 DB 속성 이름
    prop_title: str = "공고명"
    prop_company: str = "기업명"
    prop_deadline: str = "마감일"
    prop_career: str = "경력"
    prop_url: str = "URL"
    prop_stage: str = "지원 단계"
    stage_default: str = "📌 시작 전"

    # 관심 기업 리스트 DB 속성 이름
    company_prop_title: str = "기업명"
    company_prop_status: str = "상태"
    company_status_default: str = "⚪️ 리서치 중"

    use_playwright: bool = True
    http_timeout_sec: float = 15.0
    max_jobs_shown: int = 50


@lru_cache
def get_settings() -> Settings:
    return Settings()


ENV_PATH = Path(".env")


class EnvValueExists(Exception):
    pass


def filled_env_keys(keys, path: Path | None = None) -> list[str]:
    """keys 중 .env에 값이 이미 들어 있는 키(대문자). 키 대소문자와 export 접두어는 pydantic-settings처럼 가리지 않는다."""
    path = path or ENV_PATH
    wanted = {k.upper() for k in keys}
    values = dotenv_values(path) if path.is_file() else {}
    return sorted({k.upper() for k, v in values.items() if v and k.upper() in wanted})


def write_env(updates: dict[str, str], path: Path | None = None, overwrite: bool = False) -> None:
    """.env의 해당 키만 바꾸고 나머지 줄은 그대로 둔다. overwrite가 아니면 값이 이미 있는 키는 덮어쓰지 않는다.
    토큰이 들어가므로 소유자만 읽게(0600) 저장한다."""
    path = path or ENV_PATH
    filled = [] if overwrite else filled_env_keys(updates, path)
    if filled:
        raise EnvValueExists(", ".join(filled))
    targets = {k.upper(): v for k, v in updates.items()}
    pending = dict(targets)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out = []
    for line in lines:
        # pydantic-settings는 키 대소문자를 가리지 않고 export 접두어도 읽으므로 같게 맞춘다
        key = line.split("=", 1)[0].strip().removeprefix("export ").strip().upper()
        if key not in targets:
            out.append(line)
        elif key in pending:
            out.append(f"{key}={pending.pop(key)}")
        # 같은 키가 또 나오면 버린다: 뒤쪽 값이 새 값을 덮어쓰지 않게
    out += [f"{k}={v}" for k, v in pending.items()]
    data = ("\n".join(out) + "\n").encode("utf-8")

    def dump(f) -> None:
        # 내용을 먼저 쓰고 권한을 바꾼다: fchmod가 실패해도 파일이 빈 채로 남지 않게
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
        os.fchmod(f.fileno(), 0o600)  # 이전 실행에서 남은 tmp가 넓은 권한이었을 수 있음

    tmp = path.with_name(path.name + ".tmp")
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
            dump(f)
        try:
            os.replace(tmp, path)
        except OSError as e:
            if e.errno != errno.EBUSY:
                raise
            # shortcut: Docker 단일 파일 bind mount는 교체가 안 돼 제자리에 덮어쓴다(원자성 없음), 설정 폴더를 따로 마운트하면 제거
            with open(path, "wb") as f:
                dump(f)
    finally:
        tmp.unlink(missing_ok=True)
