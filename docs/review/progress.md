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
| P1-F1 | done | fix/review-2026-11-p1 | 02f9565 | tests/test_p1_identity.py + identity_golden.py (127 rows); 24 failed before; parity on 457 albums: 452 identical, 4 diffs all F1 (split_key re-key, near-identical titles); gate 'have' 2,307→2,143; picker 7,909→7,767 picks | User 2026-11-04: keep edition-blind gate + one-way picker version rule; revisit the 127 dropped live/acoustic/remix takes in P1-B4; rockstar names-only is a known gap; Beatles Anthology 30 exact-title copies re-download |
| P1-F2 | done | fix/review-2026-11-p1 | a188607 | tests/test_p1_unit_identity.py (dismiss→move→recheck, accept→move into key, artist merge); migration v12 on copy: 4.4 s, 0 decisions lost, 2,514 dismissals re-keyed kind:unit_id | v12 is ONE-WAY: take a .backup before deploy. User 2026-11-04: accepted; undo-merge restoring decisions built in P1-A1. 11 units re-keyed by F1 → ~10 dismissed issues re-open once (user 2026-11-04: accepted, will re-dismiss). 163 pre-existing orphaned dismissals → phase 5 |
| P1-F3 | done | fix/review-2026-11-p1 | 8118e4b | tests/test_p1_relocate.py (property: every path resolves case-insensitively after each mover); migration v13 2.9 s; baseline: tracks.library_path unresolved 1,167 / 18,545 | app/relocate.py (not library/relocate.py: library.py is a module). merge_artist_folders dest spelling (unconfirmed, low). jobs.target_dir/plex_items follow only emptied folders |
| P1-F4 | done | fix/review-2026-11-p1 | dc102dd | tests/test_p1_settling.py (import into X while move of X planned → move deferred, import lands in current folder) | settling misses a plain album download's first track (P1-B6); API cancel/delete doesn't call job_ended (1 h fallback); folder locks held through do_scan Plex re-add (~7 min) |
| P1-F5 | done | fix/review-2026-11-p1 | a9b8ba2 | tests/test_p1_transactions.py; migration v14 2.4 s; EXPLAIN now uses indexes for ours_locked, P0-1 breaker, match_flipflop | User 2026-11-04: accepted deviation, tracks(library_path) index not added (updates by id; lookups use path_key). sync_registry is one 5-10 s transaction per cycle: watch UI responsiveness |
| P1-A1 | done | fix/review-2026-11-p2 | e4e9fab | tests/test_p2_issue_states.py: 13 failed before; migration v15 0.014 s (DDL + triggers); parity 457/457 plans identical; §7 came-back query 0 on the copy | md_issue_events + triggers; new states failed/superseded/cleared/tried; undo-merge restores decisions; breaker message fixed. User 2026-11-04: §7 came-back target is 'target 0, read any row' + watch applied->failed |
| P1-A2 | done | fix/review-2026-11-p2 | 9b4abb3 | tests/test_p2_converge.py: 5 failed before; 5-cycle stress test: cycle 2+ change nothing; 0 albums with >3 batches/field in 7 days | DMX/R.E.M. junk label, copies the merge would leave, track_order applied only after its Plex part |
| P1-A3 | done | fix/review-2026-11-p2 | 69739fb | tests/test_p2_dup_actions.py (12); suite 811 s → 290 s after faking mb.search_artists in its services() | User 2026-11-04: accept ~13.5 s re-check per duplicate-manager call; fix bulk-action cost in P2-D3 (phase 6). merge_artist_folders spelling fixed via relocate.on_disk |
| P2-A4 | done | fix/review-2026-11-p2 | 2e0536c | frontend build OK; screenshots .cache/claude-sandbox/p2-sidebar.png, p2-review.png; sidebar count = open+failed, in progress/tried beside it | User 2026-11-04: count open + failed. plex_foreign tab, 'Download ran' filter label |
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
- 2026-11-02: p1 integrated at b93d41c (5 tickets, chain of 4 lanes; resumed once after a session restart). Suite: Ran 533 tests OK; frontend build OK. Migrations v12-v14: 9.7 s on the copy, idempotent. 4 needs-decision items asked at phase end.
- 2026-11-04: p2 integrated at 53816be (4 tickets, 2 lanes). Suite: Ran 574 tests in 290 s OK after the test-network fix; frontend build OK. Migration v15 0.014 s. Phase 1 and 2 decisions recorded (all recommended options). User asked to deploy p0-p2 before any more code; p3b parked (brief in .cache/claude-sandbox/work/p3b-args.json).
