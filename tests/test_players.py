import pandas as pd

from rr import league, players, views


def T(rows):
    return pd.DataFrame(rows)


def build():
    depth_a = T([{"playerid": 1, "xMLBAMID": 101, "PlayerName": "José Ramírez Jr.", "Pos": "3B"},
                 {"playerid": 2, "xMLBAMID": 102, "PlayerName": "Will Smith", "Pos": "C"}])
    depth_b = T([{"playerid": 3, "xMLBAMID": 103, "PlayerName": "Will Smith", "Pos": "RP"},
                 {"playerid": 4, "xMLBAMID": 104, "PlayerName": "Ann Other", "Pos": "SS"}])
    pay = T([{"xMLBAMID": 101, "PlayerName": "Jose Ramirez", "Salary": "$17,000,000"},
             {"xMLBAMID": 104, "PlayerName": "Ann Other", "Salary": "$1,000,000"}])
    inj = T([{"PlayerName": "Ramirez, Jose", "Injury": "Wrist"},
             {"PlayerName": "Will Smith", "Injury": "Back"},
             {"PlayerName": "Nobody Known", "Injury": "Knee"}])
    txn = T([{"PlayerName": "Ann Other", "Transaction": "Recalled"},
             {"PlayerName": "Ann Other", "Transaction": "Optioned"}])
    nav = T([{"name": "Red Sox", "slug": "red-sox"}, {"name": "Cubs", "slug": "cubs"}])
    return players.PlayerIndex([
        ("depth-charts", "guardians", "dc", depth_a), ("depth-charts", "dodgers", "dc", depth_b),
        ("depth-charts", "guardians", "nav", nav), ("payroll", "guardians", "pay", pay),
        ("injury-report", "guardians", "inj", inj), ("transaction-tracker", "twins", "txn", txn)])


def test_norm_name():
    assert players.norm_name("José Ramírez Jr.") == "jose ramirez"
    assert players.norm_name("Ramírez, José") == "jose ramirez"
    assert players.norm_name("Ke'Bryan Hayes") == "kebryan hayes"


def test_matching_rules():
    idx = build()
    assert ("depth-charts", "nav") not in idx.tables          # team lists aren't players
    ram = idx.tables[("injury-report", "inj")].df
    assert ram.loc[0, "_pkey"] == "fg:1" and ram.loc[0, "_match"] == "name"   # name -> unique id player
    assert ram.loc[1, "_match"] == "ambiguous"                                # two Will Smiths
    assert ram.loc[2, "_match"] == "name-only"
    pay = idx.tables[("payroll", "pay")].df
    assert list(pay["_pkey"]) == ["fg:1", "fg:4"]                             # mlbam id links to fg id


def test_profile_and_combine():
    idx = build()
    srcs = {p for p, _, _ in idx.profile("fg:1")}
    assert srcs == {"depth-charts", "payroll", "injury-report"}
    assert len(idx.profile_long("fg:1")) > 3
    spec = {"fields": [
        {"page": "depth-charts", "table": "dc", "column": "Pos", "agg": "first", "label": "Pos"},
        {"page": "payroll", "table": "pay", "column": "Salary", "agg": "sum", "label": "Salary"},
        {"page": "transaction-tracker", "table": "txn", "column": "Transaction", "agg": "all", "label": "Moves"},
        {"page": "injury-report", "table": "inj", "column": "Injury", "agg": "count", "label": "Injuries"}],
        "rows_from": 0, "teams": ["*"]}
    out = idx.combine(spec).set_index("Player")
    assert len(out) == 4
    assert out.loc["José Ramírez Jr.", "Salary"] == 17_000_000
    assert out.loc["Ann Other", "Moves"] == "Recalled; Optioned"
    assert out.loc["José Ramírez Jr.", "Injuries"] == 1
    out2 = idx.combine({**spec, "rows_from": "any", "teams": ["Cleveland"]})
    assert set(out2["Team"]) == {"Cleveland"}
    assert not any(k.startswith("name?:") for k in idx.combine({**spec, "rows_from": "any"})["Player"])


def test_league_compile_and_summary():
    tabs = {"athletics": {"dc": T([{"PlayerName": "A B", "Pos": "C", "Age": 25}])},
            "cubs": {"dc": T([{"PlayerName": "C D", "Pos": "C", "Age": 31}, {"PlayerName": "E F", "Pos": "SS", "Age": 29}])}}
    tf = lambda page, team: tabs[team]
    cov = league.coverage(tf, "depth-charts", ["athletics", "cubs"])
    assert cov.iloc[0].to_dict() == {"Table": "dc", "Teams": 2, "Rows": 3, "Same for every team": False}
    df = views.combined_frame(tf, "depth-charts", ["athletics", "cubs"], "dc")
    assert list(df["Team"]) == ["Athletics", "Cubs", "Cubs"]
    s = league.summarize(df, "Team", "Pos", "count")
    assert s.set_index("Team").loc["Cubs", "Total"] == 2
    m = league.summarize(df, "Team", "Age", "mean").set_index("Team")
    assert m.loc["Cubs"].iloc[0] == 30
    ctx = views.Ctx(tf, {"depth-charts": ["athletics", "cubs"]}, None)
    assert len(views.panel_frame(ctx, {"kind": "table", "page": "depth-charts", "teams": ["*"], "table": "dc"})) == 3


