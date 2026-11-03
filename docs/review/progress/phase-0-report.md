# Phase 0 report: stop the damage

Integrator's report for `fix/review-2026-11-p0`, which started at `f3586fd`.
The code tip is `a35ee7a`. The commit that adds this report and the plan sits on
top of it. The checks ran on 2026-11-02 against the shared copy
`.cache/claude-sandbox/mdl-p0.db`, taken at 10:13 local time; its newest
`md_changes` row is 2026-11-01T22:32:16Z. All counts are sizes, not oracles,
because the live library keeps changing.

**Result:**
- All 7 tickets are done and integrated: P0-1 … P0-6, plus P1-G4.
- Backend: `Ran 451 tests in 145.635s OK` (394 at baseline, 57 new). Frontend build OK.
- No migrations, no new settings, no new dependencies, no `RULES_VERSION` bump.
- No ticket needs a decision. Some operational items need the user's attention
  before or right after deploy (see "Deploy notes").

---

## 1. Tickets and commits

There were no conflicts. Every lane's fixup commits were folded into their
ticket's commit, and that commit's body got one "Review fixes: …" line. After
each lane, the tree was compared with the lane's tip:
- after lane b, `git diff lane/p0-b HEAD` is empty (lane b contains lane a);
- lanes c and d: their files are identical to `lane/p0-c` and `lane/p0-d`.

| ticket | lane | commit on `fix/review-2026-11-p0` | lane commits folded in |
|---|---|---|---|
| P0-1 Break the merge/move oscillation | a | `4bf17a4` | `564eaa6` + fixup `761e266` |
| P0-3 Make the Plex mirror trustworthy | b | `b22f489` | `4e659fb` + fixups `79e9ca7`, `fedcfcb`, `38e9262`, `a8427b8` |
| P1-G4 Stop the match flip-flop | b | `918bb3e` | `d0fb15d` |
| P0-5 Every skip records why | c | `779d60b` | `e8d564b` |
| P0-4 Different-album label and Re-check loop | c | `ed28579` | `919438b` + fixup `c9f2d2f` |
| P0-6 Remove the foot-guns | c | `5e0a7cb` | `1342a7b` |
| P0-2 Stop re-queueing Download missing | d | `a35ee7a` | `f5f4924` + fixup `4b138a1` |

The order follows the integration rule: lane a, then lane b (it depends on a),
then c, then d, each ticket in lane order. §6 ships all of phase 0 together,
so the commit order within the phase doesn't matter for deploy.

Files changed in the phase (`git diff --stat f3586fd..a35ee7a`):
- backend `app/metadata/{apply,desired,issues,runner,plexsync,missing,routes,views}.py`
- backend `app/{plex,api,ownership,library_index,worker,cli}.py`
- 6 new test modules, plus small fixture updates in `test_attention.py`,
  `test_metadata.py` and `test_skipped.py`
- frontend `api.js`, `Library.jsx`, `SkippedList.jsx`,
  `library/{AttentionSidebar,DetailPane,IssueList}.jsx` and `library/issues.js`

## 2. Tests added (57)

Each lane recorded that its new tests failed on the code before its fix.
The review-fix tests failed on the lane's first commit. Each lane's reviewer
reproduced those failures.

- **P0-1**, `tests/test_p0_oscillation.py`, 11 tests:
  - MergeStaysMergedTestCase: `test_merge_then_check_then_auto_apply_plans_no_move`,
    `test_a_failed_tag_write_neither_moves_nor_trashes_that_track`,
    `test_undo_puts_files_tags_and_trash_back`.
  - BreakerTestCase: `test_a_file_that_went_a_b_a_is_not_moved_again`,
    `test_moves_older_than_the_window_are_allowed` (a guard; it passes before
    and after), `test_merges_are_refused_before_anything_is_trashed`.
  - LayoutGuardTestCase: `test_no_move_out_of_a_folder_just_merged_into`.
  - Review fixes, MergeMeanwhileTestCase:
    `test_a_cycle_meanwhile_sees_no_folder_split_and_keeps_the_pin`,
    `test_a_write_that_changes_the_length_is_put_back`,
    `test_a_write_that_cannot_be_put_back_is_journalled`,
    `test_a_tags_fix_that_changes_the_length_is_put_back_too`.
