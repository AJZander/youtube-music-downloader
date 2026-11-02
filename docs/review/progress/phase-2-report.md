# Phase 2 report: issues that close when they're fixed

Integrator's report for `fix/review-2026-11-p2`, which started at `263261c`
(phase 1's integrated tip plus the ledger). The code tip is `9055e30`. The
commit that adds this report and the plan sits on top of it. The checks ran on
2026-11-02 (14:56Z onwards) against the shared copy
`.cache/claude-sandbox/mdl-p2.db` (schema v11, the live app's pre-phase-1
schema; its newest `md_changes` row is 2026-11-01T22:32:16Z). All counts are
sizes, not oracles, because the live library keeps changing.

**Result:**
- All 4 tickets are done and integrated: P1-A1, P1-A2, P1-A3 and P2-A4.
- Backend: `Ran 574 tests in 811.243s OK` (533 at the phase start, 41 new).
  Frontend build OK. The suite was slower because one new test module reached
  an unfaked MusicBrainz call and backed off offline (test-only, section 5
  item 10). Fixed since: `Ran 574 tests in 289.592s OK`.
- One migration, v15 (P1-A1): DDL only, 0.014 s on the 1.4 GB copy. A start
  from v11 runs v12-v15 in about 9 s. No new settings, no new dependencies, no
  `RULES_VERSION` bump.
- One conflict, in `runner._check_unit`'s gone path; both lanes' intent is kept.
- Every phase check passes: the §7 "came back" query is 0 on the migrated copy
  and after a full check pass; no album has more than 3 batches of one field
  in the last 7 days; the sidebar drops queued rows and shows Tried beside
  the count; the §7 oscillation query is 0 rows, as in phases 0 and 1.
- Three items need the lead's decision (section 6). None blocks the merge of
  the phase branch.

---

## 1. Tickets and commits

There was one conflict (below). Every lane's fixup commits were folded into
their ticket's commit, and each folded commit got one "Review fixes: …" line.
After lane a, the index was identical to `lane/p2-a`. After lane b, the tip
differs from `lane/p2-a` only in lane b's six files, and in `runner.py` only
by the resolved hunk; lane b's other five files are identical to
`lane/p2-b`.

| ticket | lane | commit on `fix/review-2026-11-p2` | lane commits folded in |
|---|---|---|---|
| P1-A1 Honest issue states | a | `e4e9fab` | `3057b6d` + fixup `66d8301` |
| P1-A2 Fixes that converge | a | `9b4abb3` | `4663fe1` |
| P2-A4 The UI reflects the true state | a | `2e0536c` | `3c1d01e` + fixup `f80ab7a` |
| P1-A3 Duplicates-manager actions update issues at once | b | `9055e30` | `17958dc` + fixup `b723329` |

- **Order.** The integration order rule for this run is lanes a then b, each
  in its lane order, so A4 sits before A3. §6 and the plan list A1 → A2 → A3
  → A4. The two lanes' files are disjoint except for the one hunk below, so
  the tip's tree is the same in either order. Each intermediate commit holds
  only its own lane's tickets.
- **The conflict.** Lane b's fixup (`b723329`) changed `runner._check_unit`,
  which the plan had left to lane a. A gone album whose files are in Trash now
  keeps its `md_albums` record, so an immediate Restore gets its unit id and
  decisions back. Lane a's A1 had changed the same lines to close the album's
  issues as `superseded`, "the album is gone". The resolution keeps both:
  ```python
  if not await library.in_trash(units.unit_folder(key)):
      await db.execute("DELETE FROM md_albums WHERE key = ?", (key,))
  await issues.save([], scope_kinds=issues.UNIT_KINDS, album_key=key,
                    close_as="superseded", reason="the album is gone")
  ```
  After a Restore, the re-check plans the issues again and `superseded`
  becomes `open`; a dismissal is never closed by close-stale, so it stands.
  The A3 commit's "Review fixes" line records the resolution.
- **Commit messages.** Besides the "Review fixes" lines, the statements the
  fixups made untrue were corrected in place:
  - P1-A1: the re-open rule now says an applied issue fails only when it is
    planned as it was (`_same_problem`), and otherwise opens as a new
    problem; `missing.refresh` closes a gap filled by our download with
    `applied_at` and `attempts + 1`; the moves' journal batch is the first
    pair the journal has, in any field (`_move_batch`).
  - P1-A3: `_merge_artists` respells only the album's name, not the target
    artist.
- `git cherry-pick -n` stopped on the conflict with its sequencer state set.
  The conflicting pick was the last in the list, so after the commit
  `git cherry-pick --quit` cleared it. Nothing was skipped.

Files changed in the phase (`git diff --stat 263261c..9055e30`: 32 files,
+2,447/−263):
- new backend module `app/metadata/recheck.py`;
- backend `app/{api,db,library,relocate}.py`, `app/engine/tagio.py` and
  `app/metadata/{apply,desired,issues,missing,routes,runner,units,views}.py`;
- 4 new test modules, and fixture updates in `test_metadata`,
  `test_p0_download_missing`, `test_p1_relocate`, `test_p1_transactions`,
  `test_p1_unit_id` and `test_library`;
- frontend `components/{JobList,Library,NeedsReview,SkippedList}.jsx` and
  `components/library/{AttentionSidebar,DetailPane,IssueList}.jsx`,
  `components/library/issues.js`.

The diff adds no `purge_trash`, `.mdl-trash`, `unlink`, `rmtree` or
`os.remove` call to app code.

## 2. Tests added (41)

Each lane recorded that its new tests failed on its starting tip, and each
reviewer reproduced it. The review-fix tests failed on the lane's commit
before the fixup.

- **P1-A1**, 18 tests (13 + 5 from review), `tests/test_p2_issue_states.py`:
  - ReDetectTestCase: `test_a_fix_that_does_not_stick_fails_instead_of_reopening`,
    `test_a_failed_fix_is_not_applied_automatically`,
    `test_a_problem_found_after_the_window_is_new`.
  - `EventsTestCase.test_every_state_change_is_recorded`.
  - SupersedeTestCase: `test_a_merge_into_another_album_supersedes_and_the_keeper_keeps_the_dismissal`,
    `test_an_album_whose_files_are_gone_is_superseded_with_iso_time`.
  - `UndoMergeTestCase.test_undoing_a_merge_gives_the_album_back_its_decisions`.
  - TriedTestCase: `test_already_tried_goes_to_the_tried_bucket`,
    `test_retry_takes_it_out_of_the_bucket`, `test_a_new_gap_reopens_it`.
  - `BreakerWordingTestCase.test_the_refusal_says_an_apply_by_hand_is_refused_too`.
  - MigrationTestCase: `test_v15_adds_the_columns_table_and_triggers_once`,
    `test_a_cut_off_v15_leaves_nothing`.
  - From review: `NewProblemTestCase.test_a_new_file_after_the_fix_is_a_new_problem`,
    `ArtistMergeUndoTestCase.test_undoing_an_artist_merge_gives_the_folded_album_back`,
    TriedTestCase `test_a_gap_filled_by_our_download_is_applied_and_timed`,
    `test_a_filled_gap_found_again_within_a_day_failed` and
    `test_a_new_gap_after_a_fill_is_a_new_problem` (a guard).
  - Fixture updates: `test_metadata` 1236-1262 expects `superseded`;
    `test_p1_unit_id`: ORIG gets its own unit id back after the undo;
    `test_p0_download_missing`: the already-tried test also sees `tried`;
    `test_p1_relocate.test_an_artist_merge_can_be_undone`: the batch also
    holds the `('unit', 'merge')` row; `test_p1_transactions` IndexTestCase:
    `MOVE_BATCH` uses an index.
- **P1-A2**, 5 tests, `tests/test_p2_converge.py`:
  `ConvergeTestCase.test_five_auto_apply_cycles_change_nothing_after_the_first`
  (real executors, 5 cycles, and no issue may end `failed`),
  `LabelTestCase.test_a_label_the_writer_calls_junk_is_never_wanted`,
  CopiesTestCase `test_copies_the_merge_would_leave_are_not_an_issue` and
  `test_a_set_says_what_the_merge_would_do`,
  `TrackOrderTestCase.test_track_order_is_applied_once_its_plex_part_has_run`.
  Fixture update: `test_metadata` ApplyPassesTestCase's `_apply_simple`
  stand-in takes keyword arguments.
- **P2-A4**, 6 tests, `tests/test_p2_attention.py`: CountsTestCase
  `test_the_count_is_what_needs_the_user`, `test_a_group_under_way_still_shows`,
  `test_select_all_counts_what_apply_takes`,
  `test_dismiss_all_takes_only_what_the_list_shows`,
  `test_accept_incomplete_takes_the_tried_bucket`, and
  `FilePassTestCase.test_issues_are_published_after_the_file_pass`. The
  frontend has no test runner: the build check, three screenshots and a
  scratch script for the review fix stand in.
- **P1-A3**, 12 tests (7 + 5 from review), `tests/test_p2_dup_actions.py`,
  all in DupActionsTestCase: `test_trashing_a_duplicate_closes_its_issue_at_once`,
  `test_a_recheck_applies_nothing_and_leaves_auto_fixes_to_the_cycle`,
  `test_not_duplicates_closes_the_group_at_once`,
  `test_a_restore_reopens_what_the_trash_closed` (amended by review: the
  record is kept through the Trash),
  `test_an_artist_merge_closes_the_split_and_moves_the_albums_issues`,
  `test_an_artist_merge_files_into_the_targets_spelling`,
  `test_albums_past_the_cap_are_only_marked_due`; from review
  `test_a_restore_keeps_what_was_decided_about_the_album`,
  `test_a_restore_keeps_a_dismissal`,
  `test_an_artist_merge_of_another_spelling_moves_its_files`,
  `test_an_artist_merge_leaves_the_target_itself_alone`,
  `test_undoing_not_duplicates_reports_the_group_again_at_once`. Lane b's
  tests assert "not in `('open', 'queued', 'failed')`", never a state name,
  so they hold with A1's new closing states. Fixture update: `test_library`
  MergeArtistsTestCase stubs `db.fetchone`.

## 3. Suite and frontend build (integrated tip)

- Backend: the integration worktree's `backend` was mounted read-only, with
  `--network none`, and `-m unittest discover tests` ran on the tree of
  `9055e30`. Result: **`Ran 574 tests in 811.243s OK`**, with 0 errors and
  0 failures.
  - 574 = 533 at the phase start, plus A1 18, A2 5, A4 6 and A3 12.
  - Lane a alone ran 562 OK and lane b alone 545 OK.
  - The suite takes 811 s, against 216 s at the phase start. Nearly all of it
    is `test_p2_dup_actions` (536 s alone for 12 tests), idle: its re-checks
    run `check_artist`, which calls `mb.search_artists`, and the shared
    harness (`test_p0_oscillation.LibraryTestCase.services()`) doesn't fake
    that one. Offline, the real client backs off 2 + 4 + 8 + 16 = 30 s per
    call (`musicbrainz.py:93`, found by wrapping `asyncio.sleep`; one such
    test takes 33 s, 30 of them there). Test-only, and fixed since: see
    section 5 item 10.
- Frontend: files changed in this phase (A4), so the build check ran:
  `docker build --no-cache -t mdl-web-sandbox <wt-integration>/frontend`.
  It passed: `✓ 1084 modules transformed`, `✓ built in 8.14s`, exit 0. The
  only warning is vite's existing chunk-size warning.

## 4. Phase checks (dry runs, read-only, on `mdl-p2.db`)

Scripts and outputs are in `.cache/claude-sandbox/work/p2-integration/`
(`scripts/`, `*.out`). Every container ran with `--network none` and the shared
copy mounted `:ro`, then backed up into the container's `/tmp`. The library
was mounted `:ro` only for (b)'s check pass. `PLEX_TOKEN` was empty and every
write flag was passed `=false`. (c) and (e) are plain SQL on the copy, opened
`mode=ro` from the host.

### (a) Migrations on a `/tmp` copy (`scripts/checks.py`)

Each migration was run on its own start (`MIGRATIONS[:n]`), on the integrated
tip's code:

| step | seconds |
|---|---|
| v12 (phase 1, P1-F2) | 4.16 |
| v13 (phase 1, P1-F3) | 2.39 |
| v14 (phase 1, P1-F5) | 2.52 |
| **phase 1 total** | **9.06** |
| **v15 (phase 2, P1-A1)** | **0.014** |
| a second start | 0.006 |

- One start from v11 (v12-v15 together, `scripts/counts.py`): 9.17 s. The
  base code's own v12-v14 on the same copy: 8.37 s.
- `md_issues` per state, before (v11) and after (v15):

  | state | v11 | v15 |
  |---|---|---|
  | applied | 52,968 | 52,968 |
  | dismissed | 3,249 | 3,249 |
  | open | 1,149 | 1,149 |
  | failed | 1 | 1 |
  | queued, tried, superseded, cleared | 0 | 0 |
  | **rows** | **57,367** | **57,367** |

- v15 adds `md_issues.attempts` (0 on every row) and `applied_at` (NULL on
  every row), and `md_issue_events` (0 rows) with its triggers
  `md_issues_event_insert` and `md_issues_event_state`. No issue id is gone.
- The 9,029 `applied` rows in `datetime('now')`'s format keep it (P1-R8 finds
  them by it).
