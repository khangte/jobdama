"""설정 파일(.env) 읽기."""

from app.config import Settings


def test_empty_env_var_does_not_hide_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("NOTION_TOKEN=from-file\n")
    monkeypatch.setenv("NOTION_TOKEN", "")
    assert Settings(_env_file=env).notion_token == "from-file"

