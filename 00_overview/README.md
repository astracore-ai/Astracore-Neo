# 00_overview — the engineering record

The complete record (spec review, spec v2.0/v2.1 baseline, product strategy, development plan, decisions,
and every drop's findings from 0.1 to 0.14) is the project document:

    https://claude.ai/code/artifact/7a5e2e87-f701-4bba-8a92-ab2a63af4df3

It exports to Word, PDF or Markdown from the document itself (about 100 KB of Markdown). The files in this
folder are the parts of it a receiving team needs on day one without opening it:

- `spec_sheet_v2_1.md` — the spec as implemented; since drop 0.39 every headline row carries a status tag (RTL-proven /
  planned / IP / model) and §0 is the claims ledger
- `spec_v2_1_summary.md` — the architecture and numbers at handoff, in one page, with the same tags
- `ROADMAP.md` — the phases from drop 0.38 to handoff (synthesis on an open PDK, chip top, sparsity engine, transformer
  readiness, power mechanics, sign-off-quality verification, physical proxy, software), with the designs of the sparsity
  engine, the vector unit and the chip top
- `decisions.md` — the nine program decisions, their status and dates
- `drop_log.md` — what landed in each drop, with the test that proved it
