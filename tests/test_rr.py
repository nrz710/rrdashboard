import json
from datetime import timedelta
from unittest import mock

import pytest
import requests

from rr import config, export, parse, store, views
from rr.fetch import BlockedError, FetchError, assert_honest_user_agent, build_session, fetch_page
from rr.gate import Gate, GateClosed, utcnow
from rr.refresh import EXIT_BLOCKED, EXIT_OK, EXIT_PARTIAL, plan_jobs, run_refresh

NEXT = {"props": {"pageProps": {"dehydratedState": {"queries": [
    {"queryKey": ["depthChart", 133], "state": {"data": {"players": [
        {"name": "<a href='/x'>Player A</a>", "pos": "C", "age": 27, "stats": {"war": 2.1}},
        {"name": "Player B", "pos": "1B", "age": 31, "stats": {"war": 0.4}},
    ]}}}]}}}}
PAGE = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(NEXT)}</script></html>'


def fake_resp(status=200, text=PAGE, headers=None, url="https://x"):
    r = requests.Response()
    r.status_code = status
    r._content = text.encode()
    r.headers.update(headers or {"Content-Type": "text/html"})
    r.url = url
    return r


# ---- gate -----------------------------------------------------------------
def test_gate_blocks_inside_48h_and_opens_after(tmp_path):
    g = Gate(tmp_path)
    g.check()
    g.record("attempt_start")
    with pytest.raises(GateClosed):
        g.check()
    with pytest.raises(GateClosed):
        g.check(now=utcnow() + timedelta(hours=47, minutes=59))
    g.check(now=utcnow() + timedelta(hours=48, minutes=1))


def test_interval_cannot_be_lowered(tmp_path):
    g = Gate(tmp_path, interval_hours=1)
    g.record("attempt_start")
    with pytest.raises(GateClosed):
        g.check(now=utcnow() + timedelta(hours=2))


def test_failed_attempt_still_consumes_window(tmp_path):
    g = Gate(tmp_path)
    g.record("attempt_start")
    g.record("partial")
    with pytest.raises(GateClosed):
        g.check(now=utcnow() + timedelta(hours=10))


def test_corrupt_log_fails_closed(tmp_path):
    g = Gate(tmp_path)
    g.log_path.write_text("{not json\n")
    with pytest.raises(GateClosed, match="unreadable"):
        g.check()


def test_future_timestamp_fails_closed(tmp_path):
    g = Gate(tmp_path)
    ts = (utcnow() + timedelta(days=3)).isoformat()
    g.log_path.write_text(json.dumps({"ts": ts, "event": "attempt_start"}) + "\n")
    with pytest.raises(GateClosed, match="future"):
        g.check(now=utcnow() + timedelta(days=10) - timedelta(days=9))


def test_halt_blocks_until_cleared(tmp_path):
    g = Gate(tmp_path)
    g.halt("403 challenge")
    with pytest.raises(GateClosed, match="HALTED"):
        g.check()
    assert g.clear_halt()
    g.check()


def test_lock_is_exclusive(tmp_path):
    g1, g2 = Gate(tmp_path), Gate(tmp_path)
    with g1.exclusive():
        with pytest.raises(GateClosed, match="already running"):
            with g2.exclusive():
                pass


# ---- user agent -----------------------------------------------------------
@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Mozilla/5.0 (compatible; Google-Apps-Script)",
    "something Firefox/1",
])
def test_browser_uas_refused(ua):
    with pytest.raises(ValueError):
        build_session(ua)


def test_default_and_honest_uas_ok():
    s = build_session(None)
    assert s.headers["User-Agent"].startswith("python-requests/")
    build_session("rr-dashboard/0.1 (personal research; me@example.com)")


def test_ua_checked_on_prepared_request():
    s = build_session(None)
    s.headers["User-Agent"] = "Mozilla/5.0 sneaky"
    with mock.patch.object(s, "send") as send:
        with pytest.raises(ValueError):
            fetch_page(s, "https://x", 5)
        send.assert_not_called()