- **P0-3**, `tests/test_p0_plex_mirror.py`, 13 tests:
  - PagingTestCase: `test_iter_items_asks_for_a_stable_order`,
    `test_a_page_that_shifts_mid_sync_deletes_nothing`,
    `test_a_complete_listing_still_drops_what_plex_deleted`, and from review
    `test_an_add_and_a_delete_mid_sync_drop_no_live_row`,
    `test_an_answer_without_a_count_deletes_nothing`.
  - FolderViewTestCase: `test_the_tracks_listed_in_a_folder`,
    `test_anything_it_cant_read_is_none_not_empty`.
  - ReaddLiveTestCase: `test_no_readd_without_a_live_miss`,
    `test_empty_trash_waits_for_the_library_sentinel`,
    `test_clean_ghosts_waits_for_the_library_sentinel`, and from review
    `test_a_folder_plex_never_listed_is_scanned_not_re_added`,
    `test_files_a_lost_mount_keeps_hidden_are_put_back_later`,
    `test_a_re_add_puts_an_earlier_ones_files_back_first`.
  - Updated: `test_attention.ReaddTestCase` now fakes an empty live listing;
    `test_metadata.OldPlexEntriesTestCase`'s temp library has its sentinel.
- **P1-G4**, `tests/test_p0_plex_foreign.py`, 5 tests:
  - ForeignAlbumTestCase: `test_an_album_holding_another_units_files_gets_no_album_edits`,
    `test_one_match_then_plex_foreign_and_no_flip_flop`,
    `test_a_stale_match_on_a_foreign_album_is_not_applied`.
  - FlipFlopTestCase: `test_two_mbids_in_14_days_stop_further_matches`,
    `test_old_matches_do_not_trip_it` (a guard).
  - Updated: `test_metadata.SharedPlexAlbumTestCase` now expects `plex_foreign`
    and no `unmatched`. Its `plan()` and `test_attention.PlanTestCase.plan()`
    fake the new md_changes and registry reads.
- **P0-5**, `tests/test_p0_skips.py` SkipReasonTestCase, 4 tests:
  `test_a_gate_skip_records_its_reason`, `test_the_match_type_is_named`,
  `test_dest_exists_records_its_reason`, `test_a_stale_error_code_is_replaced`.
- **P0-4**, `tests/test_p0_skips.py`, 8 tests:
  - DifferentAlbumTestCase: `test_a_year_prefixed_folder_is_the_same_album`,
    `test_a_download_missing_skip_in_its_own_folder_is_not_different`,
    `test_a_sibling_edition_is_labelled`, `test_another_release_group_is_different`,
    `test_edition_words_count_without_release_groups`, and from review
    `test_a_moved_match_is_judged_by_its_folder`.
  - RecheckBannerTestCase: `test_skips_made_by_the_current_gate_are_not_offered`,
    and from review `test_download_missing_skips_are_not_offered`.
  - Updated: `test_skipped`'s helper calls the real `_different_album`; its
    assertions are unchanged.
- **P0-6**, `tests/test_p0_footguns.py`, 3 tests:
  `RebuildTestCase.test_index_rebuild_keeps_every_album_record`,
  `RebuildTestCase.test_rebuild_refuses_while_the_app_is_up`,
  `CliLoggingTestCase.test_cli_logging_never_shows_the_plex_token`.
