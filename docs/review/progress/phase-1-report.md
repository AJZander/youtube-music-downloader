# Phase 1 report: foundations

Integrator's report for `fix/review-2026-11-p1`, which started at `20cf111`
(phase 0's integrated tip plus the ledger). The code tip is `a9b8ba2`. The
commit that adds this report and the plan sits on top of it. The checks ran on
2026-11-02 (11:58Z onwards) against the shared copy
`.cache/claude-sandbox/mdl-p1.db` (schema v11, taken at 13:23 local time; its
newest `md_changes` row is 2026-11-01T22:32:16Z). All counts are sizes, not
oracles, because the live library keeps changing.

**Result:**
- All 5 tickets are done and integrated: P1-F1 … P1-F5.
- Backend: `Ran 533 tests in 223.516s OK` (451 at the phase start, 82 new).
  Frontend build OK.
- Three migrations (v12, v13, v14): 9.7 s in total on the 1.4 GB copy, each
  atomic. No new settings, no new dependencies, no `RULES_VERSION` bump.
- Behaviour parity over 457 albums: 452 of 456 common albums plan exactly the
  same issues. Every difference traces to P1-F1's named behaviour changes.
- Four items need the lead's decision (section 6). None blocks the merge of
  the phase branch. One of them (item 6.4) the user will see at deploy.

---

## 1. Tickets and commits

There were no conflicts. Every lane's fixup commits were folded into their
ticket's commit, and each folded commit got one "Review fixes: …" line. After
each lane, `git diff <lane branch> HEAD` was empty (each lane contains the one
before it), so the integrated tip's tree is identical to `lane/p1-d`.

| ticket | lane | commit on `fix/review-2026-11-p1` | lane commits folded in |
|---|---|---|---|
| P1-F1 One identity module | a | `02f9565` | `d799429` + fixup `58259da` |
| P1-F2 Stable album identity | b | `a188607` | `a96fa9d` + fixups `4d599eb`, `87986f0`, `bf79979` |
| P1-F3 One relocate service | c | `8118e4b` | `3e53483` + fixup `d37967a` |
| P1-F4 Writers take turns, albums settle | d | `dc102dd` | `ab7a7a9` + fixup `ef62daf` |
| P1-F5 Transactions and indexes | d | `a9b8ba2` | `f4de6d8` + fixups `2744ac4`, `71bc10d`, `f50975e`, `75a7323` |

- The order is the plan's chain: a → b → c → d, with F4 before F5 in lane d.
  F4's fixup `ef62daf` was made after F5's commits on the lane. It
  cherry-picked onto F3 cleanly (`apply.py` auto-merged), and the tree after F5
  equals `lane/p1-d`.
- Commit messages: besides the "Review fixes" line, the statements the fixups
  made untrue were corrected in place, as the fixup messages asked:
  - P1-F2's "Known gap: a folder renamed outside the app loses its identity"
    now says it keeps its identity (fixup `4d599eb`).
  - P1-F5's list of wrapped writes now names `units.rekey`, `units.follow`
    and `sync_registry`'s read. It also says `trash_paths` commits its Trash
    record before `forget` (fixups `f50975e`, `75a7323`, `71bc10d`).

Files changed in the phase (`git diff --stat 20cf111..a9b8ba2`: 40 files,
+6,431/−985):
- new backend modules `app/identity.py`, `app/relocate.py` and `app/locks.py`;
- backend `app/{api,catalog,db,discover,importer,library,library_index,ownership,worker}.py`
  and `app/metadata/{apply,desired,issues,missing,plexsync,resolver,routes,runner,units,views}.py`;
- 5 new test modules plus `tests/identity_golden.py` (data only), and fixture
  updates in `test_catalog`, `test_library`, `test_metadata`,
  `test_p0_oscillation`, `test_p0_plex_foreign` and `test_p0_skips`;
- frontend `api.js`, `Library.jsx`, `artist/ReleaseRow.jsx` and
  `library/{ArtistView,DetailPane}.jsx`, `library/issues.js`.

The diff adds no `purge_trash`, `.mdl-trash`, `unlink` or `rmtree` call to
app code. The only new ones are in tests' temporary directories.

## 2. Tests added (82)

Each lane recorded that its new tests failed on its starting tip, and each
reviewer reproduced it. The review-fix tests failed on the lane's first commit.

- **P1-F1**, 19 tests (16 + 3 from review). `tests/test_p1_identity.py`
  over `tests/identity_golden.py`, a golden table of 135 rows (127 + 8 from
  review): recording, album, path, artist and quality.
  - GoldenTestCase: `test_the_table_is_big_enough`, `test_identity_agrees_with_the_table`,
    `test_unsure_is_not_the_same`, `test_old_modules_still_answer_by_the_old_names`.
  - CallersTestCase: `test_ownership_lookup_follows_the_table`, `test_a_same_video_match_stays_have`,
    `test_the_gate_lets_a_different_length_through`, `test_the_picker_and_the_resolver_share_one_matcher`,
    `test_the_picker_assigns_the_whole_list_at_once`, `test_track_duplicates_follow_the_table`,
    `test_album_duplicates_follow_the_table`, `test_split_key_keeps_taylors_version_apart`,
    `test_different_album_follows_the_table`, `test_album_share_follows_the_table`,
    `test_discover_owned_follows_the_table`, `test_one_quality_scorer`.
  - From review: `test_a_bracketed_edition_filed_plainly_is_held`,
    `test_a_single_is_held_by_its_own_track`, `test_the_worker_gives_the_gate_its_release_type`.
  - Updated: `test_catalog.py:677` is now `("elsewhere", True)`, the
    length-ignoring rule the ticket names. `test_p0_skips.test_a_gate_skip_records_its_reason`
    was rewritten for the new gate rule.
- **P1-F2**, 16 tests (10 + 6 from review), `tests/test_p1_unit_id.py`:
  - DecisionsTestCase: `test_a_dismissal_survives_a_move`,
    `test_an_accepted_match_survives_a_move_into_an_existing_key`,
    `test_an_artist_merge_keeps_the_albums_decisions`,
    `test_a_split_folder_keeps_its_unit_and_a_rejoin_merges_it`,
    `test_a_pin_never_crosses_release_groups`,
    `test_a_merged_away_album_redirects_to_its_keeper` (through the real router),
    `test_an_auto_pause_follows_the_album`.
  - DecisionsTestCase, from review: `test_a_folder_renamed_outside_the_app_keeps_its_decisions`,
    `test_files_that_cannot_be_told_apart_start_afresh` (a guard),
    `test_an_album_back_at_a_merged_away_key_opens_as_itself`,
    `test_a_pin_never_crosses_release_groups_on_an_artist_merge`,
    `test_a_pin_never_crosses_release_groups_on_a_move`,
    `test_one_file_of_another_group_keeps_a_pin_behind`.
  - MigrationTestCase: `test_existing_rows_get_unit_ids_and_their_issue_keys_follow`,
    `test_an_interrupted_migration_leaves_nothing_half_done`,
    `test_a_python_step_runs_in_one_transaction_with_its_version`.
  - Fixture-only: `test_p0_oscillation` reads `auto_paused:<unit id>`;
    `test_p0_plex_foreign` stores its override under the unit id.
- **P1-F3**, 17 tests (14 + 3 from review), `tests/test_p1_relocate.py`:
  - PathsResolveTestCase (asserts `dangling() == []` after each mover):
    `test_after_an_import`, `test_after_a_move`, `test_after_a_merge_of_copies`,
    `test_after_filing_in_downloads`, `test_after_an_undo`,
    `test_after_trash_and_restore`, `test_after_an_artist_merge`, and the
    property test `test_any_sequence_of_movers_keeps_every_path_resolving`
    (three seeds of 12 steps each).
  - ArtistMergeTestCase: `test_an_artist_merge_keeps_its_files_indexed_with_their_video_ids`,
    `test_an_artist_merge_can_be_undone`, and from review
    `test_an_album_trash_emptied_hands_nothing_to_the_one_beside_it`.
  - `ImporterSpellingTestCase.test_the_importer_uses_the_on_disk_spelling`,
    `CaseInsensitiveJoinTestCase.test_a_case_only_difference_still_joins`,
    `FolderPrefixTestCase.test_a_folder_never_touches_one_that_starts_like_it`,
    `PathKeyMigrationTestCase.test_v13_backfills_path_keys_atomically`.
  - From review: `ExclusionOrderTestCase.test_a_pair_stored_the_other_way_round_is_still_found`,
    `test_a_followed_pair_is_kept_once_in_order`.
  - Fixture updates: `test_library` MergeArtistFolders patches `relocate.relocate`,
    and MergeRetag fakes `tagio`. In `test_p1_unit_id`, three
    `counts['merged'] == 1` checks now assert that the record was folded.
- **P1-F4**, 15 tests (12 + 3 from review), `tests/test_p1_settling.py`:
  - ImportDuringMoveTestCase: `test_an_import_during_a_planned_move_lands_in_the_current_folder`
    (the triage's acceptance; it runs the real check, executor and importer),
    `test_an_import_waits_for_a_running_move_and_follows_it`,
    `test_a_scan_during_a_move_neither_reads_nor_prunes_its_folders`.
  - MergeTestCase: `test_a_scan_during_a_merge_does_not_index_the_movers_at_the_source`,
    `test_merges_and_the_layout_guard_wait_while_a_copy_is_settling`,
    `test_trash_waits_for_a_folder_held_by_an_apply`.
  - `QueueAfterMoveTestCase.test_download_missing_is_not_queued_in_the_pass_that_moves_the_album`.
  - LocksTestCase: `test_a_task_holding_a_folder_holds_it_again_at_once`,
    `test_a_disc_folder_is_its_album_folder`,
    `test_two_tasks_taking_folders_in_either_order_both_finish`,
    `test_a_folder_let_go_since_counts_as_touched`,
    `test_every_file_and_queue_executor_waits_for_downloads`.
  - From review: `LocksTestCase.test_a_task_holding_a_folder_takes_one_before_it_only_if_free`,
    `test_a_task_holding_a_folder_waits_for_one_after_it`,
    `FilingDuringTrashTestCase.test_the_download_gives_way_to_a_trash_of_both_folders`.
  - Fixture updates: `test_metadata`'s ApplyPasses fake learns the new calls.
    Three lane c relocate tests now queue their job after the move began
    (`queued_after()`), since §2 forbids queueing a Download missing into an
    album before moving it.
- **P1-F5**, 15 tests (10 + 5 from review), `tests/test_p1_transactions.py`:
  - TransactionTestCase: `test_a_failure_rolls_back_every_statement`,
    `test_db_calls_inside_a_transaction_share_it`, `test_another_task_waits_for_the_commit`,
    `test_issues_save_is_all_or_nothing`, `test_a_failed_relocate_changes_no_table`,
    `test_a_failed_forget_changes_no_table`, `test_sync_registry_is_atomic`.
  - TransactionTestCase, from review:
    `test_a_statement_that_failed_alone_does_not_break_the_next_transaction`,
    `test_trash_after_a_failed_statement_keeps_its_record`,
    `test_trash_keeps_its_record_when_the_index_update_fails`,
    `test_a_move_committed_while_the_registry_reads_keeps_the_record`,
    `test_an_album_following_its_files_moves_whole_or_not_at_all`.
  - IndexTestCase: `test_the_journal_lookups_use_an_index` (EXPLAIN QUERY PLAN
    on the modules' own SQL constants), `test_a_download_follows_its_file_by_its_key`,
    `test_v14_is_an_index_migration_a_second_start_leaves_alone`.
  - Fixture updates: `test_library`'s stubbed-DB cases stub `db.transaction`.
    `test_p1_relocate`'s v13 test expects the latest version.

## 3. Suite and frontend build (integrated tip)

- Backend: the integration worktree's `backend` was mounted read-only, with
  `--network none`, and `-m unittest discover tests` ran on the tree of
  `a9b8ba2`. That is the tree of `lane/p1-d`; the later amends changed only
  commit messages. Result:
  **`Ran 533 tests in 223.516s OK`**, with 0 errors and 0 failures.
  - 533 = 451 at the phase start, plus F1 19, F2 16, F3 17, F4 15 and F5 15.
- Frontend: files changed in this phase (F1's ReleaseRow tooltip, F2's
  unit-id album URLs), so the build check ran:
  `docker build --no-cache -t mdl-web-sandbox <wt-integration>/frontend`.
  It passed: `✓ 1084 modules transformed`, `✓ built in 10.78s`, exit 0.
  The only warning is vite's existing chunk-size warning.

## 4. Phase checks (dry runs, read-only, on `mdl-p1.db`)

Scripts and outputs are in `.cache/claude-sandbox/work/p1-integration/`
(`scripts/`, `*.out`). Every container ran with `--network none` and the shared
copy mounted `:ro`, then backed up into the container's `/tmp`. The library
was mounted `:ro` only for (b). `PLEX_TOKEN` was empty and every write flag
was passed `=false`.

### (a) Migration: v12 → v14 on a `/tmp` copy (`scripts/migrate_check.py`)

| step | seconds |
|---|---|
| v12 (F2: unit ids, issue re-keys, aliases) | 4.40 |
| v13 (F3: `path_key` backfill of 64,333 rows) | 2.87 |
| v14 (F5: two `md_changes` indexes) | 2.44 |
| **total** | **9.71** (a second run of all three in one start: 9.53) |
| a second start | 0.01 |

Before and after:

| | v11 | v14 |
|---|---|---|
| `md_albums` rows | 8,928 | 8,928 |
| `md_issues` rows | 57,367 | 57,367 |
| `md_issue_refs` rows | 60,664 | 60,664 |
| issues applied / dismissed / open / failed | 52,968 / 3,249 / 1,149 / 1 | the same |
| `manual` / `no_release` / `ignored` | 545 / 207 / 1 | the same |
| `md_overrides` | 0 | 0 |
| `auto_paused:` rows | 0 | 0 |
| dismissed unit issues on live albums | 2,514 | 2,514, now keyed `kind:<unit id>` |

- **Unit ids:** 0 `md_albums` rows without one, and 8,928 distinct ids.
- **No decision is lost:**
  - per album, the tuple (manual, pinned release, no_release, ignored) is
    identical before and after;
  - the 3,249 dismissed issue ids are the same rows;
  - 0 issue rows changed kind, state, album key or summary, and 0 were deleted.
- **Every open issue resolves** (open and failed, 1,150 rows):
  - 785 unit-kind issues: key `kind:<unit id>`, ref the unit id, and the
    album key is a live album. 0 don't resolve.
  - 303 `missing_tracks`: release group known (from `md_albums`, or else the
    cached MusicBrainz release), and the album is live. 0 don't resolve.
    They stay keyed by release (plan default; P1-B1 re-keys them).
  - 3 `album_copies`: every copy is a live album. They keep their path key
    until the next cycle plans them again under the unit-id key.
  - 59 library-wide issues are not unit issues: 45 `dup_track`, 8
    `artist_unknown`, 4 `dup_album` and 2 `split_artist`.
- **First registry pass** after the migration (the parity run's p1 side, 8.0 s):
  - 64,333 index rows stamped, 0 successors, 0 merges;
  - 19 new units and 11 gone: P1-F1's `split_key` splits 8 folders
    differently. See 6.4: 10 dismissals come back.

### (b) Behaviour parity: metadata check, plan only (`scripts/parity.py`, `scripts/compare_parity.py`)

Setup:
- The p0 tip (`20cf111`, extracted with `git archive`) and the p1 tip, each
  on its own `/tmp` copy. Each run does one `sync_registry`, then
  `runner.check_unit` for every album of the sample folders, in key order.
- The p1 side migrated at connect.
- The sample is 410 folders, 457 albums:
  - every folder of Hilltop Hoods, Paul Kelly, The Devil Wears Prada, Snoop
    Dogg, Michael Jackson, Laufey, Slipknot and Wiz Khalifa (221 folders);
  - plus 189 folders picked by `md5(key) % 45 == 0`.
- `issues.plan_unit`'s output was captured per album (kind, summary, auto),
  and the open issues each album had after its check were recorded.
- MusicBrainz came from `meta_cache` only, Plex was faked, auto-apply was
  patched out, and nothing was applied.
- The write flags were set true in memory only, so the planner plans file and
  Plex fixes as the live app does. No executor ran, and the library was `:ro`.
- Both runs finished: 457 checked, 0 errors (93.6 s and 73.7 s).

**Result: 452 of the 456 albums both runs share plan the same issues**
(kind and summary). No `auto` flag changed where the plan is the same.
Statuses: p0 320 resolved / 24 review / 113 unresolved, p1 321 / 23 / 113.
The calibration albums plan identically: Paul Kelly, Hilltop Hoods'
Instrumental Edition and v1-era folders, The Devil Wears Prada *Dead & Alive*,
Snoop Dogg *Doggystyle* and its 2005 reissue, Laufey and *Thriller 25*.

Each class of difference, with counts:

| class | albums | ticket and named change |
|---|---|---|
| a key that `split_key` now splits: `DMX/Singles\|wherethehoodat` becomes `…\|wherethehoodataxiomaticremix` (album-wide "Remix" no longer folds) | 1 (in each run) | P1-F1, `split_key` keeps version qualifiers apart |
| review → resolved: Hilltop Hoods *(2012) Drinking From the Sun*, 0.848 → 0.889. It now plans `tags`, `move` (rename), `track_order` and `extra_tracks` (3 files), plus `plex_foreign` (mixed with 2 tracks of the Instrumental Edition) and `missing_tracks` (2 of 14) | 1 | P1-F1, resolver rank C (near-identical title). One of the 5 crossings lane a's dry run listed and checked |
| more files mapped on a resolved album: Michael Jackson *King of Pop: Deluxe UK Edition*, extra files 3 → 1. It now plans `tags` (2 files), a rename of 2 files and `track_order` (2 numbered wrong) | 1 | P1-F1, resolver rank C |
| `review_match` score changes: MJ *The Very Best Of* 61 % → 65 %; Snoop Dogg *Bible of Love (Chapter Two)* 74 % → 77 % | 2 | P1-F1, resolver rank C raises coverage |

The new fixes were checked by hand, because auto-apply would take them
(`auto_ids` 3 on each album; `detail-p1.out`):
- Hilltop Hoods `10 - The Thirst Pt. 2.opus` (65.6 s) → "The Thirst, Part 2"
  (MusicBrainz 65.0 s), numbered 10.
  - The two v1-era files `The Thirst Pt. 1/Pt. 3 (Official Audio)` stay
    unmapped extras. That is the conservative answer.
- King of Pop:
  - `Black or white (The Civilles & Cole House-Club mix)` (454.88 s) →
    3-12 "Black or White (The Clivillés & Cole house/club mix)" (454.88 s);
  - `Ghosts` (313.49 s) → 2-14 "Ghost" (313.49 s). The recording's title is
    "Ghosts"; the release's track title is "Ghost", and the planner writes the
    release's title, as it always has.
- All three are the same recordings, within 1 s.

Nothing else differs. There are no settling differences: the copy has 0
active jobs. F2 to F5 change no plan.

### (c) P1-F3 baseline on real data, after the migration (`scripts/migrate_check.py`)

These references don't resolve case-insensitively (by `path_key`) to an
indexed file or folder, and aren't in Trash. It is a baseline only: relocate
fixes future moves, and repairing the old ones is phase 5's, with the user's
go-ahead.

| path-holding table | dangling | of |
|---|---|---|
| `tracks.library_path` (completed) | 1,167 | 18,545 |
| `library_index.provider_track_id` (a completed download whose indexed file lost its video id) | 478 | – |
| `library_exclusions` album scope (folder names) | 1,399 | 5,336 (2,668 pairs) |
| `library_exclusions` track scope | 0 | 0 |
| `md_albums.key` / `dirpath` | 0 / 0 | 8,928 |
| `md_issues.album_key` (open unit issues) | 0 | 785 |
| `md_issue_refs.album_key` (open issues) | 0 | 1,148 |
| `jobs.target_dir`, active jobs (`dangling()`) | 0 | 0 active |
| `jobs.target_dir`, every status | 20 (all completed jobs) | 529 |
| `md_overrides.ref` | 0 | 0 |
| `md_state` `merge_keeper:` | 0 | 0 |
| `md_state` `plex_readd:` | 3 | 23 |
| `plex_items.path` (tracks) | 0 | 64,333 |

**Completed downloads whose `library_path` is no longer indexed (§1 says
2,026): 2,027 of 18,545 by exact path.**
- 238 are indexed under another case: a case-insensitive join now finds them.
- 622 are in Trash.
- 1,167 are neither, the `tracks.library_path` row above.

### (d) §7 merge/move oscillation query

The query as written ran at 2026-11-02T11:58Z. **0 rows**, the same baseline
as phase 0.
- The 48 h window holds 2 merge batches, one per album:
  - Bob Dylan *More Blood, More Tracks … (deluxe edition)* at 11-01 20:46:38;
  - *Thriller 25 [2008 Reissue]* at 11-01 20:46:39.
- 7 days: 0 rows (2 batches).
- 14 days: 18 albums with 39 batches among them, and 196 merge batches in
  all, the same as phase 0.

Target after deploy: 0 rows over 48 h.

## 5. Review findings

**Fixed (16, all folded into their ticket's commit):**

| ticket | finding | fix (lane commit) |
|---|---|---|
| P1-F1 | Under `release_group`, `on_album` compared keep-mode keys, so a bracketed edition (*Sempiternal [Deluxe Edition]*) wasn't held by its plain folder, even for the same video: 15 same-video downloads and 23 Discover tracks on the copy. | `same_album` also compares names with `[..]` dropped (`AlbumId.names`), and the release type must still agree (`58259da`) |
| P1-F1 | The gate passed no release type, so a wanted single never matched its own `[Single]`/EP folder, even for the same video. | The worker passes `catalog.release_type` (None when unknown). By video, None also matches a single's folder. 30 elsewhere→have on replay, all same video (`58259da`) |
| P1-F1 | A folder qualifier naming one bonus track (*(Includes "Roses (Imanbek Remix)")*) marked the whole album as remix. | Only includes/incl./plus/"with additional" qualifiers are dropped; a bare "(with X …)" credit keeps its version (`58259da`) |
| P1-F2 | A folder renamed outside the app lost its unit and every decision. | `library.scan` carries a vanished file's unit id to the new file with the same content, only when each side has exactly one match. Renaming all 1,668 artist folders on the copy kept 8,936 of 8,936 units (`4d599eb`) |
| P1-F2 | `merge_into` worked out the keeper's release group after the mover's files were in its folder, so a pin could cross groups. | Only the keeper's own files vote, and every group they name must be the mover's (`87986f0`) |
| P1-F2 | `resolve()` followed an alias's old key before looking at what the key holds, so an album back at a merged-away key redirected, and its actions answered 409. | It checks for a key that holds files first (`bf79979`) |
| P1-F3 | `relocate._landing` fell back to the files just moved out, so a split album whose files went to Trash handed its pin, ignore and overrides to the album beside it. | The fallback is only for a whole-folder record, and only takes unstamped files (`d37967a`) |
| P1-F3 | `rekey_exclusions` renamed pairs without re-sorting them, so "not duplicates" decisions stopped matching. 705 of 2,668 album pairs on the copy were stored unsorted. | Pairs are re-sorted on rename, and read through `pair_key`. Album duplicate groups 5 → 4: The Velvet Underground *White Light/White Heat* stays marked not-duplicates (`d37967a`) |
| P1-F3 | An artist-merge undo re-read a folder path and wrote an unreadable index row for it. | `reread_files` reads only indexable audio files (`d37967a`) |
| P1-F4 | `_file_in_downloads` took the downloads' folder inside the runner's album hold, which broke the lock order: a Trash, Undo, Restore or artist merge of both folders could deadlock with the apply loop. | `locks.hold` waits only for folders after all it holds, and raises `locks.Busy` otherwise. The Download missing fails readably and files nothing (`ef62daf`) |
| P1-F5 | `sync_registry` read its snapshot outside its transaction, so a relocate committed in the gap dropped the moved album's record. | The read is inside the transaction. The DB lock is held no longer than before (`f50975e`) |
| P1-F5 | The Trash ledger row and `forget` were one transaction, so a failure after the files reached `.mdl-trash` left them unlisted. | The ledger commits first; `forget` is its own transaction (`71bc10d`) |
| P1-F5 | `transaction()` broke after any failed plain statement ("cannot start a transaction within a transaction"). | The implicit transaction is committed first, and BEGIN is inside the rollback (`2744ac4`) |
| P1-F5 | `units.follow`'s rekey committed statement by statement. | `units.rekey` and `units.follow` are one transaction each (`75a7323`) |

**Rejected:** none.

**Unconfirmed, still in the tip's code (lane b's reviewer, low):**
- `library.merge_artist_folders` (now `library.py:1470-1480`) builds a moved
  file's destination from the **source's** spelling of the album folder
  (`destination = target_dir / child.name`).
- On the case-insensitive share, a target album folder that differs only in
  case (`In The Lonely Hour` vs `In the Lonely Hour`) takes the files, but the
  index rows name the source spelling.
- The next scan sees them as gone and added again:
  - `4d599eb`'s rename-follow now carries the unit id across, so decisions
    survive;
  - but the `provider_track_id` link is lost, and the album shows twice until
    that scan.
- It can't be reproduced in the case-sensitive test container. The fix is to
  build `final` from `relocate.on_disk(...)`, as the importer does. Suggested
  as a small P1-F3 follow-up.

**Still open.** These are not phase 1 defects against the acceptance. They are
for the lead to schedule.

P1-F1:
1. The one-way version rule turns exact-title copies whose YouTube Music
   titles carry version words into `elsewhere`. That is 30 Beatles
   *Anthology 1/3* tracks ("(Home Demo - Remastered)", "(Live In Stockholm …)").
   The result is re-downloads, not deletions.
2. The picker misses 127 live, acoustic and remix takes that MusicBrainz titles
   plainly (e.g. ABBA "The Way Old Friends Do (Live At Wembley)"), and rank B
   at 2.5 s drops 10 more. They show as "not on YouTube Music". See 6.3.
3. `api._different_album` still judges by name, so a hold of an untyped single
   by its own video shows "different album" on the Skipped list. Display only:
   Re-check excludes gate-coded skips.
4. Under `release_group`, names drop every `[..]`, as base did. So Discover
   and plexsync count a `[Live]`, `[Unplugged]` or `[X Remix]` release as
   owned or the same when the plain one is held.
5. `issues.plan_album_copies` matches by recording id and has no golden-table
   test. P1-B2 owns its explicit rule.
6. The rockstar single with names only stays a known gap, as planned.

P1-F2:

7. Undoing a merge doesn't give the restored album back its decisions. See 6.1.
8. v12 is one-way. Rolling back to pre-p1 code would re-file every issue and
   orphan the 2,514 dismissals now keyed `kind:<unit id>`. See deploy notes.
9. The 163 dismissed unit issues that past key changes had already orphaned are
   not re-attached (plan default; phase 5).

P1-F3:

10. `jobs.target_dir` and `plex_items` follow only folders a move leaves
    empty. P1-F4's settling covers moving an album with a queued job.
11. `relocate._follow_tracks` still scans `tracks`: 20–60 ms per 400 paths.
    See 6.2.
12. Pre-existing and untouched: `library.scan`'s unreadable counter counts
    `r[-1]` (phase 0 open item 18).

P1-F4:

13. Settling covers only jobs with a `target_dir` or completed tracks. A plain
    album download's first track isn't covered until P1-B6.
14. An API cancel or delete of a job doesn't call `job_ended` (`api.py` was
    outside the lane). A settling album is re-checked within 1 h anyway.
15. Folder locks stay held through slow work: `do_scan`'s Plex re-add (up to
    about 7 min), `trash_paths`' Plex refresh, and the importer's lyrics and
    cover fetch. A track that targets such a folder stalls the sequential
    worker meanwhile.
16. With the largest job on the copy active (1,154 folders), `settling()`
    takes 25–29 ms per check, about 3 min of DB-lock time per full cycle.
17. A Download missing that meets a Trash, Undo, Restore or artist merge of
    the same folders fails with "… is being changed by something else; try
    again once that is done". The user retries it.

P1-F5:

18. `sync_registry` is now one transaction: about 5–10 s on the copy, with
    `all_units` 2.2–2.8 s of SQL plus 1.5–2.2 s of `build_units`. The read
    held the lock before too, but other tasks' DB calls (worker, API) now
    wait for the whole pass once per cycle. Watch the UI's responsiveness
    during the "checking" phase after deploy.

Phase-wide:

19. The first registry pass re-keys 11 units, and their 10 dismissals come
    back. See 6.4.

## 6. Needs-decision

The planner held none back, and every lane finished with status done. These
four came out of the reviews and this integration.

**6.1 P1-F2: undoing a registry merge doesn't restore the merged-away album's decisions** (lane b fixer, `blocked`).
- Evidence:
  - After an undo, the restored album gets a fresh unit id. Its pre-merge pin,
    ignore and dismissals stay on the keeper.
  - The cause: `sync_registry`'s `merge_into` isn't journalled, and
    `routes.revert_batch` re-checks only the batch's album keys, which
    `merge_into` repointed to the keeper.
  - Neither the F2 ticket nor §2/§9 covers undoing a registry merge.
  - `bf79979` makes the restored album open and work as itself; only its
    decisions are missing.
- **Recommended:** accept for phase 1. Nothing is deleted, and the
  decisions stay on the keeper. Schedule a follow-up with P1-A1's state
  history: journal `merge_into`'s folded decisions in `md_changes` and replay
  them on undo. Until then, the user re-applies the decisions on an album
  they un-merge.

**6.2 P1-F5: the `tracks(library_path)` index was not added** (reviewer d asks the lead).
- Evidence:
  - Since P1-F3 the update is `UPDATE tracks SET library_path = ? WHERE id = ?`,
    served by the primary key; `IndexTestCase` asserts this.
  - Every lookup before it is `library_path IN (…) OR path_key(library_path) IN (…)`,
    and a plain index on `library_path` can't serve the OR. The same holds for
    `_relink` and `missing._album_download`.
- **Recommended:** accept the deviation. If the 20–60 ms scan per relocate
  ever matters, add a `tracks.path_key` column with an index. Don't use an
  expression index on `path_key()`: a writer that hasn't registered the
  function, such as the host `sqlite3`, would then fail.

**6.3 P1-F1: policy calls for the lead's review** (lane a, not blocking).
- (a) The gate and the Skipped list compare albums by release group, in any
  edition, not by edition. The edition check broke P0-4's
  `test_a_moved_match_is_judged_by_its_folder`, and §2 says nothing another
  edition holds is re-downloaded. The duplicate finders keep the strict edition
  rule.
- (b) The picker's version rule drops 127 takes, and rank B at 2.5 s drops 10
  more (still open item 2).
- **Recommended:** keep both as committed. Revisit the lost takes in P1-B4
  (assign the whole release with held tracks), rather than loosening the
  version rule here.

**6.4 Deploy: P1-F1's `split_key` re-keys 11 units, and F2 can't follow them on the first pass** (integrator).
- Evidence, from the parity run and lane a's `splits-*.jsonl`:
  - The first `sync_registry` after the migration stamps the index for the
    first time. So it finds 0 successors, makes 19 new units and drops 11.
  - The 11 are in 8 singles-type folders: Akon, Avicii, DMX, Flo Rida ×3,
    Korn, Maroon 5 ×3 and Pitbull. None is manual or ignored.
  - They carry 10 dismissed issues (9 `unresolved`, 1 `folder_artist`) and 2
    open `review_match`.
  - The new units file their issues again as open. In the parity run,
    `DMX/Singles|wherethehoodataxiomaticremix` has an open `unresolved` that
    p0 kept dismissed.
- So **10 dismissed items come back in Needs attention once**, at deploy. No
  file is affected.
- Options:
  - (a) accept it, and the user dismisses them again;
  - (b) a small follow-up before deploy: the first registry pass stamps the
    index rows by the old keys before the new split applies, so the
    successors are found.
- **Recommended: (a)**, and tell the user. It is 10 low-stakes items on
  singles folders, and (b) would add one-off code to the registry, the most
  sensitive path of F2.

## 7. Migrations

| version | ticket | what | time on the copy |
|---|---|---|---|
| v12 | P1-F2 | `md_albums.unit_id` (unique index, plus an insert trigger), `library_index.unit_id`, `md_unit_aliases`; re-keys live unit issues (19,795 keys), overrides (0) and `auto_paused:` rows (0) | 4.40 s |
| v13 | P1-F3 | `library_index.path_key`, filled in 2,000-row chunks (64,333 rows), plus `idx_li_path_key` (a Python step) | 2.87 s |
| v14 | P1-F5 | `idx_changes_target_ref` and `idx_changes_target_field_at` on `md_changes` (285,911 rows) | 2.44 s |

- Total 9.71 s stepwise, and 9.53 s in one start. A second start takes 0.01 s.
- `_migrate` now runs each migration and its version row in one transaction
  (part of P2-D8). The lanes showed that a migration cut off by a failing
  statement leaves the previous version and no trace: v12 → v11, v13 → v12,
  v14 → v13.
- `connect()` also registers a deterministic `path_key()` SQL function.
  Nothing in the schema depends on it: there is no trigger or expression
  index, so the host `sqlite3` can still write.
- `library_index.unit_id` is stamped by the first registry pass, not by the
  migration (8.0 s on the copy).
- Journal lookups after v14 (lane d, best of 5): `ours_locked` 103.6 → 0.0 ms,
  the breaker 148.8 → 29.9 ms, `match_flipflop` 136.6 → 0.1 ms and
  `changed_by_us` 98.1 → 0.0 ms.

## 8. Deploy notes (before `docker compose up -d --build`)

Deploy is the user's call (§8). Phase 1 should ship as one unit: F1 and F2 must
deploy together (6.4), and F3 to F5 build on F2's schema.

- **Back up first.** Take a sqlite `.backup` of `data/mdl.db` while the app
  runs, before rebuilding:
  `sqlite3 data/mdl.db ".backup data/mdl-pre-p1.db"`.
  - v12 is one-way (open item 8), and P2-D8's automatic backup isn't built
    yet.
  - To roll back: stop the app, restore that file, and deploy the old code.
- **Start-up** runs v12 → v14, about 10 s on a DB this size, before the app
  answers. Each migration is atomic: an interrupted start just runs it again.
- **The first metadata cycle:**
  - `sync_registry` stamps about 64k index rows. It now holds the DB lock for
    its whole pass (about 5–10 s), so the UI and the worker pause briefly once
    per cycle (open item 18).
  - **About 10 dismissed items reappear** in Needs attention: `unresolved`
    and `folder_artist` on the Akon, Avicii, DMX, Flo Rida, Korn, Maroon 5 and
    Pitbull singles folders (6.4). The user can dismiss them again, and
    they stay dismissed from then on, across moves.
- **What the user will notice:**
  - Album URLs become `#/library/<artist>/<unit id>`. Old links still open,
    and a merged-away album redirects to its keeper.
  - Decisions (dismiss, Accept match, ignore, no release, overrides) now
    follow an album through moves, merges, artist merges and renames made on
    the share.
  - Some tracks that used to be skipped as held now download: a same-title
    file whose length is more than 5 s off (30 s for an old YouTube upload),
    or whose version differs. Re-gating the copy's skips, 164 of 2,307 "have"
    answers become "elsewhere", and Download missing's skips would drop from
    934 to 776. The whole-release picker takes 7,767 tracks instead of
    7,909: some live, acoustic and remix takes now show "not on YouTube
    Music" (6.3b).
  - About 5 albums in review become resolved through near-identical titles
    (lane a's dry run; Hilltop Hoods *Drinking From the Sun* in the parity
    run). With auto-apply on they get tag, rename and numbering fixes. The
    ones checked are correct.
  - A download into an album holds that album's file fixes (tags, moves,
    merges, numbering, extra tracks, not in Plex) until the job ends. The
    issue says "waits for the download into <folder> to finish".
  - A Download missing started while a Trash, Undo, Restore or artist merge
    touches the same folders fails with "… is being changed by something else;
    try again once that is done".
  - Artist merges are journalled as one batch and can be undone; no rescan is
    needed.
  - Downloads into an existing artist or album folder reuse its on-disk
    spelling, so `SAM SMITH/` is no longer created again as `Sam Smith/`.
- **Phase 0's notes still apply:** don't purge Trash (P1-R2), and hold off on
  Re-check for the rockstar-style singles (known gap).
- **Nothing to configure.** There are no new environment variables, settings
  or dependencies, and no `RULES_VERSION` bump. The F1 rule changes reach
  albums as they come due.
- **After deploy, run:**
  - the §7 oscillation query (target 0 rows over 48 h);
  - the Download-missing skips query with `:deploy_time` (target: no NULL
    `error_code`);
  - a scan of `docker logs mdl-app` for "oscillating between", "short
    listing", "is being changed by something else" and "waits for the
    download".
- **Sandbox clean-up (not the live app).** Three sandbox containers from the
  lanes are still running and idle:
  - `b55bd23683c6` (lane b fixer, `rename_real.py`, 4 h);
  - `4809e13b678c` (`-m unittest tests.test_p1_settling` on lane d's
    pre-fix worktree, 2 h);
  - `312d8400a506` (lane d fixer, `sync_time.py`).

  All three run with `--network none` and read-only mounts, so they write
  nothing. The permission system blocked the agents from killing them, so the
  user should run `docker kill b55bd23683c6 4809e13b678c 312d8400a506`.
