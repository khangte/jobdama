"""설정 파일(.env) 읽기·쓰기."""

import errno
import os

import pytest

from app.config import EnvValueExists, Settings, write_env


def test_empty_env_var_does_not_hide_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("NOTION_TOKEN=from-file\n")
    monkeypatch.setenv("NOTION_TOKEN", "")
    assert Settings(_env_file=env).notion_token == "from-file"


def test_write_env_matches_keys_like_settings(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# 주석\nnotion_token=\nexport JOB_DATA_SOURCE_ID=\nNOTION_TOKEN=\nUSE_PLAYWRIGHT=false\n")
    env.chmod(0o644)
    write_env({"notion_token": "new", "job_data_source_id": "j"}, env)
    assert env.read_text() == "# 주석\nNOTION_TOKEN=new\nJOB_DATA_SOURCE_ID=j\nUSE_PLAYWRIGHT=false\n"
    assert os.stat(env).st_mode & 0o777 == 0o600
    assert not (tmp_path / ".env.tmp").exists()
    assert Settings(_env_file=env).notion_token == "new"


def test_write_env_keeps_existing_values(tmp_path):
    env = tmp_path / ".env"
    original = "export notion_token=mine\nJOB_DATA_SOURCE_ID=\n"
    env.write_text(original)
    with pytest.raises(EnvValueExists, match="NOTION_TOKEN"):
        write_env({"notion_token": "new", "job_data_source_id": "j"}, env)
    assert env.read_text() == original


def test_write_env_in_place_when_replace_busy(tmp_path, monkeypatch):
    # Docker 단일 파일 bind mount: os.replace가 EBUSY로 실패하면 제자리에 쓴다
    env = tmp_path / ".env"
    env.write_text("NOTION_TOKEN=\nUSE_PLAYWRIGHT=true\n")
    env.chmod(0o644)

    def busy(*_):
        raise OSError(errno.EBUSY, "busy")

    monkeypatch.setattr(os, "replace", busy)
    write_env({"notion_token": "t"}, env)
    assert env.read_text() == "NOTION_TOKEN=t\nUSE_PLAYWRIGHT=true\n"
    assert os.stat(env).st_mode & 0o777 == 0o600
    assert not (tmp_path / ".env.tmp").exists()
