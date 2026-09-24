"""Published files: same answers as parsing raw snapshots, readable locally or from GitHub."""
import json
import sys
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    import make_demo_data
    root = tmp_path_factory.mktemp("demo")
    snap = make_demo_data.main(root)
    return root, snap.name


def test_publish_layout_and_no_warnings(demo):
    from rr import published
    root, sid = demo
    idx = json.loads((root / "published" / "index.json").read_text())
    assert [s["id"] for s in idx["snapshots"]] == [sid]
    meta = json.loads((root / "published" / sid / "meta.json").read_text())
    assert meta["warnings"] == [] and len(meta["tables"]) > 10
    assert "results" not in meta["manifest"]
    assert not any(p.name.startswith(".tmp") for p in (root / "published").iterdir())


def test_published_matches_raw_parsing(demo):
    from rr import sources, views
    root, sid = demo
    raw, pub = sources.LocalSource(root), sources.PublishedSource(sources.FileReader(root))
    assert pub.available(sid) == raw.available(sid)
    teams = tuple(raw.available(sid)["depth-charts"])
    tbl = "query:depthChart.state.data"
    pd.testing.assert_frame_equal(raw.frame(sid, "depth-charts", teams, tbl),
                                  pub.frame(sid, "depth-charts", teams, tbl), check_dtype=False)
    sub = ("cubs", "yankees")
    pd.testing.assert_frame_equal(raw.frame(sid, "depth-charts", sub, tbl),
                                  pub.frame(sid, "depth-charts", sub, tbl), check_dtype=False)
    pd.testing.assert_frame_equal(raw.coverage(sid, "depth-charts"), pub.coverage(sid, "depth-charts"),
                                  check_dtype=False)
    for page in ("injury-report", "breakdowns/coaches"):
        a, b = raw.tables_for(sid, page, None), pub.tables_for(sid, page, None)
        assert a.keys() == b.keys()
        for k in a:
            pd.testing.assert_frame_equal(a[k].reset_index(drop=True), b[k], check_dtype=False)

    ri, pi = raw.index(sid), pub.index(sid)
    assert len(ri.players) == len(pi.players)
    key = ri.search("Ashby").iloc[0]["key"]
    assert [(p, t) for p, t, _ in ri.profile(key)] == [(p, t) for p, t, _ in pi.profile(key)]
    spec = {"fields": [{"page": "depth-charts", "table": tbl, "column": "Pos", "agg": "first", "label": "Pos"},
                       {"page": "payroll", "table": "query:payroll.state.data", "column": "Salary2026",
                        "agg": "sum", "label": "Salary"},
                       {"page": "injury-report", "table": "query:injuryReport.state.data", "column": "Injury",
                        "agg": "all", "label": "Injury"}], "rows_from": 2, "teams": ["*"]}
    pd.testing.assert_frame_equal(ri.combine(spec), pi.combine(spec), check_dtype=False)
    pd.testing.assert_frame_equal(ri.match_report(), pi.match_report(), check_dtype=False)

    for src in (raw, pub):
        av = src.available(sid)
        ctx = views.Ctx(lambda p, t, s=src: s.tables_for(sid, p, t), av, lambda s=src: s.index(sid),
                        frame_fn=lambda p, tm, tb, s=src: s.frame(sid, p, tuple(tm), tb))
        inj = ctx.view_frame("injury-report", ["cubs", "yankees"], "query:injuryReport.state.data")
        assert set(inj["Team"]) <= {"Cubs", "Yankees"} and len(inj) > 0
        txn = ctx.view_frame("transaction-tracker", ["cubs"], "query:transactions.state.data")
        assert set(txn["Team"]) == {"Cubs"} and len(txn) == 5
        cl = ctx.view_frame("closer-depth-chart", ["*"], "query:closerDepthChart.state.data[*].relievers")
        assert cl["Team"].nunique() == 30 and list(cl.columns)[0] == "Team"