- **P0-2**, `tests/test_p0_download_missing.py`, 13 tests:
  - QueueOnceTestCase: `test_a_second_queue_after_a_fully_skipped_job_queues_nothing`
    (the acceptance test), `test_force_queues_them_again`,
    `test_only_failed_tracks_are_tried_again`,
    `test_a_download_from_before_attempts_were_listed_counts`,
    `test_a_job_the_issue_lost_track_of_counts`, `test_attempts_survive_a_recheck`.
  - QueueOnceTestCase, from review: `test_already_tried_says_the_download_has_finished`,
    `test_a_job_removed_from_the_history_still_counts`,
    `test_a_later_failure_outweighs_a_removed_job` (a guard),
    `test_a_track_a_running_download_still_has_isnt_queued_again`,
    `test_only_what_no_running_download_has_is_queued`.
  - BulkDefaultTestCase: `test_download_missing_all_takes_only_untried_issues`,
    and from review `test_every_issue_ticked_in_review_is_taken`.

## 3. Suite and frontend build (integrated tip `a35ee7a`)

- Backend: the integration worktree's `backend` was mounted read-only, with
  `--network none`, and `-m unittest discover tests` ran. Result:
  **`Ran 451 tests in 145.635s OK`**, with 0 errors and 0 failures.
  - 451 = the baseline 394, plus lanes a (11), b (18), c (15) and d (13).
- Frontend: frontend files changed in this phase, so the build check ran:
  `docker build -t mdl-web-sandbox <wt-integration>/frontend`.
  It passed: `✓ 1084 modules transformed`, `✓ built in 10.22s`, exit 0.
  The only warning is vite's existing chunk-size warning.

## 4. Phase checks (dry runs, read-only, on `mdl-p0.db`)

Scripts and outputs are in `.cache/claude-sandbox/work/p0-integration/`, and
every run used `--network none`. The SQL ran on the host against
`file:…mdl-p0.db?mode=ro`. The Python runs copied the DB to the container's
`/tmp` and mounted the library `:ro`. They also set every write flag to false
and left `PLEX_TOKEN` empty.

### (a) §7 merge/move oscillation query: pre-deploy baseline

The query as written ran at 2026-11-02T02:37Z.

**0 rows.**
- The 48 h window holds 2 merge batches, one per album:
  - Bob Dylan *More Blood, More Tracks … (deluxe edition)* at 11-01 20:46:38;
  - *Thriller 25 [2008 Reissue]* at 11-01 20:46:39.
- Thriller's move back (20:51) is a layout `move`, not a "Merged … copies"
  batch, so this query doesn't count it.

For context, the same query over wider windows:

| window | albums merged more than once | merge batches |
|---|---|---|
| 48 h | 0 | – |
| 7 days | 0 | – |
| 14 days | 18 | 39 |

- In the 14-day window: *Thriller 25* had 4 batches, Nelly Furtado *Loose
  [2021 Expanded Edition]* had 3, and the other 16 albums had 2 each. The 16
  include both Laufey editions, Eminem *Side B*, Outkast *Stankonia* and Stone
  Temple Pilots *Tiny Music … (Super Deluxe)*.
- Most of those 39 batches ran on 10-21 and 10-22.
- All time: 196 merge batches, from 2026-10-21T00:26Z to 2026-11-01T20:46Z.

**Target after deploy:** 0 rows over 48 h.

Note for whoever runs it: the query compares ISO `…T…Z` strings with
`datetime('now', …)`, which has a space where the `T` is. So the whole first
day of the window counts, and the window is up to a day wider than 48 h.

### (b) §7 Download-missing skips by `error_code`, with `:deploy_time = '2026-10-01T00:00:00Z'`

| error_code | count |
|---|---|
| NULL | 1,008 |

- All 1,008 rows come from 346 jobs, created 2026-10-21 between 03:07:46Z and
  10:24:39Z, before P0-5 existed. So no `error_code` is expected.
- Across every skip in `tracks`: 2,598 have NULL and 1 has `dest_exists`.
- **After deploy:** rerun with `:deploy_time` set to the deploy time.
  - Target: no NULL row. Only `held_same_album` and `dest_exists` should appear.

