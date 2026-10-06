"""RAT - metric engine.

All five metric categories (file, directory, repository, commit set, author)
with the exact semantics of the brief:

per commit h, file f    l+ (added), l- (removed), growth d = l+ - l-,
                        churn x = l+ + l-
commit set H            subset of stored commits; date filter ts >= from AND
                        ts < to (to exclusive); manual filter = explicit hash
                        list; author filter via canonical identity.  All
                        filters combine with AND.  |H| = selected commits.
per object o            l+_H, l-_H, d_H, x_H; modifications n_H = number of
                        commits in H with x > 0 on o; frequency n_H / |H|;
                        churn rate x_H / |H|; both 0 when |H| = 0.
per author a            x_H,a; ownership w = x_H,a / x_H,o, 0 when x_H,o = 0.

Path matching: exact path for files; directories use the subtree predicate
`substr(path, 1, length(:p) + 1) = :p || '/'` (never LIKE - path characters
would need escaping).  Author identity always resolves as
COALESCE(canonical_id, id).
"""

import datetime
import time

MAX_HASHES = 900
HEX = "0123456789abcdef"


class FilterError(ValueError):
    """Bad or out-of-range input; surfaced as JSON 400."""


class NotFound(LookupError):
    """Entity does not exist; surfaced as JSON 404."""


# --------------------------------------------------------------- filters

def _parse_int(raw, name, lo, hi):
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise FilterError("Invalid integer for %s: %r" % (name, raw))
    if not lo <= value <= hi:
        raise FilterError("Value for %s is out of range" % name)
    return value


def parse_filters(qs):
    """Build the commit-set filter from query params.

    from / to   epoch seconds (to exclusive)
    hashes      comma-separated hashes or hash prefixes; present-but-empty
                means the empty commit set
    author      canonical author id
    """
    f = {"from_ts": None, "to_ts": None, "hashes": None, "author_id": None}
    raw = qs.get("from", [None])[0]
    if raw:
        f["from_ts"] = _parse_int(raw, "from", 0, 2 ** 62)
    raw = qs.get("to", [None])[0]
    if raw:
        f["to_ts"] = _parse_int(raw, "to", 0, 2 ** 62)
    raw = qs.get("hashes", None)
    if raw is not None:
        text = raw[0] if raw else ""
        tokens = [t.strip().lower() for t in text.split(",") if t.strip()]
        if len(tokens) > MAX_HASHES:
            raise FilterError("Too many hashes in filter (max %d)" % MAX_HASHES)
        for t in tokens:
            if len(t) < 4 or len(t) > 40 or any(c not in HEX for c in t):
                raise FilterError("Invalid commit hash in filter: %r" % t)
        f["hashes"] = tokens
    raw = qs.get("author", [None])[0]
    if raw:
        f["author_id"] = _parse_int(raw, "author", 1, 2 ** 62)
    return f


def clean_path(raw):
    """Normalise and validate a repo-relative path ('' = root)."""
    p = (raw or "").strip().strip("/")
    if len(p) > 1000:
        raise FilterError("Path too long")
    if p and any(seg in ("", ".", "..") for seg in p.split("/")):
        raise FilterError("Invalid path: %r" % raw)
    return p


def _commit_where(f, repo_id):
    """WHERE fragment over commits alias c, plus its bound parameters."""
    parts = ["c.repo_id = ?"]
    params = [repo_id]
    if f["from_ts"] is not None:
        parts.append("c.ts >= ?")
        params.append(f["from_ts"])
    if f["to_ts"] is not None:
        parts.append("c.ts < ?")
        params.append(f["to_ts"])
    if f["author_id"] is not None:
        parts.append(
            "(SELECT COALESCE(auth2.canonical_id, auth2.id) FROM authors auth2 "
            "WHERE auth2.id = c.author_id) = ?"
        )
        params.append(f["author_id"])
    if f["hashes"] is not None:
        tokens = f["hashes"]
        if not tokens:
            parts.append("0")  # empty commit set
        else:
            ors = []
            exact = sorted({t[:12] for t in tokens if len(t) >= 12})
            short = sorted({t for t in tokens if len(t) < 12})
            if exact:
                ors.append(
                    "substr(c.hash, 1, 12) IN (%s)" % ",".join("?" * len(exact))
                )
                params.extend(exact)
            for t in short:
                ors.append("c.hash LIKE ? || '%'")
                params.append(t)
            parts.append("(" + " OR ".join(ors) + ")")
    return " AND ".join(parts), params


