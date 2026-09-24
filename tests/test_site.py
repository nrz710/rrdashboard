"""The static website build (GitHub Pages)."""
import json

import pytest


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    from rr import site as sitemod
    out = tmp_path_factory.mktemp("site") / "_site"
    sitemod.build_demo(out, log=lambda _: None)
    return out


def test_site_files(site):
    assert (site / "index.html").exists() and (site / "fg.css").exists() and (site / ".nojekyll").exists()
    idx = json.loads((site / "data" / "index.json").read_text())
    assert len(idx["snapshots"]) == 1 and len(idx["teams"]) == 30


def test_table_payloads(site):
    idx = json.loads((site / "data" / "index.json").read_text())
    sid = idx["snapshots"][0]["id"]
    meta = json.loads((site / "data" / sid / "meta.json").read_text())
    by = {(t["page"], t["table"]): t for t in meta["tables"]}

    dc = json.loads((site / "data" / sid / by[("depth-charts", "depthChart")]["file"]).read_text())
    assert dc["per_team"] and dc["has_team"] and len(dc["rows"]) == 780
    assert set(dc["tslug"]) == set(t["slug"] for t in idx["teams"])
    assert "playerid" in dc["hidden"] and dc["name_col"] == "PlayerName" and dc["fg_id_col"] == "playerid"
    assert "fg-acq" in dc["cls"] and all(len(r) == len(dc["columns"]) for r in dc["rows"])
    assert sum(p is not None for p in dc["pkey"]) == 780

    inj = json.loads((site / "data" / sid / by[("injury-report", "injuryReport")]["file"]).read_text())
    assert not inj["per_team"] and inj["has_team"] and "Team" not in inj["columns"]   # league-wide, team normalized
    assert "fg-il" in inj["cls"] and None not in inj["tslug"]

    cl = json.loads((site / "data" / sid / by[("closer-depth-chart", "closerDepthChart > relievers")]["file"]).read_text())
    assert len(set(cl["tslug"])) == 30

    players = json.loads((site / "data" / sid / "players.json").read_text())
    assert len(players["key"]) > 700 and len(players["key"]) == len(players["name"])


def test_site_with_no_data(tmp_path):
    from rr import site as sitemod
    out = tmp_path / "_site"
    sitemod.build(tmp_path / "empty", out, log=lambda _: None)
    assert json.loads((out / "data" / "index.json").read_text())["snapshots"] == []