### (c) P0-4 labelling over every Download-missing skip

Script: `work/p0-integration/p04_labels.py`. It is lane c's `p04_labels.py`
with a summary added, run on the integrated backend. It sends each skip
through the real `api._album_context` and `api._different_album`. Ground truth
comes from `library_index` and the `md_albums` release groups. Folders compare
case-insensitively.

**257 of the 1,008 are labelled "different album"** (the old rule labelled 999).
- **225 are cross-edition: same release group, other folder.** All carry the
  `edition` reason:
  - 220 have the matched file indexed in another folder of the same release group;
  - 5 are Of Monsters and Men *My Head Is an Animal* → the *[2012 Reissue]*
    folder. Their matched file is no longer indexed, but both folders'
    `md_albums` release groups agree.
- **32 are not proven cross-edition by release group:**
  - 30 have reason `album`: A$AP Rocky *(2026) Don't Be Dumb* → *Don't Be Dumb
    (Bonus Tracks)*. Both folders are gone from the index, so neither release
    group is known. By name, the match is the bonus-tracks edition of the same
    album.
  - 2 have reason `release group`: Australian Crawl *The Boys Light Up* →
    *(1980) The Boys Light Up [Single]*. That is another release group. §2
    says a single is a different album, so the label is right. These 2 are
    the only labelled skips that are not editions.

The rest:
- The 552 same-folder skips: none labelled (the old rule labelled all 552).
- The 166 whose target folder is gone, and 9 whose release group is unknown:
  none labelled.
- Of the 59 whose matched file is gone, 35 are labelled (30 album and 5
  edition, both counted above).

Re-check banner: 2,144 tracks in 429 jobs before, **102 tracks in 18 jobs**
now, none of them from a Download-missing job.

Across all 2,599 skips: no reason 2,240, `album` 132, `release group` 2,
`edition` 225. The unreviewed Skipped list shows 1,016 skips, 259 of them
labelled.

This meets the acceptance, read the way the lane c reviewer read it. "Only the
cross-edition ones are labelled" holds except for the 2 Australian Crawl
skips, which are another release group, and the 30 A$AP Rocky skips, which
are cross-edition by name.

### (d) P1-G4 on the copy

Script: `work/p0-integration/g4_sim.py`. It runs the integrated tip (`MODE=new`)
and, on the same units, the pre-G4 code (`MODE=old`: the P0-3 commit `b22f489`,
extracted to `work/p0-integration/old-p03/`). In memory it sets
`metadata_write_plex` to True. Plex is faked in `apply`, MusicBrainz is served
from `meta_cache` only, and auto-apply is patched out.

**Units whose Plex album holds another unit's indexed tracks**
(`plexsync.foreign_files`): **451 units on 184 Plex albums.**
- 166 Plex albums span several folders.
- 128 rating keys are shared by several units.

**Full check of all 451, before and after:**

| code | units planning an album-level Plex edit there | by kind | units given `plex_foreign` | Plex calls |
|---|---|---|---|---|
| pre-G4 (`b22f489`) | **5** | album genres 4, `unmatched` 1 | 0 | – |
| integrated (`a35ee7a`) | **0** | – | **156** | match 0, set_tags 0, poster 0 |

- The 5 that the pre-G4 code planned:
  - `unmatched`: Snoop Dogg *Doggystyle [2005 Reissue]* (235578);
  - album genres: Eminem *The Marshall Mathers LP* (110001), Disclosure *Settle*
    (207817), Death Cab for Cutie *We Have the Facts and We're Voting Yes*
    (209055) and Denzel Curry *KING OF THE MISCHIEVOUS SOUTH* (208205).
- All 5 now raise `plex_foreign` instead.
- The other foreign units were already covered by the old `shares_album`
  branch, or don't resolve.
- Status in the sandbox: 156 resolved, 15 review, 280 unresolved. The live
  copy has 156 / 59 / 236. Plex is planned only for resolved units, and
  those match. The 44 extra unresolved units are an artefact of serving
  MusicBrainz from the cache only.
