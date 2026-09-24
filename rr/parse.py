"""Turn a fetched page into tables, without knowing its schema in advance.

RosterResource pages are Next.js pages that ship their data as JSON in a
<script id="__NEXT_DATA__"> tag. Rather than hard-code field names I haven't
been able to verify, we walk that JSON and treat every list of flat-ish
records as a table. The dashboard then lets you pick tables and columns.

Everything here runs offline against saved raw files, so you can change the
parsing as often as you like without re-fetching anything.
"""
from __future__ import annotations

import html
import json
import re

import pandas as pd

NEXT_DATA_RE = re.compile(
    r"<script[^>]*\bid=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", re.S | re.I
)
TAG_RE = re.compile(r"<[^>]+>")


def extract_next_data(page_html: str) -> dict | None:
    m = NEXT_DATA_RE.search(page_html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _is_scalar(v) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _is_query_entry(d) -> bool:
    """A react-query cache entry (what RosterResource pages embed): a wrapper, not data."""
    return isinstance(d, dict) and "queryKey" in d and "state" in d


def _is_record_list(node: list) -> bool:
    if not node or not all(isinstance(x, dict) for x in node):
        return False
    if any(_is_query_entry(x) for x in node):
        return False  # descend into each entry and find the real tables inside
    sample = node[:25]
    keys = set().union(*(d.keys() for d in sample))
    if len(keys) < 2:
        return False
    values = [v for d in sample for v in d.values()]
    if sum(_is_scalar(v) for v in values) / max(len(values), 1) < 0.5:
        return False
    # Rows that carry big nested blocks are containers, not tables: look inside instead.
    for v in values:
        if not _is_scalar(v) and len(json.dumps(v, default=str)) > 2000:
            return False
    return True


def _query_label(query_key) -> str:
    """Stable name for a react-query cache entry: its first string, the query's name.
    The rest of the key (team id, league, division, team name...) differs per team page,
    so it's left out; that way the same table gets the same name on all 30 teams."""
    if isinstance(query_key, list):
        for p in query_key:
            if isinstance(p, str) and p:
                return p
        return "query"
    return str(query_key)


def _clean_id(path: str, team_slug: str | None) -> str:
    """Readable, team-independent table name, e.g. 'depth-charts-all > dataBullpenUsage.dataPlayers'."""
    for prefix in ("props.pageProps.", "props."):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    if path.startswith("query:"):
        label, _, rest = path[len("query:"):].partition(".")
        rest = "." + rest if rest else ""
        for noise in (".state.data", ".state"):
            if rest.startswith(noise):
                rest = rest[len(noise):]
                break
        rest = rest.lstrip(".")
        if rest.startswith("[*]."):
            rest = rest[4:]
        path = f"{label} > {rest}" if rest else label
    if team_slug:
        path = re.sub(re.escape(team_slug), "{team}", path, flags=re.I)
    return path or "root"


def strip_html(value):
    if isinstance(value, str) and "<" in value and ">" in value:
        return html.unescape(TAG_RE.sub("", value)).strip()
    return value


def to_frame(records: list[dict]) -> pd.DataFrame:
    df = pd.json_normalize(records, max_level=1, sep=".")
    for col in df.columns:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            df[col] = df[col].map(
                lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else strip_html(v)
            )
    return df


def _child_tables(records: list[dict], path: str) -> list[tuple[str, list]]:
    """Grouped layouts (e.g. "Group By Team") nest a list of rows inside each group record.
    Flatten each such list into its own table, carrying the group's plain fields (such as
    the team) onto every row so they aren't lost."""
    keys = []
    for d in records[:25]:
        for k, v in d.items():
            if k not in keys and isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                keys.append(k)
    out = []
    for k in keys:
        rows = []
        for d in records:
            children = d.get(k)
            if not isinstance(children, list):
                continue
            parent = {pk: pv for pk, pv in d.items() if _is_scalar(pv)}
            for child in children:
                if isinstance(child, dict):
                    lead = {(pk if pk not in child else f"group.{pk}"): pv for pk, pv in parent.items()}
                    rows.append({**lead, **child})
        if rows and _is_record_list(rows):
            out.append((f"{path}[*].{k}", rows))
    return out


def discover_tables(payload, team_slug: str | None = None) -> dict[str, pd.DataFrame]:
    """Map table-id -> DataFrame for every record list found in the payload."""
    found: list[tuple[str, list]] = []

    def walk(node, path: str) -> None:
        if isinstance(node, list):
            if _is_record_list(node):
                found.append((path, node))
                found.extend(_child_tables(node, path))
                return  # don't descend into rows
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")
        elif isinstance(node, dict):
            if "queryKey" in node and "state" in node:
                path = "query:" + _query_label(node["queryKey"])
            for k, v in node.items():
                if k == "queryKey":
                    continue
                walk(v, f"{path}.{k}" if path else k)

    walk(payload, "")

    tables: dict[str, pd.DataFrame] = {}
    for path, records in found:
        tid = _clean_id(path, team_slug)
        base, n = tid, 2
        while tid in tables:
            tid = f"{base}#{n}"
            n += 1
        tables[tid] = to_frame(records)
    return tables
