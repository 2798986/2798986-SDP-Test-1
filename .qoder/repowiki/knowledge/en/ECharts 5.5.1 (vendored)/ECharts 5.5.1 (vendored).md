---
kind: external_dependency
name: ECharts 5.5.1 (vendored)
slug: echarts
category: external_dependency
category_hints:
    - vendor_identity
    - client_constraint
scope:
    - '**'
source_files:
    - static/index.html
    - static/app.js
---

### ECharts
- Role: the only third-party library in this stdlib-only Python web app; vendored under `static/vendor/` and loaded from `index.html`.
- Integration point: `static/index.html` loads the bundled bundle; `static/app.js` calls the ECharts JS API to render the churn bar chart, top-files pie, author-share donut, and weekly line chart.
- Client constraint: the build is fully offline — no runtime CDN references are allowed; the vendor bundle must ship with the repo so the dashboard works without network access.
- Verify exact ECharts initialization / chart-option shapes against the official docs when extending charts.