- Every resolved foreign unit gets `plex_foreign`.
- The live planner has 0 album-level Plex edits open on these units today.
- `md_changes` shows the Plex edits applied to those 184 albums in the last
  14 days and not reverted: 29 genre, 8 match and 5 mood.

**Rating keys matched to different MBIDs within 14 days**
(the `match_flipflop` query; window from 2026-10-19T02:39Z): **2.**
- **235578, Doggystyle:** 6 matches, alternating between `a2a412fb` (the 2005
  reissue unit) and `d7a96c61` (the 1993 unit). They ran from 10-20 23:25 to
  11-01 22:32; the last one set `d7a96c61`, from the original's unit.
- **202038, ABBA *Thank You for the Music*:** 3 matches across 2 guids, from
  10-20 22:48 to 10-21 03:40.
- All time, 9 rating keys have 2 or more guids. 5 of them are fully reverted.
  The other 2 (Eurythmics 183041 and Green Day 184617) have only one guid
  inside the window.
- The breaker clears for 202038 at about 11-04 02:34Z and for 235578 at about
  11-15 22:00Z, when one guid leaves the window.

**Doggystyle.**
- Both units resolve: the original to `d7a96c61`, the reissue to `a2a412fb`.
  Both have `plex_key` 235578.
- Integrated code: both get `plex_foreign` with reason `shared`.
  - The original's issue names 12 tracks of the *[2005 Reissue]* folder.
  - The reissue's issue names 16 tracks of the original's folder.
  - Neither plans an album-level Plex edit, and 0 match, set_tags or poster calls are made.
- Pre-G4 code: the reissue unit planned `unmatched` on 235578. That would have
  been the next flip, back to `a2a412fb`.
- Lane b's replay also showed that stale `unmatched`, `moods` and `poster`
  fixes on these units now return "left alone: Plex files other albums'
  tracks in this album".
- At apply time, `apply._match` also refuses any match on 235578 while the
  flip history lasts.

### (e) P0-1 replay: *Thriller 25* and Laufey *Bewitched* (copied from lane a's report, not re-run)

Script: `work/p0-a/replay.py`. Outputs: `work/p0-a/replay-as-is.txt` and `replay-no-breaker.txt`.

Setup:
- `--network none`, with the shared DB copied to `/tmp`.
- Only the two Thriller folders and the two Laufey *Bewitched* folders were
  copied into a container-local `/tmp/library`.
- Trash batch `c580e4aed926` was put back with shutil: 27 of 27 entries, none skipped.
- The scan skipped pruning ("54 files against 64333 indexed"), so the rest of
  the index stayed. That is an artefact.
- The replay ran on lane a's first commit `564eaa6`, before the review fixup
  `761e266` changed when movers are reindexed. The fixup's own test
  (`test_a_cycle_meanwhile_sees_no_folder_split_and_keeps_the_pin`) covers
  that change. The replay was not re-run, as instructed.

1. **As-is, breaker on.** The real history makes the breaker refuse both merges:
   - Thriller: merged 11-01 20:46, moved back 20:51.
   - Laufey: merged 10-22 00:25, moved back 00:37.
   - Nothing changed: 0 tag writes, 0 file moves, 0 files trashed.
   - Both keepers got `auto_paused` rows.
   - The next pass plans the same 2 merges: 4 moves and 0 trash for Thriller,
     8 moves and 14 trash for Laufey.
2. **Breaker off** (`apply.oscillation` patched to return None), to test the
   root fix alone. There was one merge batch per album:
   - *Thriller 25*: 4 moved in, 0 trashed, 5 left behind.
   - Laufey *Goddess Edition*: 8 moved in; 27 Trash entries, 14 of them audio.
   - All 12 moved-in files carry the keeper's album, release, disc and track.
   - Then check, auto-apply, check again. Auto-apply ran 3 fixes: 2 `not_in_plex`,
     plus 1 `tags` for an album whose files aren't in the sandbox, which failed
     as expected.
   - Afterwards neither album has a split unit, 0 moves are planned, and 0
     files moved outside the merges.
   - The next pass's `copies_plan` for Thriller has 0 moves and 0 trash.
     **There is no second merge.**

