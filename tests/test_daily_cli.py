from __future__ import annotations

import json
from pathlib import Path

import daily_asin_list
import daily_report


def test_daily_asin_list_uses_bind_key_from_env(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("HERMES_BINDING_KEY", "env-demo")
    monkeypatch.setattr("sys.argv", ["daily_asin_list.py", "--action", "add", "--asin", "B0CDX5XGLK"])
    monkeypatch.setattr(daily_asin_list, "load_app_config", lambda: type("Config", (), {"base_dir": tmp_path})())

    exit_code = daily_asin_list.main()

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["bindKey"] == "env-demo"
    assert payload["items"] == ["B0CDX5XGLK"]


def test_daily_asin_list_errors_without_bind_key(monkeypatch, capsys):
    monkeypatch.delenv("HERMES_BINDING_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["daily_asin_list.py", "--action", "list"])

    exit_code = daily_asin_list.main()

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert "bindKey is required" in payload["message"]


def test_daily_report_uses_bind_key_from_env(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("HERMES_BINDING_KEY", "env-demo")
    monkeypatch.setattr("sys.argv", ["daily_report.py", "--date", "2026-06-02"])
    monkeypatch.setattr(daily_report, "load_app_config", lambda: type("Config", (), {"base_dir": tmp_path})())

    bindings_path = tmp_path / "config" / "daily_bindings.json"
    bindings_path.parent.mkdir(parents=True, exist_ok=True)
    bindings_path.write_text(
        json.dumps({"bindings": {"env-demo": ["B0CDX5XGLK"]}}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(daily_report, "run_cli", lambda urls, mode="both": {"results": [{"asin": urls[0]}]})
    monkeypatch.setattr(daily_report, "export_daily_report_csv", lambda results, output_path: str(output_path))

    exit_code = daily_report.main()

    assert exit_code == 0
    output_path = capsys.readouterr().out.strip()
    assert output_path.endswith("/runtime_data/env-demo/2026-06-02-1.csv")


def test_daily_report_errors_without_bind_key(monkeypatch, capsys):
    monkeypatch.delenv("HERMES_BINDING_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["daily_report.py"])

    exit_code = daily_report.main()

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "error"
    assert "bindKey is required" in payload["message"]