# ------------------------------------------------------------ aggregation

def _path_clause(kind, path):
    """(sql, params) matching a file exactly or a directory's subtree."""
    if kind == "root":
        return "", []
    if kind == "file":
        return "f.path = ?", [path]
    return "substr(f.path, 1, length(?) + 1) = ? || '/'", [path, path]


def _agg(conn, repo_id, kind, path, where, wparams):
    """(added, removed, modifications) for an object under the commit filter."""
    p_sql, p_params = _path_clause(kind, path)
    sql = (
        "SELECT COALESCE(SUM(f.added), 0) AS added, "
        "COALESCE(SUM(f.removed), 0) AS removed, "
        "COUNT(DISTINCT CASE WHEN f.added + f.removed > 0 THEN f.commit_id END) AS modifications "
        "FROM files f JOIN commits c ON c.id = f.commit_id "
        "WHERE f.repo_id = ? "
    )
    params = [repo_id]
    if p_sql:
        sql += "AND " + p_sql + " "
        params.extend(p_params)
    sql += "AND " + where
    params.extend(wparams)
    row = conn.execute(sql, params).fetchone()
    return int(row["added"]), int(row["removed"]), int(row["modifications"])


def _derived(added, removed, modifications, h_size):
    churn = added + removed
    return {
        "added": added,
        "removed": removed,
        "growth": added - removed,
        "churn": churn,
        "modifications": modifications,
        "frequency": (modifications / float(h_size)) if h_size else 0.0,
        "churn_rate": (churn / float(h_size)) if h_size else 0.0,
    }


def commit_set_size(conn, repo_id, f):
    where, params = _commit_where(f, repo_id)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM commits c WHERE " + where, params
    ).fetchone()
    return int(row["n"])


def get_repo(conn, repo_id):
    row = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
    if row is None:
        raise NotFound("No such repository: %d" % repo_id)
    return dict(row)