The reviewer checked the outputs against the script and reproduced the suite.
Over the 10 moves currently planned in the real-data copy, the breaker
refuses none, so there are no false positives today.

## 5. Review findings

**Fixed (12, all folded into their ticket's commit):**

| ticket | finding | fix (lane commit) |
|---|---|---|
| P0-1 | `do_album_copies` reindexed retagged movers at their source before Trash and the moves. A cycle's `sync_registry` in that window saw the source folder split and dropped the source album's `md_albums` row (manual, release_mbid). | Each mover is rekeyed and reindexed right after its own move; a mover tagged but not moved is reindexed in a `finally` (`761e266`) |
| P0-1 | `_write_checked` raised after the write when the length moved by more than 0.5 s. The file stayed retagged as the keeper's track with no journal row. | The old tags are restored before raising. If the restore also fails, `TagsLeftWritten` makes `do_album_copies`/`do_tags` journal `result["before"]` (`761e266`) |
| P0-3 | An add plus a delete during a full listing keep the count equal, but the `sort=id` pages shift past a live item, whose row was dropped. | Each unseen row is looked up by key; only 404s are dropped (`79e9ca7`) |
| P0-3 | With no `totalSize` in the reply, `plex.count()` returned 0, so a full sync dropped every unseen row. | `count()` returns None and the sync treats it as short (`fedcfcb`) |
| P0-3 | When `folder_files` returned None, `do_scan` raised before any scan, losing the old scan-the-folder behaviour. | It scans and raises KeepOpen, hiding nothing (`38e9262`) |
| P0-3 | Offline branch: `restore_hidden` deleted `plex_readd_pending` with the files still hidden, and the message claimed they were put back. | The record is kept while offline, the message gives the true count, and an earlier record is put back before re-hiding (`a8427b8`) |
| P0-4 | A Download-missing skip whose target folder is gone, with a match that has moved, was judged by folder name alone. Eminem's *Music To Be Murdered By* came out "album", and Re-check would have re-downloaded what the *Side B* edition holds. | A match that is no longer indexed is judged by its folder name plus that folder's `md_albums` tags (`c9f2d2f`) |
| P0-4 | Re-check left out current-gate skips only by the P0-5 stamp. All 1,008 Download-missing skips are unstamped, so 34 were still offered. | `_cross_album_skips` requires `j.target_dir IS NULL` (`c9f2d2f`) |
| P0-2 | The already-tried path copied a stale `download.active=True`, which hid the Retry button. | `active` is recomputed from the job (`4b138a1`) |
| P0-2 | After "Remove from history", recorded attempts were ignored and the same videos were queued again. | An attempt whose job is gone still counts, unless a later job on record decides otherwise (`4b138a1`) |
| P0-2 | The implicit "untried" default also applied to Review's select-all, so ticked issues were dropped silently. | The default moved to `views.bulk_filter`, sent by the sidebar's confirm (`4b138a1`) |
| P0-2 | Dedup matched only an active job's exact filter, so a track still pending in a running job was queued in a second one. | `_in_queue()` counts any active Download-missing job of the same album (`4b138a1`) |

**Rejected:** none. **Unconfirmed:** none.

**Still open.** These are not phase 0 defects against the acceptance; they are
for the lead to schedule.

P0-1:
1. The breaker also refuses an explicit user Apply, with no override (planner
   default). Its message says the fixes are "not applied automatically for 14
   days", which suggests a manual Apply would work, but it is refused again
   while the A→B→A history is in the window. The only way to lift it early is
   to undo a history batch.
2. The layout guard knows only merges made after deploy (the
   `merge_keeper:` marker). The 196 earlier merge batches aren't covered; 0 of
   them plan a move out today. The breaker still stops a third leg.
3. Residual races, which belong to P1-F4:
   - A library scan that reads a mover between its tag write and its move can
     still index it at the source.
   - If a move or trash fails after the tag write, the mover stays in the
     source folder with the keeper's tags. That is journalled and undoable,
     and it plans a non-auto `move_conflict`.
   - There is no settling guard against active downloads yet.
4. Each move, merge or album_copies call reads about 5.6k `md_changes` rows
   (about 0.3 s). `md_changes` has no index on `target` or `applied_at`.
5. Thriller's `album_copies` is re-planned every pass with 0 moves, because 5
   files are left behind. Applying it does nothing ("nothing to merge") and
   writes no batch.

P0-3:

6. `POST /library/plex/empty-trash` (`api.py:973`) still empties Plex's trash
   without the library-online sentinel check.
7. A file Plex lists under another case spelling makes `do_scan` answer
   "mirror was behind" on every check without closing the issue
   (`album_for_paths` matches exact paths). It is harmless: no re-add happens.
8. Watch item: a short full listing keeps `plex_last_full`, so the next sync
   is full again. If Plex's hourly scan keeps the listing moving, full syncs
   repeat. Look for the "short listing" warning after deploy.

P1-G4:

9. `runner._check_unit` (`runner.py:565`) still offers a foreign album's MBID as
   a resolver hint and its genres as a fallback; only `shares_album`
   suppresses them. This is a one-line follow-up: also suppress them when
   `plexsync.foreign_files` finds files. In the replay, Doggystyle still
   resolved correctly.
10. `Library.jsx` `TAB_FOR_KIND` has no `plex_foreign` entry, so the issue
    opens the default "compare" tab.
11. `plex_foreign` can't be applied. The repair is P1-C5 (merge API spike).
    By design, `plex_shared`'s restore is still planned when an album is both
    shared and foreign.

P0-4 / P0-5:

12. Re-check still offers 102 tracks in 18 jobs. They are old-gate skips from
    before 2026-10-13, outside Download missing.
    - Re-gated today: 91 "elsewhere", 9 "have", 2 none.
    - Some of the "elsewhere" ones are the same single under another folder
      name, e.g. *rockstar (feat. 21 Savage)* against *(2017) rockstar*.
      `album_identity` keeps "(feat. …)" and "(with …)", so pressing Re-check
      would download second copies. That belongs to P1-F1.
    - 2 Sum 41 *(Live - Remastered 2026)* tracks are different recordings, and
      downloading them is right.
13. The 2 Australian Crawl skips matched a *[Single]*. §2 says that is never a
    reason to skip. They are labelled "release group", but Re-check doesn't
    offer them, because today's gate would skip them again (P1-F1/P1-B1).
14. The 166 skips whose target folder is gone stay unlabelled, because there is
    no target release group. Reading it from the `meta_cache` release can wait
    for P1-B1.
15. Cosmetic: `DownloadDetails.jsx:115` now shows a skip's reason in the error
    colour.

P0-6:

16. `README.md:414-418` still documents `mdl index --rebuild` through
    `docker compose exec`. That is now always refused while the app runs.
17. `mdl library scan --full` does the same scan without the app-running guard.
18. `library.scan`'s unreadable counter (`library.py:609`, `r[-1]` is
    `tags_version`) counts every file read as unreadable. It also affects the
    `mdl library scan` output. `_cmd_index` works around it.
