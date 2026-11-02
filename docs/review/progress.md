# 2026-11 review: implementation ledger

One row per ticket of `docs/review/2026-11-triage.md`. Phase branches stack in this order:
`fix/review-2026-11-p0` → `-p1` → `-p2` → `-p3b` → `-p3` → `-p4` → `-p6` → `-p7` → `-p8` → `-p5`.
Per-phase plans and reports are in `docs/review/progress/`. Statuses: todo / in-progress / done / blocked / needs-decision / deferred.

| ticket | status | branch | commit | test evidence | notes |
|---|---|---|---|---|---|
| P0-1 | done | fix/review-2026-11-p0 | 4bf17a4 | tests/test_p0_oscillation.py: 6 of 7 failed before (+4 review-fix tests); real-data replay Thriller 25/Laufey: no second merge, 0 moves after merge→check→apply→check | breaker refuses manual Apply too (msg says 'automatically'); layout guard covers post-deploy merges only; residual races → P1-F4; breaker query unindexed → P1-F5 |
| P0-2 | done | fix/review-2026-11-p0 | a35ee7a | tests/test_p0_download_missing.py: 13 tests; dry run: bulk 'Download missing all' takes 51 of 303 (never tried) | skip_videos rewritten-id mismatch left to P1-B3 |
| P0-3 | done | fix/review-2026-11-p0 | b22f489 | tests/test_p0_plex_mirror.py (shifting page, missing totalSize, live-miss-before-readd, offline mount); 4 review fixes | POST /library/plex/empty-trash (api.py) still lacks the online check → P2-C8 |
| P0-4 | done | fix/review-2026-11-p0 | ed28579 | tests/test_p0_skips.py + test_skipped.py: 6 failed before; dry run: 'different album' 999→257 (225 cross-edition), Re-check 2,144→102 tracks, 0 Download-missing | hold off on Re-check until P1-F1 (102 old-gate tracks incl. '(feat. …)' singles) |
| P0-5 | done | fix/review-2026-11-p0 | 779d60b | tests/test_p0_skips.py: 4 failed before (real Worker._process_track) | DownloadDetails.jsx shows the skip reason in error colour (cosmetic) |
| P0-6 | done | fix/review-2026-11-p0 | 5e0a7cb | tests/test_p0_footguns.py: 3 failed before (real scan+sync_registry keeps manual=1; real httpx request has no token) | README still documents 'mdl index --rebuild' via exec; 'mdl library scan --full' has no app-running guard; library.py:609 unreadable counter bug → P2-D1 |
| P1-G4 | done | fix/review-2026-11-p0 | 918bb3e | tests/test_p0_plex_foreign.py; dry run: 0 album-level Plex edits on 451 shared units (5 before), 156 plex_foreign; flip-flops: Doggystyle 235578, ABBA 202038 | runner._check_unit still uses a foreign album's MBID as resolver hint (one-line follow-up); plex_foreign not applyable until P1-C5; Library.jsx TAB_FOR_KIND lacks plex_foreign |
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
- 2026-11-02: p0 integrated at 15299c6 (7 tickets, 4 lanes, 0 integration-fix attempts). Suite: Ran 451 tests OK; frontend build OK. §7 oscillation baseline on the copy: 0 rows (48 h). No migrations. Ready to deploy (user's call).
