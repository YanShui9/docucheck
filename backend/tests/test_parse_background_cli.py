"""Parser daemon logs only safe status when its database is unavailable."""

import json

from backend.app import parse_background


def test_once_db_failure_is_sanitized(monkeypatch, capsys, tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError("private database detail")

    monkeypatch.setattr(parse_background, "process_once", fail)
    code = parse_background.main(["--once", "--data-dir", str(tmp_path)],
                                 engine=object())
    output = capsys.readouterr()
    assert code == 2
    assert json.loads(output.out) == {"error": "parse_worker_unavailable"}
    assert "private database detail" not in output.out + output.err