def test_github_reader_reads_the_same_files(demo):
    from rr import sources
    root, sid = demo
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        path = url.split("/contents/", 1)[1]
        f = root / path
        r = mock.Mock(status_code=200 if f.exists() else 404, content=f.read_bytes() if f.exists() else b"")
        r.raise_for_status = lambda: None
        return r

    reader = sources.GitHubReader("me/rrdash", "data", "tok")
    with mock.patch.object(reader.session, "get", side_effect=fake_get):
        src = sources.PublishedSource(reader)
        assert [s["id"] for s in src.snapshots()] == [sid]
        df = src.frame(sid, "payroll", ("cubs",), "query:payroll.state.data")
        assert set(df["Team"]) == {"Cubs"}
        assert src.gate_status()["last_attempt"] is None  # no pull log in the demo data
    url, params, headers = calls[0]
    assert url == "https://api.github.com/repos/me/rrdash/contents/published/index.json"
    assert params == {"ref": "data"} and headers["Authorization"] == "Bearer tok"
    assert headers["Accept"] == "application/vnd.github.raw+json"
    assert not any("fangraphs" in c[0] for c in calls)


def test_github_reader_auth_error():
    from rr import sources
    reader = sources.GitHubReader("me/rrdash", "data", "bad")
    with mock.patch.object(reader.session, "get", return_value=mock.Mock(status_code=401)):
        with pytest.raises(sources.ReaderError, match="refused access"):
            reader.read("published/index.json")


def test_gate_status_read_from_published_log(tmp_path):
    from rr import sources
    from rr.gate import Gate
    Gate(tmp_path / "state").record("attempt_start")
    Gate(tmp_path / "state").halt("403 challenge")
    st = sources.PublishedSource(sources.FileReader(tmp_path)).gate_status()
    assert st["last_attempt"] is not None and st["halted"]


def test_retention(tmp_path):
    from rr import published, store
    import make_demo_data
    snap = make_demo_data.main(tmp_path / "a")
    for i in range(4):
        s2 = store.create_snapshot(tmp_path, f"2030010{i}T000000Z_current_2030")
        store.save_raw(s2, "breakdowns/coaches", None, store.load_raw(snap, "breakdowns/coaches", None))
        store.write_manifest(s2, {**store.read_manifest(snap),
                                  "results": [{"page": "breakdowns/coaches", "team": None, "ok": True}]})
        published.publish(tmp_path, s2, keep=2, log=lambda _: None)
    assert [p.name for p in published.list_published(tmp_path)] == [
        "20300103T000000Z_current_2030", "20300102T000000Z_current_2030"]
    idx = json.loads((tmp_path / "published" / "index.json").read_text())
    assert [s["id"] for s in idx["snapshots"]] == ["20300103T000000Z_current_2030", "20300102T000000Z_current_2030"]
    assert len(store.prune(tmp_path, 2)) == 2


def test_github_reader_rate_limit_message():
    from rr import sources
    reader = sources.GitHubReader("me/rrdash", "data", "tok")
    r = mock.Mock(status_code=403, headers={"x-ratelimit-remaining": "0"})
    with mock.patch.object(reader.session, "get", return_value=r):
        with pytest.raises(sources.ReaderError, match="read limit"):
            reader.read("published/index.json")


def test_validate_layout_accepts_own_layouts_and_rejects_junk():
    from rr import views
    good = [{"kind": "table", "title": "x", "page": "payroll", "teams": ["*"], "table": "t"},
            {"kind": "player", "title": "p", "player_key": "fg:1"},
            {"kind": "combined", "title": "c", "spec": {"fields": []}},
            {"kind": "summary", "title": "s", "page": "p", "table": "t", "by": "Team", "how": "count"}]
    assert views.validate_layout(good) == good
    for bad in [{}, [1], [{"kind": "evil"}], [{"kind": "table", "page": "p"}], [{"kind": "table"}] * 51,
                [{"kind": "table", "title": "x" * 500, "page": "p", "table": "t"}]]:
        with pytest.raises(ValueError):
            views.validate_layout(bad)