- 19,795 rows changed their key between v11 and v15. That is exactly v12's
  re-key of live unit issues (phase 1's figure); no row changed state.

### (b) §7 "issues that came back" against the new state history

The query for the user to run after deploy (`sqlite3` 3.25 or later; the host
has 3.50). It takes the deploy time from the v15 row in `schema_migrations`,
so there is no parameter to fill in:

```sql
-- Issues that came back: re-opened from applied within 24 h of the apply,
-- since the v15 migration (the deploy). Target 0; read any row (below).
SELECT i.kind, COUNT(*) AS came_back, SUM(i.state = 'open') AS open_now
FROM (SELECT issue_id, at, from_state, to_state,
             LAG(at) OVER (PARTITION BY issue_id ORDER BY id) AS applied_at
        FROM md_issue_events) e
JOIN md_issues i ON i.id = e.issue_id
WHERE e.from_state = 'applied' AND e.to_state = 'open'
  AND e.applied_at > strftime('%Y-%m-%dT%H:%M:%SZ', e.at, '-24 hours')
  AND e.at >= (SELECT applied_at FROM schema_migrations WHERE version = 15)
GROUP BY i.kind;

-- Fixes that didn't stick: the honest form of the same. Watch it, don't
-- target 0: each one is shown as failed, with its reason, and not retried.
SELECT i.kind, COUNT(*) AS n
FROM md_issue_events e JOIN md_issues i ON i.id = e.issue_id
WHERE e.from_state = 'applied' AND e.to_state = 'failed'
  AND e.at >= (SELECT applied_at FROM schema_migrations WHERE version = 15)
GROUP BY i.kind;
```

- `LAG(at)` is the event that put the issue in `applied`. An issue applied
  before the migration has no such event, so its re-open isn't counted. Those
  are the old rows with `applied_at` NULL, which re-open as they did before.

Results:

| run | came back | didn't stick |
|---|---|---|
| on the migrated copy (history starts empty) | 0 | 0 |
| after one registry pass and a plan-only check of 457 albums | 0 | 0 |
| after a synthetic case on the `/tmp` copy: one issue applied and re-opened at once, one applied and failed, one old applied row re-opened | 1 (`missing_tracks`, open now 1) | 1 |

- The check pass is phase 1's parity sample (every album of Hilltop Hoods,
  Paul Kelly, The Devil Wears Prada, Snoop Dogg, Michael Jackson, Laufey,
  Slipknot and Wiz Khalifa, plus `md5(key) % 45 == 0`): one `sync_registry`
  (8.7 s; 19 new units, 11 gone, as in phase 1), then `check_unit` for 457
  albums (69.9 s, 0 errors; 321 resolved, 113 unresolved, 23 review, the
  same as phase 1's p1 side). MusicBrainz from `meta_cache` only, Plex faked,
  auto-apply and `request_apply` patched out, nothing applied. As in phase 1,
  the write flags came in false and were set true in memory only, so the
  planner plans file and Plex fixes as the live app does; no executor ran,
  and the library mount was read-only.
- Its transitions, from `md_issue_events`, are the same as lane a's A1 dry run:
  (new) → open 56; open → cleared "no longer found" 16; applied → open 11
  (old rows, `applied_at` NULL, re-opened as before, not counted);
  open → superseded "the album is gone" 2. No `failed` from re-detection.
- The synthetic case shows the query counts what it should: the applied →
  open within the window, and not the old row.
- **What a non-zero "came back" means now.** A1's review fix (`_same_problem`)
  sends a re-detection within 24 h to `failed` only when it is planned as it
  was. Planned with something new under the same key (a new download's file
  needing tags, a new gap), it is a new problem: `open`, and auto-apply takes
  it. So "came back" can be non-zero without a loop. A row there needs a look:
  a new problem under the same key is fine; a fix whose detail drifts each
  pass is a loop the 24 h guard can't see. See 6.3.

### (c) P1-A2 scope: albums with more than 3 batches of one field (`scripts/scope.sql`, `scripts/scope2.sql`)

Windows end at the copy's newest `md_changes` row (2026-11-01T22:32:16Z). A
batch is a distinct `batch_id` per album key, target and field.

| target/field | last 7 days | last 30 days (albums, worst) | any 7-day window in the last 30 days (albums, worst) | of those, repeated across cycles (span ≥ 1 h) | last repeated batch |
|---|---|---|---|---|---|
| plex/genre | 0 | 679, 72 | 672, 72 | 671 | 10-26 06:22 (Courtney Barnett *Mantis/Sugar Plum*: 3 batches on 10-19, then this one) |
| file/tags | 0 | 56, 23 | 62, 22 | 59 | 10-22 22:45 (DMX *Mixtape*) |
| move/file | 0 | 15, 7 | 20, 12 | 10 | 10-22 22:30 (Michael Jackson *Thriller*, the *Thriller 25* copies) |
| plex/match | 0 | 14, 6 | 14, 5 | 14 | 10-21 05:42 |
| move/sidecar | 0 | 6, 5 | 3, 5 | 3 | 10-22 03:22 (ABBA *Ring Ring [2001 Remaster]*) |
| trash/files | 0 | – | 3, 4 | 0 (one pass) | – |
| plex/index | 0 | 1, 6 | 1, 6 | 0 (one pass) | – |
| plex/mood | 0 | – | 1, 4 | 0 (one pass) | – |
| move/leftover | 0 | 1, 4 | – | – | – |

- **No album has more than 3 batches of one field in the last 7 days, and no
  7-day window that starts on or after 10-23 has more than 3.**
- Since 10-23 the most any album/field has is 3: Bob Dylan *More Blood, More
  Tracks* move/file (10-26 to 11-01), which the P0-1 breaker now refuses. The
  rest are pairs: Arctic Monkeys *Favourite Worst Nightmare*, Hilary Duff
  *Holiday Things* (imports retagged once more), Holly Clausius *Your Move*
  (the resolver mis-identification), and Snoop Dogg *Doggystyle*'s plex/match
  on 11-01 (P1-G4's guard is in the base, `918bb3e`).
- Plex genre batches with no album key (artist genres) are outside the
  query: 16 since 10-24, one each for 16 different artists, so not a loop.
- A window "in one pass" has all its batches within an hour, e.g. Depeche
  Mode *Remixes 2: 81-11* with 6 move batches in one second on 10-21 (the
  live attention clean-up that day). These are bursts, not loops.

Classes and what covers them:

| class | cause | covered by |
|---|---|---|
| plex/genre, 671 albums (worst Peking Duk *Paradise*, 72) | the genre loop | fixed on 10-21, before this review; nothing since 10-23 |
| file/tags: DMX *Mixtape* (22), R.E.M. *Live in Greensboro* | a label the writer strips as junk | **P1-A2** (label rule) |
| file/tags: the other 57 | the pre-10-21 tag loops | fixed on 10-21; nothing since 10-23 |
| move/file, move/sidecar, move/leftover, trash/files | the merge ↔ move oscillation, and the 10-21 clean-up's bursts | P0-1's breaker (phase 0); **P1-A2** stops re-planning copies the merge leaves (*Thriller 25*, *My Head Is an Animal*, *Dreaming Out Loud*) |
| plex/match, 14 albums | the Doggystyle/ABBA flip-flop | P1-G4 (in the base) |
| plex/index, 1 album (R.E.M. *at the BBC*, 6 batches in 11 s) | one renumbering pass in parts | not a loop. **P1-A2** keeps a two-stage `track_order` queued until its Plex part runs |
| any class not yet found | | **P1-A1**: a fix planned again as it was within 24 h becomes `failed` and auto-apply doesn't retry it |

Nothing in the journal is a live loop the phase leaves open.

### (d) Sidebar counts, old code (`263261c`) vs new (`scripts/counts.py`)

Each tree ran on its own `/tmp` copy, migrated by its own `db.connect()`, and
`views.attention()` was read: the sidebar's total and chips. Then the same
rows (lowest ids) were seeded on each copy, and the counts read again: 3
`missing_tracks` and 2 `move_conflict` → `queued`, 4 more `missing_tracks` →
`tried`, 2 `extra_tracks` → `failed`.

| | states: open / failed / queued / tried | old sidebar total | new sidebar total | new in progress | new tried |
|---|---|---|---|---|---|
| the migrated copy | 1,149 / 1 / 0 / 0 | 1,150 | 1,150 | 0 | 0 |
| seeded | 1,138 / 3 / 5 / 4 | 1,146 | **1,141** | 5 | 4 |

- On the copy itself the totals are equal, because it holds no `queued` or
  `tried` row (none was queued when it was taken, and `tried` is new).
- Seeded, the old total counts open + failed + queued (1,138 + 3 + 5), and the
  4 `tried` rows vanish from it. The new total is open + failed
  (1,138 + 3): **it excludes queued and tried**, which show beside it
  ("5 in progress · 4 tried"). Per chip: Missing tracks 299 old → 296 new
  with 3 in progress and 4 tried; Move blocked 10 → 8 with 2 in progress.
- `issues.counts()` (library.issues, the overview, the artist badges) gives the
  same: `open` 1,141, `in_progress` 5, `tried` 4.
- **The new count includes `failed`** (3 here: 1 on the copy, 2 seeded). That
  is the plan's default 4: a failed fix needs the user, and nothing retries it.
  The open-only figure is 1,138. This phase's check asked for a count that
  excludes failed; see 6.1.
- 252 open `missing_tracks` issues are in the "Download ran" filter (a
  download was queued for them before). They stay open until the user asks
  for Download missing again, which moves each one with nothing left to fetch
  to Tried at once.

### (e) §7 merge/move oscillation query

The query as written ran at 2026-11-02T14:56Z. **0 rows**, the same baseline
as phases 0 and 1.
- The 48 h window holds 2 merge batches, one per album:
  - Bob Dylan *More Blood, More Tracks … (deluxe edition)* at 11-01 20:46:38;
  - *Thriller 25 [2008 Reissue]* at 11-01 20:46:39.
- Anchored at phase 1's run time (11-02 11:58): 48 h 0 rows (2 batches);
  7 days 0 rows (2 batches); 14 days 18 albums with 39 batches among them,
  and 196 merge batches in all. The same as phases 0 and 1.

Target after deploy: 0 rows over 48 h.

## 5. Review findings

**Fixed (7, all folded into their ticket's commit):**

| ticket | finding | fix (lane commit) |
|---|---|---|
| P1-A1 (high) | `issues._replanned` turned any re-plan within 24 h of an apply into `failed` "still there after the fix" without comparing the detail. A new problem under the same key, such as a Download missing's new file needing tags, was then never auto-applied. | `_same_problem`: it fails only when planned as it was (the same detail; for `missing_tracks`, nothing newly missing). Anything else is `open`, as on the base (`66d8301`) |
| P1-A1 (low) | `relocate` looked up the moves' journal batch from the first pair only, and `MOVE_BATCH` matched only field `file`. An artist merge whose first moved file was a booklet (`leftover`) folded the album in a batch of its own, and Undo didn't restore it. | `_move_batch` takes the first pair the journal has; `MOVE_BATCH` matches any move field (`66d8301`) |
| P1-A1 (low) | `missing.refresh` closed a gap filled by our download as `applied` without `applied_at`, so the same gap found again went applied → open and counted in §7. | It sets `applied_at` and `attempts + 1`, as `_mark(ran=True)` does (`66d8301`) |
| P2-A4 (low) | The Review dialog's SSE reload asked for every row shown in one request; past 1000 rows the API answered 422, and the dialog stopped following the runner. | `issues.readRows` reads 1000 at a time (`f80ab7a`) |
| P1-A3 (medium) | The re-check right after a Trash deleted the album's record, so an immediate Restore re-registered it under a new unit id and lost ignore, manual, no_release, overrides and dismissals. | A gone album whose files are in Trash keeps its record; the cycle's registry pass still drops it (`b723329`) |
| P1-A3 | `_merge_artists` respelled the target artist too with `relocate.on_disk`, which could name the source's own folder: every file was then "already there" and the album went to Trash. | Only the album name is respelled; a source that is the target's own folder in another spelling is skipped and reported (`issues.same_file`) (`b723329`) |
| P1-A3 | DELETE `/library/exclusions` (undo "not duplicates") ran no re-check, so the group stayed out of Needs attention until the next cycle. | It re-checks the decision's folders, as adding one does (`b723329`) |

**Rejected:** none.

**Unconfirmed, still in the tip's code (lane b's reviewer, low):**
- `metadata/recheck.py:36`: each of the four duplicates-manager calls now waits
  for a full duplicate analysis, because every exclusion, Trash or move
  changes `_analysis_token`. That is 13.5 s per the live log.
- TrackDupes' bulk Trash then calls "not duplicates" once per group that keeps
  2 or more copies, one call after another (`TrackDupes.jsx:156-170`). A
  "Trash all" on 40 groups, 20 of which keep 2 copies, would take about
  21 × 13.5 s, roughly 5 minutes, while the dialog shows busy.
- The figure is computed from the traced calls and the logged analysis time,
  not measured. The implementer disclosed the cost. See 6.2.

**Still open.** These are not phase 2 defects against the acceptance. They are
for the lead to schedule.

1. **Trash → cycle → Restore** still loses the album's record, as on the base:
   the cycle's `sync_registry` drops gone records. Only an immediate Restore
   keeps it now. Keeping records until the Trash is purged would be a broader
   P1-F2 decision (lane b's fixer).
2. A whole-album Trash leaves the album's `missing_tracks` issue open until
   the next cycle, as on the base: `missing_tracks` is not in `UNIT_KINDS`.
3. An artist with no files left keeps its `md_artists` row (unchanged, outside
   P1-A3).
4. *Thriller 25* stays planned on the copy, against the plan's expectation:
   its merge would move 4 of the original's files in (each filling a gap,
   replacing nothing) and leave 5. After that merge it is planned no more
   (lane a's A2 dry run).
5. A MusicBrainz error in the copies planner closes the set. Pre-existing,
   not A2's.
6. Right after an artist merge, the moved albums show "not in Plex" until Plex
   rescans and the next cycle syncs (2 on lane b's dry run). The same as
   before, one cycle sooner.
7. `api.js`'s selection comment doesn't mention `state`, which passes through
   (A4, cosmetic; `api.js` was outside the lane).
8. At A1's commit alone, a two-stage `track_order` goes applied → failed →
   applied within one apply. A2, the next commit, fixes it. Ship the phase
   whole.
9. `md_issue_events` grows one row per state change. Its retention belongs to
   P2-D5 (say 180 days, as for `md_changes`).
10. **Test hygiene (integrator, low, test-only), fixed:** `test_p2_dup_actions`
    reached the real MusicBrainz client through `recheck → check_artist →
    desired.build_artist → mb.search_artists`, which `services()` doesn't
    fake. In the sandbox (`--network none`) each call backed off 30 s, so the
    suite went from 216 s to 811 s. Run with a network, these tests would have
    queried musicbrainz.org, against the plan's rule that tests fake it.
    Fixed in P1-A3's commit: `DupActionsTestCase.services()` fakes
    `mb.search_artists` too (`[]`, what the offline client's give-up already
    led to, so no assertion changed); `test_p0_oscillation.services()` is
    unchanged. With every outbound connection refused and recorded, the
    module made 9 such calls and the rest of the suite none; after the fix,
    none. The module takes about 25 s (536 s before) and the suite
    `Ran 574 tests in 289.592s OK`. In production a
    reachable MusicBrainz answers in about a second and the cache serves
    known artists. Only while MusicBrainz is unreachable would each artist
    check in a re-check back off up to 30 s before the handler returns (the
    cycle's artist checks already do the same).

Carry-overs from phases 0 and 1 that this phase closes:
- phase 1's 6.1 (undoing a registry merge didn't restore the album's
  decisions): P1-A1 journals `merge_into` and replays it on Undo;
- phase 1's unconfirmed `merge_artist_folders` spelling finding: P1-A3;
- phase 0's open item 1 (the breaker's wording): P1-A1;
- phase 0's items 10 (`TAB_FOR_KIND` lacked `plex_foreign`) and 22 (the
  `tried` filter's help text): P2-A4.

## 6. Needs-decision

The planner held none back, and every lane finished with status done. These
three came out of the reviews and this integration. None blocks the merge.

**6.1 P2-A4: does Needs attention count `failed`?** (integrator)
- Evidence:
  - The triage's P2-A4 asks for "open only" in the sidebar, with in progress
    and tried beside it; P1-A1 says to show a fix "as needing attention with
    the reason" after 2 failed attempts. This phase's check (d) asked that the
    new count exclude queued, failed and tried.
  - The plan's default 4 counts `failed` from the first failure
    (`issues.ATTENTION_STATES = ("open", "failed")`), shows the reason and
    "failed ×N", and auto-apply never retries it. The lane built that, and
    its review accepted it.
  - On the copy that is 1 issue (1,150 with it, 1,149 without); seeded, 3.
- **Recommended:** keep the default. A failed fix is something only the user
  can move on; leaving it out of the count would hide it, since nothing
  retries it. If the lead wants open only, it is a one-line change
  (`ATTENTION_STATES = ("open",)`) plus a "N failed" line beside the chip, as
  for in progress.

**6.2 P1-A3: the re-check's cost on bulk duplicate actions** (lane b's
reviewer, unconfirmed, low).
- Evidence: section 5. Each call pays one duplicate analysis (13.5 s live), and
  TrackDupes' bulk Trash makes one "not duplicates" call per group that keeps
  2+ copies, sequentially.
- **Recommended:** accept for this phase (it only costs time; nothing is
  applied inline), and schedule a small follow-up: let a bulk action ask for
  one re-check at its end (a `recheck=false` flag on the per-group calls,
  then one call), or run the re-check in the background and publish when
  done.

**6.3 §7's "came back" target after A1's review fix** (integrator).
- Evidence: section 4(b). With `_same_problem`, an issue applied and planned
  again with something new within 24 h opens as a new problem, which the
  "came back" query counts. A re-detection of the same problem is `failed`,
  which the second query counts.
- **Recommended:** keep the query as the §7 check, with "target 0, read any
  row" rather than a hard 0: a new problem under the same key is fine; a fix
  whose detail changes each pass is a loop to fix. Watch the "didn't stick"
  query alongside it.

## 7. Migrations

| version | ticket | what | time on the copy |
|---|---|---|---|
| v15 | P1-A1 | `md_issues.attempts` (default 0) and `applied_at` (NULL); `md_issue_events` (id, issue_id, at, from_state, to_state, reason) with indexes on `(issue_id, id)` and `(to_state, at)`; triggers `md_issues_event_insert` and `md_issues_event_state` (on a change of state). No backfill. | 0.014 s |

- Phase 1's v12-v14 take 9.06 s on the same copy (4.16 + 2.39 + 2.52); a
  start from v11 runs all four in 9.17 s. A second start takes 0.006 s.
- v15 is SQL in one transaction with its version row, like every migration
  since phase 1. The lane's `test_a_cut_off_v15_leaves_nothing` shows a cut-off
  run leaves v14 and no trace.
- The triggers use only `strftime`, so the host `sqlite3` can still write to
  `md_issues`.
- History starts at the migration: no event exists for anything before it.

## 8. Deploy notes (before `docker compose up -d --build`)

Deploy is the user's call (§8). Phase 2 should ship whole: A1's state machinery
needs A2's `track_order` fix (open item 8), and A4's UI reads A1's states.

- **Phase 1 first.** As of the shared copy (taken 2026-11-02, schema v11),
  the live DB has none of phase 1's migrations. Deploying this branch
  deploys phases 1 and 2 together, and phase 1's deploy notes all apply:
  - **Back up first:** `sqlite3 data/mdl.db ".backup data/mdl-pre-p1.db"`.
    v12 is one-way; v15 is additive.
  - About 10 dismissed items come back once in Needs attention (phase 1's
    6.4).
- **Start-up** runs v12 → v15, about 9 s on a DB this size, before the app
  answers. Each migration is atomic; an interrupted start just runs it again.
- **Nothing to configure.** There are no new environment variables, settings
  or dependencies, and no `RULES_VERSION` bump.
- **What the user will notice:**
  - Needs attention counts open and failed issues. "N in progress" (queued)
    and "N tried" show under each chip, and "N tried" opens the Tried list,
    where the action is Accept incomplete (6.1).
  - A fix that doesn't stick within 24 h shows **failed**, "still there after
    the fix", with how often it ran ("failed ×2"); auto-apply leaves it.
    Apply by hand still runs it. A slow Plex scan after a "not in Plex" fix
    can show failed until a later check clears it.
  - Closed issues say why: **superseded** (the album moved, merged or is
    gone), **cleared** (no longer found; no fix of ours ran), **applied**
    (a fix of ours ran).
  - 252 open missing-tracks issues had a download before. They stay open
    until the user asks again: Review → "Download ran" → select all →
    Download missing moves each one with nothing left to fetch to Tried at
    once, and queues nothing already tried.
  - The oscillation breaker's message now says it refuses a manual Apply too.
  - Undoing a move or merge that folded an album gives the album back its
    unit id, pin, dismissals and overrides. An immediate Restore after a
    Trash does too.
  - Trash, "not duplicates" (and its undo), Restore and artist merges update
    Needs attention before they return. Each now takes about one duplicate
    analysis longer (13.5 s on the live app). A bulk Trash on the track
    duplicates screen can take minutes (6.2).
  - Two albums get one tags fix that removes a junk label: DMX *Mixtape*
    ("Siccness.net", 12 files) and R.E.M. *Live in Greensboro* ("nugs.net",
    26 files; the release's next label "Rhino" is written instead). Then they
    stop being retagged.
  - Two album-copies sets the merge can't act on are no longer listed: *My
    Head Is an Animal* and *Dreaming Out Loud*. Each listed set says what the
    merge would do ("4 moved in, 0 to Trash, 5 left where they are").
  - Errors from Needs review, Skipped and the job list are shown, not
    swallowed. The Review dialog follows the runner live.
- **Phase 0's and 1's notes still apply:** don't purge Trash (P1-R2), and hold
  off on Re-check for the rockstar-style singles (known gap).
- **After deploy, run:**
  - the two §7 issue queries in 4(b) (came back: target 0, read any row;
    didn't stick: watch);
  - the §7 oscillation query (target 0 rows over 48 h);
  - the Download-missing skips query with `:deploy_time` (target: no NULL
    `error_code`);
  - a scan of `docker logs mdl-app` for "oscillating between", "duplicate
    sync after a library change failed", "check failed for" and "artist
    check failed for". "Still there after the fix" is an issue's reason, not
    a log line: the "didn't stick" query finds those.
- **Sandbox clean-up (not the live app).** Four sandbox containers are still
  running and idle, all with `--network none` and read-only mounts, so they
  write nothing:
  - `cc12a9670613` (`cool_lewin`, lane b's fixer, `dry_trash_restore.py`,
    hung on a failed connect);
  - phase 1's three, still there: `b55bd23683c6`, `4809e13b678c` and
    `312d8400a506`.

  Earlier agents' attempts to stop them were blocked by the permission
  system, so the user should run
  `docker kill cc12a9670613 b55bd23683c6 4809e13b678c 312d8400a506`.