# ---- fetch ----------------------------------------------------------------
def test_block_detection():
    s = build_session()
    for resp in [fake_resp(403, "<title>Just a moment...</title>"),
                 fake_resp(429, "slow down"),
                 fake_resp(200, "<title>Just a moment...</title>")]:
        with mock.patch.object(s, "send", return_value=resp):
            with pytest.raises(BlockedError):
                fetch_page(s, "https://x", 5)
    with mock.patch.object(s, "send", return_value=fake_resp(404, "nope")):
        with pytest.raises(FetchError):
            fetch_page(s, "https://x", 5)


def test_fetch_ok_and_json():
    s = build_session()
    with mock.patch.object(s, "send", return_value=fake_resp()):
        rec = fetch_page(s, "https://x", 5)
    assert rec["source"] == "next_data" and rec["payload"] == NEXT
    with mock.patch.object(s, "send", return_value=fake_resp(200, '{"a":[1]}', {"Content-Type": "application/json"})):
        assert fetch_page(s, "https://x", 5)["source"] == "json"


# ---- parse / views / export -----------------------------------------------
def test_discover_tables():
    t = parse.discover_tables(NEXT, team_slug="athletics")
    assert list(t) == ["depthChart > players"]
    df = t["depthChart > players"]
    assert df["name"].tolist() == ["Player A", "Player B"]
    assert "stats.war" in df.columns


def test_views_and_exports():
    tables = parse.discover_tables(NEXT)
    tf = lambda page, team: tables
    panel = {"title": "DC", "page": "depth-charts", "teams": ["athletics", "cubs"],
             "table": "depthChart > players", "columns": ["name", "age"],
             "filter": "player a", "sort_by": "age", "ascending": False}
    ctx = views.Ctx(tf, {"depth-charts": ["athletics", "cubs"]}, None)
    df = views.panel_frame(ctx, panel)
    assert list(df.columns) == ["Team", "name", "age"] and len(df) == 2
    meta = {"title": "T", "snapshot": "s", "exported": "e"}
    items = [("Depth: A's [x]", df), ("Depth: A's [x]", df)]
    assert export.to_excel(items, meta)[:2] == b"PK"
    assert export.to_csv_zip(items, meta)[:2] == b"PK"
    assert "<table" in export.to_html(items, meta)


# ---- refresh end-to-end with a fake network --------------------------------
def _run(tmp_path, send_side_effect, teams=("athletics", "cubs")):
    g = Gate(tmp_path / "state")
    s = build_session()
    jobs = plan_jobs(list(teams), ["depth-charts", "free-agent-tracker"])
    with mock.patch.object(s, "send", side_effect=send_side_effect):
        code = run_refresh(gate=g, data_dir=tmp_path, jobs=jobs, kind="current", season=2026,
                           session=s, sleep=lambda _: None, log=lambda _: None)
    return g, code, s


def test_refresh_success_then_gate_refuses(tmp_path):
    g, code, _ = _run(tmp_path, lambda *a, **k: fake_resp())
    assert code == EXIT_OK
    events = [e["event"] for e in g.read_log()]
    assert events == ["attempt_start", "success"]
    snap = store.list_snapshots(tmp_path)[0]
    assert store.read_manifest(snap)["status"] == "complete"
    assert set(store.available(snap)) == {"depth-charts", "free-agent-tracker"}
    with pytest.raises(GateClosed):
        _run(tmp_path, lambda *a, **k: fake_resp())


def test_refresh_blocked_halts_immediately(tmp_path):
    calls = []
    def side(*a, **k):
        calls.append(1)
        return fake_resp() if len(calls) == 1 else fake_resp(403, "Just a moment...")
    g, code, _ = _run(tmp_path, side)
    assert code == EXIT_BLOCKED and len(calls) == 2
    assert g.halt_path.exists()
    snap = store.list_snapshots(tmp_path)[0]
    assert store.read_manifest(snap)["status"] == "blocked"


