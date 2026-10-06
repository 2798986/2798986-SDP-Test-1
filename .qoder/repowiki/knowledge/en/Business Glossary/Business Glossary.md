---
kind: business_term
name: Business Glossary
category: business_term
scope:
    - '**'
---

### RAT
- Definition：Repo Analysis Tool — the name of this project, a local offline dashboard that ingests Git repositories (zip containing `.git` or a public clone URL) and computes added/removed lines, growth, churn, modifications, churn rate and ownership per file/directory/repo/commit-set/author.

### churn
- Definition：The total number of modified lines (adds + removes) for a given scope (file, directory, author, commit set); used as both a metric tile and a filter dimension. Commits whose only change is binary files do not contribute churn.

### churn rate
- Definition：A normalized metric expressed as churn per commit (total churn divided by commit count), shown on the summary tiles alongside raw churn.

### ownership
- Definition：Per-file (or per-directory) attribution of who owns changes, derived from the Git history after ingesting a repository.

### topfiles
- Definition：The tab showing the most-changed files in the selected scope, rendered as a donut chart plus a table; one of the two columns at ≥1280px viewport width.

### share
- Definition：The tab showing author share of changes (who contributed how much), rendered as a donut chart plus a table; paired with topfiles at wide viewports.

### rail chips
- Definition：The left-side navigation/filter rail composed of chip-style controls (date range, pinned-commit selector, author filter) that drive the dashboard state.

### metric tiles
- Definition：The row of summary cards at the top of the dashboard (commits, +lines, -lines, churn, week-bucket count, churn rate); at ≥1280px they render 5+5 across, collapsing to fewer columns on narrower viewports.

### breadcrumb drill-down
- Definition：Navigation flow from a broader scope (repo) into a narrower one (directory → file) via clickable breadcrumbs, preserving the selected filters.

### identity merge
- Definition：The operation that collapses multiple Git author identities into a single canonical identity (e.g. merging Alice's different email/name variants), changing the resulting metrics and author counts.

### sample fixture
- Definition：The bundled demo repository packaged as `demo/fixture.zip`, shipped with the repo so users can load it without any network access; contains commits covering pure renames, rename+edit, binary deletions, merges, etc.
- Aliases：fixture

### cJSON
- Definition：The real-world public GitHub repository (`https://github.com/DaveGamble/cJSON.git`) used as a smoke-test target for cloning and full ingestion during verification.