# ---- styling, storage, remote --------------------------------------------------
def test_fgstyle_rows_links_headers():
    from rr import fgstyle
    df = pd.DataFrame([
        {"playerid": 123, "PlayerName": "A B", "HowAcquired": "Trade (NYM) Dec'25", "Status": "", "Salary2026": 25000000.0, "Team": "Cubs"},
        {"playerid": 124, "PlayerName": "C D", "HowAcquired": "Drafted 2nd Rd '19", "Status": "60IL", "Salary2026": 760000.0, "Team": "Cubs"},
        {"playerid": 125, "PlayerName": "E F", "HowAcquired": "Waivers (LAA) Jul'24", "Status": "", "Salary2026": None, "Team": "Cubs"}])
    h = fgstyle.table_html(df, season=2026)
    assert "statss.aspx?playerid=123" in h and ">PLAYER<" in h and ">PLAYERID<" not in h
    assert "class='fg-acq'" in h and "class='fg-il'" in h and "Acquired since end of 2025 season" in h
    assert "$25,000,000" in h and ">CHC<" in h


def test_store_gzip_and_prune(tmp_path):
    import time
    from rr import store
    snaps = []
    for i in range(3):
        s = store.snapshots_dir(tmp_path) / f"2026010{i}T000000Z_current_2026"
        s.mkdir(parents=True)
        store.save_raw(s, "breakdowns/coaches", None, {"payload": {"x": i}})
        store.write_manifest(s, {"results": []})
        snaps.append(s)
    assert store.load_raw(snaps[0], "breakdowns/coaches", None)["payload"] == {"x": 0}
    assert (snaps[0] / "raw" / "breakdowns__coaches" / "_league.json.gz").exists()
    gone = store.prune(tmp_path, keep=2)
    assert gone == [snaps[0]] and len(store.list_snapshots(tmp_path)) == 2


def test_gatecheck_exit_codes(tmp_path, monkeypatch):
    from rr import config, gatecheck
    from rr.gate import Gate
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    assert gatecheck.main() == 0
    Gate(tmp_path).record("attempt_start")
    assert gatecheck.main() == 3


def test_gatecheck_halt_code(tmp_path, monkeypatch):
    from rr import config, gatecheck
    from rr.gate import Gate
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    Gate(tmp_path).halt("403")
    assert gatecheck.main() == 4


# ---- lever 1: league-wide pages ----------------------------------------------------
def test_request_count_is_97():
    from rr import config
    from rr.refresh import plan_jobs
    jobs = plan_jobs(list(config.TEAMS.values()), list(config.ALL_PAGES))
    assert len(jobs) == 97
    urls = {j.url for j in jobs}
    for page in ("injury-report", "transaction-tracker", "closer-depth-chart"):
        assert f"https://www.fangraphs.com/roster-resource/{page}" in urls
        assert f"https://www.fangraphs.com/roster-resource/{page}/cubs" not in urls


def test_grouped_layout_is_flattened_with_team():
    from rr import parse
    payload = {"props": {"pageProps": {"data": [
        {"team": "CHC", "relievers": [{"PlayerName": "A B", "Role": "CL"}, {"PlayerName": "C D", "Role": "SU"}]},
        {"team": "NYY", "relievers": [{"PlayerName": "E F", "Role": "CL", "team": "x"}]}]}}}
    t = parse.discover_tables(payload)
    flat = t["data[*].relievers"]
    assert list(flat.columns)[:1] == ["team"] and len(flat) == 3
    assert "group.team" in flat.columns  # child's own 'team' kept, group's renamed


def test_with_team_and_filter_on_league_page():
    from rr import views
    df = pd.DataFrame([{"Team": "CHC", "PlayerName": "A"}, {"Team": "NYY", "PlayerName": "B"},
                       {"Team": "CWS", "PlayerName": "C"}])
    ctx = views.Ctx(lambda p, t: {"t": df}, {"injury-report": [None]}, None)
    out = ctx.view_frame("injury-report", ["cubs", "white-sox"], "t")
    assert list(out["Team"]) == ["Cubs", "White Sox"]
    assert len(ctx.view_frame("injury-report", ["*"], "t")) == 3
    # an old snapshot fetched per team still works the old way
    ctx2 = views.Ctx(lambda p, t: {"t": pd.DataFrame([{"PlayerName": t}])}, {"injury-report": ["cubs", "mets"]}, None)
    assert list(ctx2.view_frame("injury-report", ["*"], "t")["Team"]) == ["Cubs", "Mets"]


def test_league_wide_warnings():
    from rr import published as publish
    full = pd.DataFrame({"Team": [a for a in ["ATH", "BAL", "BOS", "CHW", "CLE", "DET", "HOU", "KCR", "LAA", "MIN",
                                               "NYY", "SEA", "TBR", "TEX", "TOR", "ARI", "ATL", "CHC", "CIN", "COL",
                                               "LAD", "MIA", "MIL", "NYM", "PHI", "PIT", "SDP", "SFG", "STL", "WSN"]] * 4})
    assert publish.league_wide_warnings({("injury-report", None, "t"): full.head(61)}) == []
    w = publish.league_wide_warnings({("injury-report", None, "t"): full.head(100)})
    assert any("page-size" in x for x in w)
    w = publish.league_wide_warnings({("closer-depth-chart", None, "t"): full.head(10)})
    assert any("only mentions 10 teams" in x for x in w)
    w = publish.league_wide_warnings({("transaction-tracker", None, "t"): pd.DataFrame({"x": [1, 2]})})
    assert any("no team column" in x for x in w)
