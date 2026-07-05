---
schema: report_bundle.v1
project: pimc
agent: writing
run_id: 2026-06-20T1212_demo
created_at: 2026-06-20T12:12:00Z
data_pack: writing/report_data_pack.v1.json
deliverables:
  - kind: markdown
    path: writing/research_report.approved.md
    status: completed
    bytes: 1200
  - kind: excel
    path: writing/deliverables/results_workbook.xlsx
    status: completed
    bytes: 2048
  - kind: word
    path: writing/deliverables/research_report.docx
    status: completed
    bytes: 4096
  - kind: powerpoint
    path: writing/deliverables/research_deck.pptx
    status: completed
    bytes: 4096
source_refs:
  - execution/metrics.json
  - writing/research_report.approved.md
qa_status:
  status: passed
  checks:
    - name: excel.zip_structure
      status: passed
      detail: results_workbook.xlsx
generation_errors: []
---

# Report Bundle

该 manifest 记录 Writing Agent 生成的 Markdown、Excel、Word、PPT 产物、统一数据包、来源引用和 QA 状态。