def _canonical_map(conn, repo_id):
    """id -> canonical id, plus display name/email per canonical id."""
    rows = conn.execute(
        "SELECT id, name, email, canonical_id FROM authors WHERE repo_id = ?",
        (repo_id,),
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    canonical = {}
    for r in rows:
        canonical[r["id"]] = r["canonical_id"] or r["id"]
    display = {}
    for cid in set(canonical.values()):
        row = by_id.get(cid)
        display[cid] = (row["name"], row["email"]) if row else ("unknown", "")
    return canonical, display, by_id


# -------------------------------------------------------------- endpoints

def summary(conn, repo_id, f):
    h_size = commit_set_size(conn, repo_id, f)
    where, wparams = _commit_where(f, repo_id)
    added, removed, modifications = _agg(conn, repo_id, "root", "", where, wparams)
    out = _derived(added, removed, modifications, h_size)
    out["commits"] = h_size
    out["files"] = int(
        conn.execute(
            "SELECT COUNT(DISTINCT f.path) AS n FROM files f "
            "JOIN commits c ON c.id = f.commit_id WHERE f.repo_id = ? AND " + where,
            [repo_id] + wparams,
        ).fetchone()["n"]
    )
    out["authors"] = int(
        conn.execute(
            "SELECT COUNT(DISTINCT COALESCE(a.canonical_id, a.id)) AS n FROM commits c "
            "JOIN authors a ON a.id = c.author_id WHERE " + where,
            wparams,
        ).fetchone()["n"]
    )
    return out


def tree(conn, repo_id, dirpath, f):
    base = clean_path(dirpath)
    h_size = commit_set_size(conn, repo_id, f)
    where, wparams = _commit_where(f, repo_id)

    # The directory structure is listing-only and filter independent.
    p_sql, p_params = _path_clause("root" if not base else "dir", base)
    sql = "SELECT DISTINCT f.path FROM files f WHERE f.repo_id = ? "
    params = [repo_id]
    if p_sql:
        sql += "AND " + p_sql
        params.extend(p_params)
    children = {}
    for row in conn.execute(sql, params).fetchall():
        full = row["path"]
        rest = full[len(base) + 1:] if base else full
        if not rest:
            continue
        name, sep, _tail = rest.partition("/")
        if not name:
            continue
        kind = "dir" if sep else "file"
        children[(name, kind)] = (base + "/" + name) if base else name

    out = []
    for (name, kind), path in sorted(children.items()):
        added, removed, mods = _agg(
            conn, repo_id, "file" if kind == "file" else "dir", path, where, wparams
        )
        item = _derived(added, removed, mods, h_size)
        item.update({"name": name, "path": path, "kind": kind})
        out.append(item)
    dirs = sorted((c for c in out if c["kind"] == "dir"), key=lambda c: c["name"])
    files = sorted((c for c in out if c["kind"] == "file"), key=lambda c: c["name"])
    return {"path": base, "children": dirs + files}


def file_detail(conn, repo_id, path, f):
    path = clean_path(path)
    if not path:
        raise FilterError("A file path is required")
    h_size = commit_set_size(conn, repo_id, f)
    where, wparams = _commit_where(f, repo_id)
    added, removed, modifications = _agg(conn, repo_id, "file", path, where, wparams)
    out = _derived(added, removed, modifications, h_size)
    out["path"] = path

    total_churn = out["churn"]
    canonical, display, _by_id = _canonical_map(conn, repo_id)
    rows = conn.execute(
        "SELECT COALESCE(a.canonical_id, a.id) AS cid, "
        "COALESCE(SUM(f.added), 0) AS added, COALESCE(SUM(f.removed), 0) AS removed, "
        "COUNT(DISTINCT CASE WHEN f.added + f.removed > 0 THEN f.commit_id END) AS modifications, "
        "COUNT(DISTINCT f.commit_id) AS commits "
        "FROM files f JOIN commits c ON c.id = f.commit_id "
        "JOIN authors a ON a.id = c.author_id "
        "WHERE f.repo_id = ? AND f.path = ? AND " + where + " GROUP BY cid",
        [repo_id, path] + wparams,
    ).fetchall()
    entries = []
    for r in rows:
        cid = r["cid"]
        name, email = display.get(cid, ("unknown", ""))
        churn = int(r["added"]) + int(r["removed"])
        entries.append(
            {
                "id": cid,
                "name": name,
                "email": email,
                "commits": int(r["commits"]),
                "modifications": int(r["modifications"]),
                "added": int(r["added"]),
                "removed": int(r["removed"]),
                "churn": churn,
                "ownership": (churn / float(total_churn)) if total_churn else 0.0,
            }
        )
    entries.sort(key=lambda e: (-e["churn"], e["name"].lower()))
    out["authors"] = entries
    return out


def authors_table(conn, repo_id, f):
    where, wparams = _commit_where(f, repo_id)
    _added, _removed, _mods = _agg(conn, repo_id, "root", "", where, wparams)
    total_churn = _added + _removed

    canonical, display, by_id = _canonical_map(conn, repo_id)
    groups = {}
    for aid, cid in canonical.items():
        g = groups.setdefault(
            cid,
            {
                "id": cid,
                "name": display.get(cid, ("unknown", ""))[0],
                "email": display.get(cid, ("unknown", ""))[1],
                "identities": [],
                "commits": 0,
                "modifications": 0,
                "churn": 0,
            },
        )
        row = by_id[aid]
        g["identities"].append({"id": aid, "name": row["name"], "email": row["email"]})

    rows = conn.execute(
        "SELECT COALESCE(a.canonical_id, a.id) AS cid, "
        "COUNT(DISTINCT c.id) AS commits, "
        "COALESCE(SUM(f.added + f.removed), 0) AS churn, "
        "COUNT(DISTINCT CASE WHEN COALESCE(f.added, 0) + COALESCE(f.removed, 0) > 0 "
        "THEN c.id END) AS modifications "
        "FROM commits c JOIN authors a ON a.id = c.author_id "
        "LEFT JOIN files f ON f.commit_id = c.id AND f.repo_id = c.repo_id "
        "WHERE " + where + " GROUP BY cid",
        wparams,
    ).fetchall()
    active = set()
    for r in rows:
        g = groups.get(r["cid"])
        if g is None:
            continue
        active.add(r["cid"])
        g["commits"] = int(r["commits"])
        g["modifications"] = int(r["modifications"])
        g["churn"] = int(r["churn"])

    out = []
    for cid in active:
        g = groups[cid]
        g["ownership"] = (g["churn"] / float(total_churn)) if total_churn else 0.0
        g["identities"].sort(key=lambda i: i["id"])
        out.append(g)
    out.sort(key=lambda g: (-g["churn"], g["name"].lower()))
    return out


def merge_authors(conn, repo_id, canonical_id, merge_ids):
    """Manual identity merge: point merged rows at the canonical row."""
    rows = {
        r["id"]: r["canonical_id"]
        for r in conn.execute(
            "SELECT id, canonical_id FROM authors WHERE repo_id = ?", (repo_id,)
        ).fetchall()
    }
    if canonical_id not in rows:
        raise FilterError("Unknown canonical author id: %r" % canonical_id)
    merge_ids = [m for m in dict.fromkeys(merge_ids or [])]
    if not merge_ids:
        raise FilterError("No author identities selected to merge")
    for m in merge_ids:
        if m not in rows:
            raise FilterError("Unknown author id: %r" % m)

    target = rows[canonical_id] or canonical_id
    merged = 0
    for m in merge_ids:
        actual = rows[m] or m
        if actual == target:
            continue
        conn.execute(
            "UPDATE authors SET canonical_id = ? WHERE repo_id = ? AND (id = ? OR canonical_id = ?)",
            (target, repo_id, actual, actual),
        )
        for aid in rows:
            if rows[aid] == actual:
                rows[aid] = target
        merged += 1
    conn.commit()
    return merged


def commits_list(conn, repo_id, f, limit, offset, query):
    where, wparams = _commit_where(f, repo_id)
    extra = ""
    eparams = []
    query = (query or "").strip()
    if query:
        if len(query) > 80:
            raise FilterError("Search query too long")
        extra = " AND (c.hash LIKE ? || '%' OR instr(lower(c.subject), ?) > 0)"
        eparams = [query.lower(), query.lower()]
    total = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM commits c WHERE " + where + extra,
            wparams + eparams,
        ).fetchone()["n"]
    )
    rows = conn.execute(
        "SELECT c.hash AS hash, c.ts AS ts, c.subject AS subject, "
        "COALESCE(a.canonical_id, a.id) AS author_id, a2.name AS author_name "
        "FROM commits c JOIN authors a ON a.id = c.author_id "
        "JOIN authors a2 ON a2.id = COALESCE(a.canonical_id, a.id) "
        "WHERE " + where + extra + " ORDER BY c.ts DESC, c.id DESC LIMIT ? OFFSET ?",
        wparams + eparams + [limit, offset],
    ).fetchall()
    return {
        "total": total,
        "commits": [
            {
                "hash": r["hash"],
                "ts": int(r["ts"]),
                "subject": r["subject"],
                "author_id": int(r["author_id"]),
                "author": r["author_name"],
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------- charts

def _bucket_labels(ts_values):
    """Adaptive day/week/month bucketing for the churn chart."""
    if not ts_values:
        return [], "day"
    span_days = (max(ts_values) - min(ts_values)) / 86400.0
    if span_days <= 62:
        mode = "day"
    elif span_days <= 400:
        mode = "week"
    else:
        mode = "month"
    return mode


def _bucket_key(ts, mode):
    if mode == "day":
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    if mode == "month":
        return time.strftime("%Y-%m", time.localtime(ts))
    iso = datetime.date.fromtimestamp(ts).isocalendar()
    return "%d-W%02d" % (iso[0], iso[1])


def chart(conn, repo_id, ctype, f):
    where, wparams = _commit_where(f, repo_id)
    if ctype == "churn":
        # LEFT JOIN from commits so commits whose only files are binary (and
        # therefore unmeasured) still produce a time bucket: they belong to H
        # even though they contribute no lines.
        rows = conn.execute(
            "SELECT c.ts AS ts, COALESCE(SUM(f.added), 0) AS added, "
            "COALESCE(SUM(f.removed), 0) AS removed "
            "FROM commits c "
            "LEFT JOIN files f ON f.commit_id = c.id AND f.repo_id = c.repo_id "
            "WHERE " + where + " GROUP BY c.id",
            wparams,
        ).fetchall()
        mode = _bucket_labels([r["ts"] for r in rows])
        buckets = {}
        for r in rows:
            key = _bucket_key(r["ts"], mode)
            b = buckets.setdefault(key, {"added": 0, "removed": 0})
            b["added"] += int(r["added"])
            b["removed"] += int(r["removed"])
        labels = sorted(buckets.keys())
        return {
            "labels": labels,
            "bucket": mode,
            "series": [
                {"name": "Added", "data": [buckets[k]["added"] for k in labels]},
                {"name": "Removed", "data": [buckets[k]["removed"] for k in labels]},
                {
                    "name": "Churn",
                    "data": [buckets[k]["added"] + buckets[k]["removed"] for k in labels],
                },
            ],
        }
    if ctype == "topfiles":
        rows = conn.execute(
            "SELECT f.path AS path, SUM(f.added) AS added, SUM(f.removed) AS removed, "
            "SUM(f.added + f.removed) AS churn "
            "FROM files f JOIN commits c ON c.id = f.commit_id "
            "WHERE f.repo_id = ? AND " + where + " "
            "GROUP BY f.path HAVING churn > 0 ORDER BY churn DESC, f.path LIMIT 10",
            [repo_id] + wparams,
        ).fetchall()
        return {
            "labels": [r["path"] for r in rows],
            "series": [
                {"name": "Churn", "data": [int(r["churn"]) for r in rows]},
                {"name": "Added", "data": [int(r["added"]) for r in rows]},
                {"name": "Removed", "data": [int(r["removed"]) for r in rows]},
            ],
        }
    if ctype == "authorshare":
        _a, _r, _m = _agg(conn, repo_id, "root", "", where, wparams)
        total_churn = _a + _r
        canonical, display, _by_id = _canonical_map(conn, repo_id)
        rows = conn.execute(
            "SELECT COALESCE(a.canonical_id, a.id) AS cid, "
            "COALESCE(SUM(f.added + f.removed), 0) AS churn "
            "FROM commits c JOIN authors a ON a.id = c.author_id "
            "LEFT JOIN files f ON f.commit_id = c.id AND f.repo_id = c.repo_id "
            "WHERE " + where + " GROUP BY cid ORDER BY churn DESC",
            wparams,
        ).fetchall()
        entries = [
            (display.get(r["cid"], ("unknown", ""))[0], int(r["churn"]))
            for r in rows
            if int(r["churn"]) > 0
        ]
        top = entries[:10]
        rest = entries[10:]
        labels = [name for name, _v in top]
        data = [v for _n, v in top]
        if rest:
            labels.append("Others (%d)" % len(rest))
            data.append(sum(v for _n, v in rest))
        return {
            "labels": labels,
            "series": [{"name": "Churn", "data": data}],
            "total": total_churn,
        }
    raise FilterError("Unknown chart type: %r" % ctype)