19. There is no Trash purge guard: `mdl library purge` and the UI purge
    button still exist (see the ops note below).

P0-2:

20. The `skip_videos` rewritten-id mismatch is left to P1-B3.
21. A job that was cancelled and then removed from history counts as tried.
    Retry (force) is available, so nothing is lost.
22. The help text for the `tried` filter in `issues.js` ("A download ran; …")
    reads slightly off now that "tried" means a job was queued.

## 6. Needs-decision

None. The planner held none back and every lane finished with status done. The
open items above are follow-ups. The planner's defaults, which the lead can
override, are listed in `phase-0-plan.md` under "Defaults chosen". The one the
user is most likely to notice is item 1 (no override for the breaker).

## 7. Migrations

None. There is no schema change in any ticket and no `db._migrate` step, so
there is nothing to time. The new state lives in `md_state` rows:
- `auto_paused:<album key>` and `merge_keeper:<folder casefold>` (P0-1);
- `detail.download.attempts` inside `md_issues.detail` (P0-2).

`RULES_VERSION` is not bumped, so deploy does not re-plan the library. The new
behaviour (`plex_foreign`, the new labels) appears as units are re-checked.

## 8. Deploy notes (before `docker compose up -d --build`)

- **Nothing to configure.** There are no new environment variables, settings,
  dependencies or migrations. Deploy is the user's call (§8).