def test_refresh_network_error_no_retry(tmp_path):
    calls = []
    def side(*a, **k):
        calls.append(1)
        raise requests.ConnectionError("down")
    g, code, _ = _run(tmp_path, side)
    assert code == EXIT_PARTIAL and len(calls) == 1
    assert [e["event"] for e in g.read_log()] == ["attempt_start", "partial"]


def test_before_fetch_hook_runs_before_requests_and_failure_blocks_fetch(tmp_path):
    order = []
    def hook():
        order.append("hook")
    def side(*a, **k):
        order.append("fetch")
        return fake_resp()
    g = Gate(tmp_path / "state")
    s = build_session()
    jobs = plan_jobs(["athletics"], ["depth-charts"])
    with mock.patch.object(s, "send", side_effect=side):
        run_refresh(gate=g, data_dir=tmp_path, jobs=jobs, kind="current", season=2026, session=s,
                    sleep=lambda _: None, log=lambda _: None, before_fetch=hook)
    assert order == ["hook", "fetch"]

    g2 = Gate(tmp_path / "state2")
    def bad():
        raise RuntimeError("push failed")
    with mock.patch.object(s, "send") as send:
        code = run_refresh(gate=g2, data_dir=tmp_path / "d2", jobs=jobs, kind="current", season=2026, session=s,
                           sleep=lambda _: None, log=lambda _: None, before_fetch=bad)
    assert code == 5 and not send.called
    assert [e["event"] for e in g2.read_log()] == ["attempt_start", "failure"]


def test_record_prior_pull_only_makes_gate_stricter(tmp_path, monkeypatch):
    from rr import cli, config
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "d")
    recent = (utcnow() - timedelta(hours=5)).isoformat(timespec="seconds")
    assert cli.main(["record-prior-pull", recent]) == 0
    with pytest.raises(GateClosed):
        Gate(tmp_path).check()
    future = (utcnow() + timedelta(hours=5)).isoformat(timespec="seconds")
    assert cli.main(["record-prior-pull", future]) == 1
    assert cli.main(["record-prior-pull", "2026-09-24 08:00"]) == 1  # no time zone
    assert len(Gate(tmp_path).read_log()) == 1


def test_real_rosterresource_structure():
    """Shaped like a real RosterResource page (from a live pull): query wrappers with the
    team in the key, a team list, and player rows nested a level down."""
    def page(team, abbr, tid, n):
        teams = [{"TeamId": i, "ShortName": f"T{i}", "AbbName": f"A{i}", "FullName": f"Team {i}"} for i in range(30)]
        rows = [{"gameDate": "2026-09-16", "mlbamid": 656240 + i, "playerName": f"P {i}", "teamid": tid, "ip": 1.2}
                for i in range(n)]
        return {"props": {"pageProps": {"dehydratedState": {"queries": [
            {"dehydratedAt": 1790277634344, "queryKey": ["depth-charts-all", tid, "AL", "W", team, abbr],
             "queryHash": "x", "state": {"data": {"dataLoadDate": "1790262734", "dataTeamList": teams,
                                                  "dataBullpenUsage": {"dataPlayers": rows}},
                                         "dataUpdateCount": 1, "error": None}}]}}}}
    a = parse.discover_tables(page("Athletics", "ATH", 10, 40), team_slug="athletics")
    b = parse.discover_tables(page("Chicago Cubs", "CHC", 17, 5), team_slug="cubs")
    assert set(a) == set(b) == {"depth-charts-all > dataTeamList", "depth-charts-all > dataBullpenUsage.dataPlayers"}
    df = a["depth-charts-all > dataBullpenUsage.dataPlayers"]
    assert list(df.columns) == ["gameDate", "mlbamid", "playerName", "teamid", "ip"] and len(df) == 40
    assert not any(c.lower().startswith(("state", "querykey", "dehydrated")) for t in a.values() for c in t.columns)
