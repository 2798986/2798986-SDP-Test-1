#!/usr/bin/env python3
"""RAT self-test - automated assertions proving the metric engine's semantics.

Run:  python3 selftest.py

Creates an isolated temporary DATA_DIR, ingests demo/fixture.zip through the
real ingestion pipeline, and asserts hand-computed metric values.

Hand-computed fixture expectations (see demo/make_fixture.py):

  c1 Initial commit (Alice)            +20  -0
  c2 Tidy imports and docs (Bob)       +3   -2
  c3 Rename util to helpers (Alice)    +0   -0   (pure rename)
  c4 Move helpers to lib (Alice A.)    +1   -1   (rename + edit, new path)
  c5 Remove obsolete guide (Bob)       +0   -3   (deletion)
  c6 Add logo asset (Alice)            binary - excluded
  c7 Start feature module (Alice A.)   +8   -0
  c8 Touch up readme (Bob)             +1   -0
  (merge commit                       excluded)
  c10 Polish feature module (Alice)    +2   -1
  TOTAL: added 35, removed 7, growth 28, churn 42, |H| 9, modifications 7, files 7
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import ingest  # noqa: E402
import metrics  # noqa: E402

TZ = timezone(timedelta(hours=2))


def epoch(year, month, day, hour=0, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=TZ).timestamp())


def mk_filter(from_ts=None, to_ts=None, hashes=None, author_id=None):
    return {"from_ts": from_ts, "to_ts": to_ts, "hashes": hashes, "author_id": author_id}


FULL = mk_filter()


class FixtureMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rat-selftest-")
        os.environ["DATA_DIR"] = cls.tmp
        db.init_db()
        cls.conn = db.connect()

        cur = cls.conn.execute(
            "INSERT INTO repos(name, source, path, status, created_at) "
            "VALUES ('fixture', 'sample', '', 'running', 0)"
        )
        cls.repo_id = cur.lastrowid
        dest = db.data_dir() / ("repos/%d" % cls.repo_id)
        root = ingest.extract_zip(ROOT / "demo" / "fixture.zip", dest)
        cls.conn.execute(
            "UPDATE repos SET path = ? WHERE id = ?",
            (str(root.relative_to(db.data_dir())), cls.repo_id),
        )
        cls.conn.commit()
        cls.stats = ingest.read_history(root, cls.conn, cls.repo_id)
        cls.conn.execute(
            "UPDATE repos SET status = 'ready' WHERE id = ?", (cls.repo_id,)
        )
        cls.conn.commit()

        cls.hashes = {
            r["subject"]: r["hash"]
            for r in cls.conn.execute(
                "SELECT hash, subject FROM commits WHERE repo_id = ?", (cls.repo_id,)
            )
        }
        cls.author_ids = {
            r["email"]: r["id"]
            for r in cls.conn.execute(
                "SELECT id, email FROM authors WHERE repo_id = ?", (cls.repo_id,)
            )
        }

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        os.environ.pop("DATA_DIR", None)

    # ------------------------------------------------------------- helpers
    def summary(self, f=FULL):
        return metrics.summary(self.conn, self.repo_id, f)

    def h(self, subject):
        return self.hashes[subject]

    def file(self, path, f=FULL):
        return metrics.file_detail(self.conn, self.repo_id, path, f)

    def tree(self, path="", f=FULL):
        return metrics.tree(self.conn, self.repo_id, path, f)

    def authors(self, f=FULL):
        return metrics.authors_table(self.conn, self.repo_id, f)

    def author_of(self, rows, email):
        for g in rows:
            if any(i["email"] == email for i in g["identities"]):
                return g
        self.fail("author %s not found" % email)

    # ------------------------------------------------------- ingestion
    def test_00_fixture_stats(self):
        self.assertEqual(self.stats["commits"], 9)
        self.assertEqual(self.stats["rows"], 12)
        self.assertEqual(self.stats["renames"], 2)
        self.assertEqual(self.stats["binaries"], 1)

    def test_01_merge_excluded(self):
        for subject in self.hashes:
            self.assertFalse(subject.startswith("Merge"))
        self.assertEqual(len(self.hashes), 9)

    # ----------------------------------------------------- repo totals
    def test_10_repo_totals(self):
        s = self.summary()
        self.assertEqual(s["added"], 35)
        self.assertEqual(s["removed"], 7)
        self.assertEqual(s["growth"], 28)
        self.assertEqual(s["churn"], 42)
        self.assertEqual(s["commits"], 9)
        self.assertEqual(s["modifications"], 7)
        self.assertEqual(s["files"], 7)
        self.assertEqual(s["authors"], 3)
        self.assertAlmostEqual(s["frequency"], 7 / 9)
        self.assertAlmostEqual(s["churn_rate"], 42 / 9)

    # --------------------------------------------------- edge-case commits
    def test_20_pure_rename_zero_impact(self):
        f = mk_filter(hashes=[self.h("Rename util module to helpers")])
        s = self.summary(f)
        self.assertEqual((s["added"], s["removed"], s["churn"], s["modifications"]), (0, 0, 0, 0))
        self.assertEqual(s["commits"], 1)
        self.assertEqual(s["files"], 1)  # the 0/0 row still exists on the new path
        self.assertEqual(s["frequency"], 0.0)
        self.assertEqual(s["churn_rate"], 0.0)

    def test_21_rename_edit_attributed_to_new_path(self):
        f = mk_filter(hashes=[self.h("Move helpers to lib and annotate add")])
        s = self.summary(f)
        self.assertEqual((s["added"], s["removed"]), (1, 1))
        d = self.file("lib/helpers.py", f)
        self.assertEqual((d["added"], d["removed"], d["modifications"]), (1, 1, 1))
        old = self.file("src/helpers.py", f)
        self.assertEqual((old["added"], old["removed"], old["authors"]), (0, 0, []))

    def test_22_deletion_counted(self):
        f = mk_filter(hashes=[self.h("Remove obsolete guide")])
        s = self.summary(f)
        self.assertEqual((s["added"], s["removed"], s["growth"]), (0, 3, -3))
        d = self.file("docs/guide.md", f)
        self.assertEqual(d["removed"], 3)

    def test_23_binary_excluded(self):
        f = mk_filter(hashes=[self.h("Add logo asset")])
        s = self.summary(f)
        self.assertEqual((s["added"], s["removed"], s["modifications"], s["files"]), (0, 0, 0, 0))
        self.assertEqual(s["commits"], 1)
        self.assertEqual(s["authors"], 1)  # the commit itself is stored
        n = self.conn.execute(
            "SELECT COUNT(*) FROM files WHERE repo_id = ? AND path LIKE '%logo%'",
            (self.repo_id,),
        ).fetchone()[0]
        self.assertEqual(n, 0)

    # ------------------------------------------------------ commit sets
    def test_30_date_range(self):
        f = mk_filter(from_ts=epoch(2026, 2, 1), to_ts=epoch(2026, 3, 1))
        s = self.summary(f)
        self.assertEqual(s["commits"], 2)  # rename + rename/edit
        self.assertEqual((s["added"], s["removed"], s["churn"], s["growth"]), (1, 1, 2, 0))
        self.assertEqual(s["modifications"], 1)
        self.assertEqual(s["files"], 2)
        self.assertAlmostEqual(s["frequency"], 0.5)
        self.assertAlmostEqual(s["churn_rate"], 1.0)

    def test_31_empty_commit_set(self):
        f = mk_filter(from_ts=epoch(2026, 5, 1), to_ts=epoch(2026, 6, 1))
        s = self.summary(f)
        self.assertEqual(s["commits"], 0)
        self.assertEqual(
            (s["added"], s["removed"], s["churn"], s["modifications"]), (0, 0, 0, 0)
        )
        self.assertEqual(s["frequency"], 0.0)
        self.assertEqual(s["churn_rate"], 0.0)
        self.assertEqual(self.authors(f), [])
        d = self.file("src/main.py", f)
        self.assertEqual((d["churn"], d["authors"]), (0, []))

    def test_32_hashes_filter(self):
        f = mk_filter(hashes=[self.h("Initial commit")])
        s = self.summary(f)
        self.assertEqual((s["added"], s["removed"]), (20, 0))
        self.assertEqual(s["commits"], 1)
        self.assertEqual(s["files"], 4)
        self.assertEqual(s["modifications"], 1)
        self.assertAlmostEqual(s["frequency"], 1.0)
        self.assertAlmostEqual(s["churn_rate"], 20.0)

    def test_33_hash_prefix_and_empty_hashes(self):
        prefix = self.h("Initial commit")[:7]
        f = mk_filter(hashes=[prefix])
        self.assertEqual(self.summary(f)["commits"], 1)
        f = mk_filter(hashes=[])
        self.assertEqual(self.summary(f)["commits"], 0)

    def test_34_author_filter_bob(self):
        f = mk_filter(author_id=self.author_ids["bob@example.com"])
        s = self.summary(f)
        self.assertEqual(s["commits"], 3)
        self.assertEqual((s["added"], s["removed"], s["growth"], s["churn"]), (4, 5, -1, 9))
        self.assertEqual(s["modifications"], 3)
        self.assertEqual(s["files"], 3)
        self.assertEqual(s["authors"], 1)
        rows = self.authors(f)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["ownership"], 1.0)

    def test_35_date_and_author_combined(self):
        f = mk_filter(
            from_ts=epoch(2026, 3, 1), author_id=self.author_ids["bob@example.com"]
        )
        s = self.summary(f)
        self.assertEqual(s["commits"], 2)  # c5 + c8
        self.assertEqual((s["added"], s["removed"], s["churn"]), (1, 3, 4))
        self.assertEqual(s["files"], 2)

    # ---------------------------------------------------------- directory
    def test_40_root_children(self):
        t = self.tree("")
        names = [c["name"] for c in t["children"]]
        kinds = [c["kind"] for c in t["children"]]
        self.assertEqual(names, ["docs", "lib", "src", "README.md"])
        self.assertEqual(kinds, ["dir", "dir", "dir", "file"])
        by_name = {c["name"]: c for c in t["children"]}
        src = by_name["src"]
        self.assertEqual((src["added"], src["removed"], src["churn"]), (26, 2, 28))
        self.assertEqual(src["modifications"], 4)
        self.assertAlmostEqual(src["frequency"], 4 / 9)
        lib = by_name["lib"]
        self.assertEqual((lib["added"], lib["removed"]), (1, 1))
        docs = by_name["docs"]
        self.assertEqual((docs["added"], docs["removed"], docs["growth"]), (4, 4, 0))
        self.assertEqual(docs["modifications"], 3)
        readme = by_name["README.md"]
        self.assertEqual((readme["added"], readme["removed"], readme["modifications"]), (4, 0, 2))
        self.assertAlmostEqual(readme["churn_rate"], 4 / 9)

    def test_41_subdir_children(self):
        t = self.tree("src")
        self.assertEqual(
            [c["name"] for c in t["children"]],
            ["feature.py", "helpers.py", "main.py", "util.py"],
        )
        t = self.tree("lib")
        self.assertEqual([c["name"] for c in t["children"]], ["helpers.py"])

    def test_42_filtered_tree_keeps_structure(self):
        f = mk_filter(from_ts=epoch(2026, 2, 1), to_ts=epoch(2026, 3, 1))
        t = self.tree("", f)
        by_name = {c["name"]: c for c in t["children"]}
        self.assertIn("docs", by_name)
        self.assertEqual(by_name["src"]["churn"], 0)
        self.assertEqual(by_name["src"]["modifications"], 0)
        self.assertEqual(by_name["src"]["frequency"], 0.0)
        self.assertEqual(by_name["lib"]["churn"], 2)
        self.assertAlmostEqual(by_name["lib"]["churn_rate"], 1.0)
        self.assertAlmostEqual(by_name["README.md"]["frequency"], 0.0)

    # ------------------------------------------------------ file detail
    def test_50_file_authors_ownership(self):
        d = self.file("src/main.py")
        self.assertEqual((d["added"], d["removed"], d["churn"], d["modifications"]), (10, 1, 11, 2))
        by_email = {a["email"]: a for a in d["authors"]}
        alice = by_email["alice@example.com"]
        bob = by_email["bob@example.com"]
        self.assertEqual((alice["churn"], bob["churn"]), (8, 3))
        self.assertAlmostEqual(alice["ownership"], 8 / 11)
        self.assertAlmostEqual(bob["ownership"], 3 / 11)
        self.assertEqual((alice["modifications"], bob["modifications"]), (1, 1))
        self.assertEqual(d["authors"][0]["email"], "alice@example.com")  # churn desc

    def test_51_lambda_zero_ownership_zero(self):
        f = mk_filter(hashes=[self.h("Rename util module to helpers")])
        d = self.file("src/helpers.py", f)
        self.assertEqual(d["churn"], 0)
        self.assertEqual(len(d["authors"]), 1)
        self.assertEqual(d["authors"][0]["churn"], 0)
        self.assertEqual(d["authors"][0]["ownership"], 0.0)

    def test_52_feature_file_two_identities(self):
        d = self.file("src/feature.py")
        self.assertEqual((d["added"], d["removed"], d["churn"]), (10, 1, 11))
        emails = sorted(a["email"] for a in d["authors"])
        self.assertEqual(emails, ["alice.alt@example.com", "alice@example.com"])
        by_email = {a["email"]: a for a in d["authors"]}
        self.assertAlmostEqual(by_email["alice.alt@example.com"]["ownership"], 8 / 11)
        self.assertAlmostEqual(by_email["alice@example.com"]["ownership"], 3 / 11)

    # ---------------------------------------------------------- authors
    def test_60_author_table_full(self):
        rows = self.authors()
        self.assertEqual(len(rows), 3)
        alice = self.author_of(rows, "alice@example.com")
        alt = self.author_of(rows, "alice.alt@example.com")
        bob = self.author_of(rows, "bob@example.com")
        self.assertEqual((alice["commits"], alice["churn"], alice["modifications"]), (4, 23, 2))
        self.assertEqual((alt["commits"], alt["churn"], alt["modifications"]), (2, 10, 2))
        self.assertEqual((bob["commits"], bob["churn"], bob["modifications"]), (3, 9, 3))
        self.assertAlmostEqual(alice["ownership"], 23 / 42)
        self.assertAlmostEqual(alt["ownership"], 10 / 42)
        self.assertAlmostEqual(bob["ownership"], 9 / 42)
        self.assertEqual(rows[0]["email"], "alice@example.com")  # sorted by churn

    def test_61_author_binary_only_commit(self):
        f = mk_filter(hashes=[self.h("Add logo asset")])
        rows = self.authors(f)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["commits"], rows[0]["churn"], rows[0]["ownership"]), (1, 0, 0.0))

    # ----------------------------------------------------------- charts
    def test_70_churn_chart(self):
        c = metrics.chart(self.conn, self.repo_id, "churn", FULL)
        self.assertEqual(c["bucket"], "week")  # 81-day span
        self.assertEqual(len(c["labels"]), 9)
        by_name = {s["name"]: s for s in c["series"]}
        self.assertEqual(sum(by_name["Churn"]["data"]), 42)
        self.assertEqual(sum(by_name["Added"]["data"]), 35)
        self.assertEqual(sum(by_name["Removed"]["data"]), 7)

    def test_71_topfiles_chart(self):
        c = metrics.chart(self.conn, self.repo_id, "topfiles", FULL)
        self.assertEqual(c["labels"][0], "src/feature.py")  # churn 11, tie broken by path
        self.assertEqual(c["labels"][1], "src/main.py")
        self.assertIn("docs/guide.md", c["labels"])
        self.assertNotIn("src/helpers.py", c["labels"])  # zero churn excluded
        self.assertEqual(sum(c["series"][0]["data"]), 42)

    def test_72_authorshare_chart(self):
        c = metrics.chart(self.conn, self.repo_id, "authorshare", FULL)
        self.assertEqual(len(c["labels"]), 3)
        self.assertEqual(sum(c["series"][0]["data"]), 42)
        self.assertEqual(c["total"], 42)

    # ------------------------------------------------------ commits list
    def test_80_commits_list(self):
        out = metrics.commits_list(self.conn, self.repo_id, FULL, 500, 0, "")
        self.assertEqual(out["total"], 9)
        self.assertEqual(len(out["commits"]), 9)
        self.assertEqual(out["commits"][0]["subject"], "Polish feature module")  # newest first
        out = metrics.commits_list(self.conn, self.repo_id, FULL, 500, 0, self.h("Initial commit")[:7])
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["commits"][0]["subject"], "Initial commit")

    # ------------------------------------------------------ validation
    def test_90_filter_validation(self):
        self.assertRaises(metrics.FilterError, metrics.parse_filters, {"from": ["abc"]})
        self.assertRaises(metrics.FilterError, metrics.parse_filters, {"hashes": ["zzzz!!"]})
        self.assertRaises(
            metrics.FilterError,
            metrics.parse_filters,
            {"hashes": [",".join("aaaa%02d" % i for i in range(901))]},
        )
        f = metrics.parse_filters({"hashes": [""]})
        self.assertEqual(f["hashes"], [])
        f = metrics.parse_filters({"from": ["100"], "to": ["200"], "author": ["3"]})
        self.assertEqual((f["from_ts"], f["to_ts"], f["author_id"]), (100, 200, 3))

    def test_91_zip_slip_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../evil.txt", "boom")
        evil = Path(self.tmp) / "evil.zip"
        evil.write_bytes(buf.getvalue())
        with self.assertRaises(ingest.IngestError):
            ingest.extract_zip(evil, Path(self.tmp) / "outdir")
        self.assertFalse((Path(self.tmp).parent / "evil.txt").exists())

    def test_92_url_validation(self):
        self.assertRaises(ingest.IngestError, ingest.validate_url, "ftp://x")
        self.assertRaises(ingest.IngestError, ingest.validate_url, "")
        self.assertRaises(ingest.IngestError, ingest.validate_url, "not a url")
        ingest.validate_url("https://github.com/DaveGamble/cJSON.git")
        self.assertEqual(
            ingest.name_from_url("https://github.com/DaveGamble/cJSON.git"), "cJSON"
        )

    # -------------------------------------------------- identity merging
    def test_zz_identity_merge_manual(self):
        rows = self.authors()
        self.assertEqual(len(rows), 3)
        alice = self.author_of(rows, "alice@example.com")
        alt = self.author_of(rows, "alice.alt@example.com")
        merged = metrics.merge_authors(
            self.conn, self.repo_id, alice["id"], [alt["id"]]
        )
        self.assertEqual(merged, 1)

        rows = self.authors()
        self.assertEqual(len(rows), 2)
        alice = self.author_of(rows, "alice@example.com")
        self.assertEqual((alice["commits"], alice["churn"], alice["modifications"]), (6, 33, 4))
        self.assertAlmostEqual(alice["ownership"], 33 / 42)
        self.assertEqual(len(alice["identities"]), 2)
        bob = self.author_of(rows, "bob@example.com")
        self.assertAlmostEqual(bob["ownership"], 9 / 42)

        d = self.file("src/feature.py")
        self.assertEqual(len(d["authors"]), 1)
        self.assertEqual(d["authors"][0]["churn"], 11)
        self.assertAlmostEqual(d["authors"][0]["ownership"], 1.0)

        f = mk_filter(author_id=alice["id"])
        self.assertEqual(self.summary(f)["commits"], 6)  # merged filter covers both


if __name__ == "__main__":
    unittest.main(verbosity=2)
