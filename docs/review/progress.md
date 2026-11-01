# 2026-11 review: implementation ledger

One row per ticket of `docs/review/2026-11-triage.md`. Phase branches stack in this order:
`fix/review-2026-11-p0` → `-p1` → `-p2` → `-p3b` → `-p3` → `-p4` → `-p6` → `-p7` → `-p8` → `-p5`.
Per-phase plans and reports are in `docs/review/progress/`. Statuses: todo / in-progress / done / blocked / needs-decision / deferred.

| ticket | status | branch | commit | test evidence | notes |
|---|---|---|---|---|---|
| P0-1 | todo | fix/review-2026-11-p0 | | | |
| P0-2 | todo | fix/review-2026-11-p0 | | | |
| P0-3 | todo | fix/review-2026-11-p0 | | | |
| P0-4 | todo | fix/review-2026-11-p0 | | | |
| P0-5 | todo | fix/review-2026-11-p0 | | | |
| P0-6 | todo | fix/review-2026-11-p0 | | | |
| P1-G4 | todo | fix/review-2026-11-p0 | | | |
| P1-F1 | todo | fix/review-2026-11-p1 | | | |
| P1-F2 | todo | fix/review-2026-11-p1 | | | |
| P1-F3 | todo | fix/review-2026-11-p1 | | | |
| P1-F4 | todo | fix/review-2026-11-p1 | | | |
| P1-F5 | todo | fix/review-2026-11-p1 | | | |
| P1-A1 | todo | fix/review-2026-11-p2 | | | |
| P1-A2 | todo | fix/review-2026-11-p2 | | | |
| P1-A3 | todo | fix/review-2026-11-p2 | | | |
| P2-A4 | todo | fix/review-2026-11-p2 | | | |
| P1-G1 | todo | fix/review-2026-11-p3b | | | |
| P1-G5 | todo | fix/review-2026-11-p3b | | | |
| P1-G2 | todo | fix/review-2026-11-p3b | | | |
| P1-G3 | todo | fix/review-2026-11-p3b | | | |
| P2-G6 | todo | fix/review-2026-11-p3b | | | |
| P2-G7 | todo | fix/review-2026-11-p3b | | | |
| P1-B1 | todo | fix/review-2026-11-p3 | | | |
| P1-B2 | todo | fix/review-2026-11-p3 | | | |
| P1-B3 | todo | fix/review-2026-11-p3 | | | |
| P1-B4 | todo | fix/review-2026-11-p3 | | | |
| P1-B5 | todo | fix/review-2026-11-p3 | | | |
| P1-B6 | todo | fix/review-2026-11-p3 | | | |
| P1-C1 | todo | fix/review-2026-11-p4 | | | |
| P1-C2 | todo | fix/review-2026-11-p4 | | | |
| P1-C3 | todo | fix/review-2026-11-p4 | | | |
| P1-C4 | todo | fix/review-2026-11-p4 | | | |
| P1-C6 | todo | fix/review-2026-11-p4 | | | |
| P1-C7 | todo | fix/review-2026-11-p4 | | | |
| P2-C8 | todo | fix/review-2026-11-p4 | | | |
| P1-C5 | todo | fix/review-2026-11-p4 | | | |
| P2-D1 | todo | fix/review-2026-11-p6 | | | |
| P2-D2 | todo | fix/review-2026-11-p6 | | | |
| P2-D3 | todo | fix/review-2026-11-p6 | | | |
| P2-D4 | todo | fix/review-2026-11-p6 | | | |
| P2-D5 | todo | fix/review-2026-11-p6 | | | |
| P2-D6 | todo | fix/review-2026-11-p6 | | | |
| P2-D7 | todo | fix/review-2026-11-p6 | | | |
| P2-D8 | todo | fix/review-2026-11-p6 | | | |
| P2-D9 | todo | fix/review-2026-11-p6 | | | |
| P2-D10 | todo | fix/review-2026-11-p6 | | | |
| P3-D11 | todo | fix/review-2026-11-p6 | | | |
| P3-D12 | todo | fix/review-2026-11-p6 | | | |
| P2-E1 | todo | fix/review-2026-11-p7 | | | |
| P2-E2 | todo | fix/review-2026-11-p7 | | | |
| P2-E3 | todo | fix/review-2026-11-p7 | | | |
| P2-E4 | todo | fix/review-2026-11-p7 | | | |
| P3-E5 | todo | fix/review-2026-11-p7 | | | |
| P3-E6 | todo | fix/review-2026-11-p7 | | | |
| P3-E7 | todo | fix/review-2026-11-p7 | | | |
| P3-E8 | todo | fix/review-2026-11-p7 | | | |
| P2-H4 | todo | fix/review-2026-11-p8 | | | |
| P3-H2 | todo | fix/review-2026-11-p8 | | | |
| P3-H3 | todo | fix/review-2026-11-p8 | | | |
| P3-H1 | todo | fix/review-2026-11-p8 | | | |
| P1-R1 | todo | fix/review-2026-11-p5 | | | |
| P1-R2 | todo | fix/review-2026-11-p5 | | | |
| P1-R3 | todo | fix/review-2026-11-p5 | | | |
| P1-R4 | todo | fix/review-2026-11-p5 | | | |
| P1-R5 | todo | fix/review-2026-11-p5 | | | |
| P1-R6 | todo | fix/review-2026-11-p5 | | | |
| P1-R7 | todo | fix/review-2026-11-p5 | | | |
| P1-R8 | todo | fix/review-2026-11-p5 | | | |

## Phase log

- 2026-11-02: p0 branch created off `main` (0fec8a2); triage + prompt committed. Baseline suite: 394 tests OK.