- **Merges that will be refused.** With the real history, the breaker refuses
  the merges of albums that went A→B→A in the last 14 days. The plan counted
  10 folder pairs: Slipknot *Vol. 3*, Snoop Dogg mixed / *Oxnard*, Death Cab
  *We Have the Facts*, Laufey *Bewitched*, Thriller, MJ *King of Pop*, and 4
  others.
  - The issue ends `failed` with "oscillating between X and Y", and that
    album's auto-apply pauses for 14 days from the refusal.
  - A manual Apply is refused too while the history is in the window.
  - Laufey *Bewitched* clears at about 11-05 00:25Z and *Thriller 25* at about
    11-15 20:46Z.
  - This is intended. These merges are now fixed at the root (moved-in files
    are tagged first), so after the window they converge in one batch.
- **New Plex warning.** About 156 resolved albums on Plex albums that mix
  folders get a "Plex album mixes folders" (`plex_foreign`) warning.
  - It can't be applied yet; the repair is P1-C5.
  - Doggystyle is among them: its match will stop flipping, and Plex album
    235578 stays as it is now (matched to `d7a96c61`, 28 tracks from both
    folders) until P1-C5.
- **Download missing.**
  - "Download missing all" now takes only the issues never tried: 51 of 303
    on the copy.
  - An album that was already tried answers "already tried", and the album
    pane offers "Retry download" (force).
  - Review's select-all still takes what its confirm dialog counts.
- **Skipped list.**
  - Each new skip shows "Why: …".
  - The "different album" labels on Download-missing skips drop from 999 to
    257, and the Re-check banner from 2,144 tracks to 102.
  - **Hold off on pressing Re-check until P1-F1** (open item 12). It would
    download some second copies of singles. That deletes nothing, but it
    adds duplicates.
- **CLI.**
  - `mdl index --rebuild` is now a full `library.scan` and refuses while the
    app answers on port 8000. To run it, stop the app first (`docker compose
    stop`), then use `docker compose run --rm --entrypoint
    /opt/engine-venv/bin/python mdl-app -m app.cli index --rebuild`. The
    README still shows the old `exec` form.
  - CLI runs no longer log the Plex token.
- **Don't purge Trash** (ops note in P0-6) until P1-R2 has audited the copies
  that the oscillation trashed. `purge_trash` still exists in the CLI and the UI.
- **After deploy, run:**
  - the §7 oscillation query (target 0 rows over 48 h);
  - the Download-missing skips query with `:deploy_time` = the deploy time
    (target: no NULL `error_code`);
  - a check of `docker logs mdl-app` for "oscillating between", "short
    listing" and "couldn't confirm with Plex".
- **Plex.** P0-3's live re-add check depends on the shape of the folder view.
  The lane b reviewer confirmed it with hand-written read-only GETs: folders
  come as `key …?parent=N`, tracks with `Media/Part/file`, and `size` equals
  the entry count. "Allow media deletion" stays off. The phase writes nothing
  to Plex that it didn't write before; it only adds guards.
