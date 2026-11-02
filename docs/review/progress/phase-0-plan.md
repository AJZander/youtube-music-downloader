# Phase 0 plan: stop the damage

Planner's re-verification of P0-1 … P0-6 and P1-G4 against the phase branch tip
`f3586fd` (`fix/review-2026-11-p0`). The code there is byte-identical to `main`
`0fec8a2` (only `docs/review/` differs), so the triage's 2026-11-02 line numbers
still hold. Each ref below was re-read anyway. Counts come from the shared copy
`.cache/claude-sandbox/mdl-p0.db` (taken 2026-11-02 10:13). They are sizes, not
test oracles.

No ticket is marked done in `progress.md`, and there are no earlier phase reports.
**No premise failed.** No ticket needs a product decision that §2 or §9 leaves
open. The judgement calls are listed under "Defaults chosen" at the end. The
lead can override any of them.

Common rules for every lane:
- No schema change is needed in phase 0. State goes in `md_state` rows, which is
  the existing pattern (`plex_readd:<folder>`). If an implementer finds they need
  a column after all, it goes through `db._migrate`, and they time it on a /tmp
  copy of the shared DB.
- Do **not** bump `runner.RULES_VERSION` in phase 0. A bump re-plans every album
  on deploy, and with auto-apply on that queues every auto fix library-wide. Every
  guard below also runs at apply time, so stale plans stay safe.
- Nothing in phase 0 calls `library.purge_trash` or deletes anything under
  `.mdl-trash`. Reviewers grep the lane diff for `purge_trash`, `.mdl-trash` and
  `unlink` to check. `plex.empty_trash` is Plex's trash, not the app's, and phase
  0 only adds guards to it.
- Real-data scripts follow CONTEXT: `--network none`, `/shared/mdl.db:ro` copied
  to `/tmp/data/mdl.db`, `settings.data_dir` set before `db.connect()`, `await
  db.close()` in a `finally`, httpx logger at WARNING, `PLEX_TOKEN=` empty.
  - MusicBrainz is served from `meta_cache`. To reach the resolver code paths,
    set `settings.musicbrainz_contact` in memory to a dummy such as
    `sandbox@invalid`, never the user's email. Patch `mb.client._fetch` to raise
    `MusicBrainzError` at once, or a cache miss sits through 5 back-off retries.
  - A replay that must plan moves sets `settings.metadata_move_files` and
    `settings.metadata_write_files` to True **in memory, and only after**
    `settings.library_dir` points at the container-local `/library` copy. Keep
    `metadata_write_plex` False unless the ticket says otherwise.
  - Touch `/library/.mdl-library-online`, or `_require_online()` refuses.

---

## P0-1 Break the merge ↔ move oscillation (lane a)

**Line refs (re-verified):**
- `metadata/apply.py`:
  - `copies_plan` 625-701;
  - `do_album_copies` 704-762: the moves at 727-742 carry no tag write, and the pin is at 751-753;
  - `do_move` 366-377, `_relocate` 380-438, `do_merge` 583-622 (trash at 602-611, before the moves);
  - `do_tags`' length-checked write 189-195;
  - `rekey_unit` 151-171, which deletes the moving row when the target key exists (154-156).
- `metadata/units.py`: `split_folder` 49-66 (splits on divergent album tags) and `sync_registry` 155-197 (drops vanished keys and their pin).
- `metadata/issues.py`: layout planning in `plan_unit` 415-441; `plan_album_copies` 823-875.
- `metadata/desired.py`: per-track tag build in `build_album` 339-397.
- `metadata/runner.py`: `auto_ids` in `_check_unit` 615-621, and the album_copies lock set in `_files_pass` 772-778.

**Premise check: holds.**
- *Thriller 25*: merge batches `0adee82ca1ad` (10-21), `8c0ebd8f10f7`, `6806ae2a447c` and `16e51aa613c5` (11-01 20:46), each followed about 5 minutes later by "Moved 4 files to Michael Jackson/(1982) Thriller".
  - The move back came from `move:Michael Jackson/(1982) Thriller 25 [2008 Reissue]|thriller`, a split unit.
  - The keeper's pin is gone: `md_albums` now has `manual=0` for `…Thriller 25 [2008 Reissue]`.
  - `album_copies:f32fab67…:Michael Jackson/(1982) Thriller` is `open` again; it is the same row, re-opened each time.
- *Laufey Bewitched*: merges `63391c14209e` (10-21) and `a94302f4210c` (10-22), each with 27 copies trashed, and each followed by "Moved 8 files to Laufey/(2023) Bewitched".
- Scanning the last 14 days of `md_changes`, the A→B→A breaker would have fired on **10 folder pairs**: Slipknot *Vol. 3* (14 files), Snoop Dogg mixed / Anderson .Paak *Oxnard* (12), Death Cab *We Have the Facts* (10), Laufey *Bewitched* (8), Thriller (4), MJ *King of Pop* editions (2), and 4 others with 1 each.

**§2 rules:**
- Duplicate = the same recording within the same release group.
- Subset editions merge into the more complete edition's folder, keeping the best file.
- When in doubt, don't pair.
- Nothing is deleted: Trash is restorable and every change is journalled.
- `/library` ignores case, so folder comparisons casefold.
- Files and tags are the source of truth.
- Not covered until P1-F4: §2 also says the manager doesn't move, merge or retag an album while a job for it is active. Phase 0 can't enforce that, so it is a risk.

**Files:** `backend/app/metadata/apply.py`, `backend/app/metadata/issues.py`,
`backend/app/metadata/desired.py`, `backend/app/metadata/runner.py`; new
`backend/tests/test_p0_oscillation.py`. Touch `backend/tests/test_attention.py`
only if an existing `AlbumCopiesTestCase` or `MergePlanTestCase` assertion has to
change, and say so in the commit.

**Fix outline:**
1. **One tag builder.** Factor `build_album`'s per-track dict (desired.py:367-391) into `desired.track_tags(album_tags, rel, t, row)`. Add `desired.album_tags_of(desired)`:
   - it returns the album-level tags, taken from a matched track's `tags` with the per-track keys removed;
   - old `md_albums.desired` rows have no stored album tags, so this derivation is what they get;
   - `build_album` also stores `desired["album_tags"]` for new rows.

   `build_album`'s output must stay identical (existing `DesiredTestCase` tests guard this).
2. **Retag before moving (root fix).**
   - `copies_plan` keeps its decisions. Each move additionally carries `"track"` (the keeper release's track entry, which it already has as `on_release[rec]`) and `"replaces"` (the keeper file it beats, if any).
   - In `do_album_copies`, work in this order:
     1. Run the breaker (step 3).
     2. Build each mover's tags with `track_tags(album_tags_of(ctx.desired), rel, move["track"], row)`. Write them at the **source** path with the same length-checked write `do_tags` uses (factor that into a shared helper), and journal each as target `file`, field `tags`, in the merge batch.
     3. A mover whose write fails is dropped: it is neither moved nor trashed, and its `replaces` file is taken out of the trash list.
     4. Trash, then move. Then `rekey_paths`, then **`reindex(moved destinations)`**, so `library_index` carries the new album tag before any `units.split_folder` call.
     5. Pin the keeper as now (`manual=1`, `release_mbid`), and write the step 4 marker.
   - If `settings.metadata_write_files` is off, raise `ApplyError` ("merging needs METADATA_WRITE_FILES, or the moved files would split the album"). An untagged merge is exactly what loops.
   - Undo still works. `revert()` walks the batch newest-first: moves come back, the Trash is restored, then tags are restored at `current_path(src) == src`. A test proves this round trip.
3. **Circuit breaker.**
   - Add `issues.OSCILLATION_WINDOW = 14 * 86400` and an `oscillating(moves)` helper (in apply.py, or in issues.py so the planner can share it).
   - It does **one** query per call: `SELECT ref, new_value, applied_at FROM md_changes WHERE target='move' AND field='file' AND reverted_at IS NULL AND applied_at >= <utc ISO now-14d>`. That is about 0.3 s on the real 285k-row table. There is no index on `ref`, and adding one is P1-F5.
   - It then follows each planned `src → dst` back along exact journalled paths. The file arrived at `src` from folder(`dst`), and before that it arrived there from folder(`src`): that is A→B→A, so refuse the third leg. Folders are compared casefolded. A same-folder rename compares full paths.
   - Call it at the top of `do_move`, of `do_merge` (**before** `trash_paths`) and of `do_album_copies` (**before** any tag write or trash).
   - On a hit, raise `ApplyError(f"oscillating between {A} and {B}")`, so the runner marks the issue `failed`, and write `md_state['auto_paused:<ctx.key>'] = {"until": now+14d, "reason": …}`.
   - `Runner._check_unit` returns `auto_ids=[]` while the album is paused. This one spot covers both the cycle and the `_apply_plex` follow-ups. An explicit user Apply still reaches the executor, where the breaker refuses again.
4. **Layout guard.**
   - `do_album_copies` writes `md_state['merge_keeper:<folder casefold>'] = {"at", "release", "batch"}`.
   - In `plan_unit`'s layout block (issues.py:416-441): when the unit's current folder carries a marker younger than 14 days, plan no `move`/`move_conflict` whose destination folder differs (casefold) from the current folder. Renames inside the folder are still planned.

**Acceptance tests (write first, record the failures):** `tests/test_p0_oscillation.py`
- `MergeStaysMergedTestCase.test_merge_then_check_then_auto_apply_plans_no_move` *(ffmpeg is in the image)*. Real code paths, with only MusicBrainz, Last.fm and Plex faked.
  - **Setup:** real audio files in a temp library. `Art/(1982) Thriller` holds r1–r3, tagged album `Thriller` with rel-orig. `Art/(1982) Thriller 25 [2008 Reissue]` holds r1, r2 and r4, tagged `Thriller 25` with rel-25. Both releases are in rg-1, and rel-25 lists r1–r5.
  - **Run:**
    1. `library.scan(full=True)`, then `units.sync_registry()`, then `Runner().check_unit` for each unit.
    2. `issues.plan_album_copies()` and `save`.
    3. `Runner._files_pass([album_copies id])`.
    4. Scan, `sync_registry`, and `check_unit` for every key.
    5. `_files_pass(auto_ids)`.
    6. Scan, `sync_registry` and check again.
  - **Asserts:**
    - (a) no `md_albums` key containing `|`;
    - (b) no `move`/`move_conflict` issue in `open`/`queued`/`failed`;
    - (c) `tagio.read` of every moved-in file shows the keeper's album, album artist, MBIDs, disc and track;
    - (d) the keeper row still has `manual=1` and rel-25;
    - (e) a second `plan_album_copies` plus `copies_plan` has no moves and no trash;
    - (f) the §7 oscillation query, without its 2-day window, returns 0 rows.
  - Before the fix this fails at (a)/(b): a `…|thriller` unit plans an auto move back.
- `MergeStaysMergedTestCase.test_a_failed_tag_write_neither_moves_nor_trashes_that_track`: patch `tagio.write` to fail for one mover. That file stays put, its `replaces` copy is not in `library_trash`, and the other movers proceed.
- `MergeStaysMergedTestCase.test_undo_puts_files_tags_and_trash_back`: run `apply.revert(batch)` on the merge. Every file is back at its old path with its old tags, and the Trash is restored.
- `BreakerTestCase.test_a_file_that_went_a_b_a_is_not_moved_again`:
  - journal two real `md_changes` move rows (A→B 3 days ago, B→A 2 days ago) for a real file now at A;
  - run `apply.do_move` toward B through `Runner._apply_simple`;
  - the issue ends `failed` with "oscillating between", the file is still at A, the `auto_paused:` row exists, and `_check_unit(...)["auto_ids"] == []`.
- `BreakerTestCase.test_merges_are_refused_before_anything_is_trashed`: `do_album_copies` and `do_merge` with an oscillating mover leave `library_trash` empty, write no tags and move nothing.
- `BreakerTestCase.test_moves_older_than_the_window_are_allowed`: the same history 15 days old lets the move proceed.
- `LayoutGuardTestCase.test_no_move_out_of_a_folder_just_merged_into`: after a real `do_album_copies`, `plan_unit` for a unit in the keeper folder whose desired folder is elsewhere plans no `move`/`move_conflict`, while a rename inside the folder is still planned.

**Real-data replay** (scripts in `work/p0-a/`; this is the "replay that needs real file moves"):
- Copy only these folders from `/reallib` into container `/library`: `Michael Jackson/(1982) Thriller`, `Michael Jackson/(1982) Thriller 25 [2008 Reissue]`, `Laufey/(2023) Bewitched`, `Laufey/(2023) Bewitched_ The Goddess Edition`.
- **Laufey needs its pre-merge state back**, because today the two Bewitched folders share no recording, so no merge is planned at all.
  - Copy, with plain `shutil` in the script and never app code, the Trash batch of merge `a94302f4210c`: library_trash batch `c580e4aed926`, 27 rows at `/reallib/.mdl-trash/20261022-002502-c580e4aed926/Laufey/…`.
  - Put them back at their `original_path` inside the container-local `/library`, skipping any name that is taken.
- Run the scan, the Thriller merge (whose issue is open today) and the Laufey merge (re-planned), then check, auto-apply and check, as in the unit test, through the runner's methods and not its loops.
- **Expect:** one merge batch per album and no planned move back. Report the batches and moves before and after. The rest of the library looks missing in this replay; that is an artefact.

**Risks to files:**
- Tags are written on files that are about to move. That is journalled and undoable, but a crash between the write and the move leaves a retagged file in its old folder. It then splits as `src|<keeper album>`, resolves to the keeper and plans a non-auto `move_conflict`, so it converges.
- Trash runs after the tag writes but before the moves. Movers that failed their write keep their pair out of Trash.
- No settling guard yet (P1-F4): a merge during an active download into either folder can still race the importer.
- A release that lists one recording twice still collapses to one entry of `on_release`, which is existing behaviour (P1-B1).
- The 5 Thriller files the merge leaves behind make the source folder show "4 of 9 missing". A Download missing from there would re-fetch copies the keeper holds; P0-2 limits the repeat, P1-B1 fixes it.

---

## P0-2 Stop re-queueing Download missing that already ran (lane d)

**Line refs (re-verified):**
- `metadata/missing.py`: `queue_download` 292-343. The guard at 314-318 only looks at **active** jobs. The issue is reset to `open` at 336-341, and `detail.download` is overwritten at 321-323, which loses earlier job ids.
- `metadata/apply.py` `do_download_missing` 275-293, and `runner.py` KeepOpen to open at 930-932. Both are left untouched.
- `metadata/routes.py`: `apply_issues` 269-275 and `_selected` 249-266, which take every open/failed issue of the kind with no filter; `album_download_missing` 202-215.
- `metadata/views.py` `ISSUE_FILTERS` tried/untried 103-106.
- Frontend:
  - `library/AttentionSidebar.jsx:48-53`, the "Download missing all (N)" button that calls `onApply(group, false)`;
  - `Library.jsx:387-389`, the modal count;
  - `library/DetailPane.jsx:146-175`, "Download missing / Queue again";
  - `api.js:215`.

**Premise check: holds.**
- 98 (`source_url`, `target_dir`) pairs have two or more Download-missing jobs (112 by `source_url`).
- 252 of 303 open missing-tracks issues already have a `download.job_id`.
- **1,013 Download-missing track rows carry a `provider_track_id` that is not in their job's `track_filter`.** `_use_album_audio` rewrote them (worker.py:319-322), so "already tried" can't be matched on `provider_track_id` alone. Of 1,675 rows, 1,616 map back to a filter id by (browse id, title) through `catalog_tracks`.

**§2 rules:**
- Download missing is never automatic; only the user starts it.
- The leftovers go to a "Tried" bucket that is never re-queued automatically, and the user can retry or accept from there. Here, retry = `force`.

**Files:**
- Backend: `backend/app/metadata/missing.py`, `backend/app/metadata/routes.py`, `backend/app/metadata/views.py`.
- Frontend: `frontend/src/api.js`, `frontend/src/components/library/DetailPane.jsx`, `frontend/src/components/library/AttentionSidebar.jsx`, `frontend/src/components/Library.jsx`.
- Tests: new `backend/tests/test_p0_download_missing.py`.
- **Do not edit `apply.py` or `test_attention.py`** (they belong to lanes a and b). `FileInDownloadsTestCase` must stay green as it is.

**Fix outline:**
1. Change the signature to `queue_download(release_mbid, skip_videos=…, force=False)`. After `_match` it computes `tried`:
   - **Previous jobs** are every job whose id is in `detail.download.attempts` or `detail.download.job_id`, plus every job with `source_url = url AND target_dir IS NOT NULL`. That catches the 98 doubles, whose first job id was overwritten.
   - **Per job:** a video counts as tried when a track row is `completed` or `skipped_duplicate` and either:
     - its `provider_track_id` is in the job's filter, or
     - its normalised title (and `track_number` when both have one) equals that of the album track (from the fetched `album["tracks"]`) whose `video_id` is in the filter.

     Shortcut: when every row of a job is terminal completed or skipped, the job's whole filter is tried. Failed, cancelled and needs-review tracks stay eligible.
2. Without `force`, drop the tried videos. If nothing is left, return `{"queued": 0, "already_tried": n, "message": "already tried"}`, queue no job, and set the issue's `error` to "already tried: N tracks were downloaded or skipped by an earlier download; Retry from the album to fetch them again". The issue stays in its state; KeepOpen still turns it open.
3. Record `detail.download.attempts`, a list of `{"job_id", "queued_at", "videos"}` appended on each queue. It is seeded from a legacy `download.job_id` when missing, and carried over when `detail.download` is replaced. `refresh()`, `_file()` and `want_download()` already keep the `download` dict, and a test confirms the attempts survive.
4. **Bulk default.** In `routes.apply_issues`, when `kind == "missing_tracks"` and neither `ids` nor `filter` is given, use `filter = "untried"`.
   - Redefine the views filter: "tried" means `json_array_length(detail,'$.download.attempts') > 0 OR json_extract(detail,'$.download.job_id') IS NOT NULL`, and "untried" is the negation. A wanted-only issue (YouTube was busy) counts as untried, and `wanted_downloads()` re-asks it anyway.
   - `views.attention` adds `bulk_count` (open and untried) for missing_tracks. The sidebar button and `Library.jsx`'s modal use `group.bulk_count ?? group.count - queued_count`.
5. **Retry.** `/album/download-missing` takes `{key, force}`. `api.libraryDownloadMissing(key, force)` passes it. DetailPane says "Already tried" on `already_tried` and offers "Retry download" (force) when `m.download.attempts` is non-empty.

**Acceptance tests:** `tests/test_p0_download_missing.py`. Use a real temp DB, the real `worker.enqueue` (with `wake` patched) and the real `compute`. Only MusicBrainz's `release` and the YouTube album lookup are faked.
- `QueueOnceTestCase.test_a_second_queue_after_a_fully_skipped_job_queues_nothing`:
  1. The first `queue_download` creates J1.
  2. Insert J1's tracks as `skipped_duplicate` with rewritten `provider_track_id`s and the same titles, and set J1 `completed`.
  3. The second call returns `queued == 0` and `message == "already tried"`, the `jobs` row count is unchanged, and `detail.download.attempts` lists J1.
- `QueueOnceTestCase.test_force_queues_them_again`: a new job, and 2 attempts.
- `QueueOnceTestCase.test_only_failed_tracks_are_tried_again`: when J1 had one failed track, the next queue's `track_filter` is just that video.
- `QueueOnceTestCase.test_attempts_survive_a_recheck`: `missing.refresh(rel)` keeps the attempts.
- `BulkDefaultTestCase.test_download_missing_all_takes_only_untried_issues`: `routes.apply_issues(ApplyIn(kind="missing_tracks", only_auto=False))`, with `runner.request_apply` spied, receives only the untried id. `views.attention()` gives `bulk_count == 1`.

Frontend: build check with `docker build -t mdl-web-sandbox <worktree>/frontend`.

**Real-data dry run** (on a /tmp copy, read-only logic, no YouTube calls): for every open missing_tracks issue, count how many of its last job's videos `tried` would drop, and how many issues a bulk run would now take (untried) compared with before.

**Risks:** no file moves. This can under-download a track whose earlier "skip" was wrong (the gate bugs in 4.B); Retry with force covers that, as §2 intends. `skip_videos`' rewritten-id mismatch (worker.py:319-322) is not fixed here; P1-B3 fixes it.

---

## P0-3 Make the Plex mirror trustworthy before anything acts on it (lane b, first)

**Line refs (re-verified):**
- `plex.py` `iter_items` 189-203 pages with `X-Plex-Container-Start`/`Size` and no `sort`.
- `metadata/plexsync.py` `sync` 106-170. A full sync deletes every row unseen this run (`DELETE … WHERE seen_at < started`, 140-146) whatever it saw.
- `metadata/apply.py`:
  - `do_scan` 833-856: "missing" means missing from the mirror (839-840);
  - `readd` 858-893: `empty_trash` at 886 with no sentinel check;
  - `clean_ghosts` 1213-1227: `empty_trash` at 1225.

**Premise check: holds.** The code has no stable sort and no check of the run's total before deleting, and the re-add trusts the mirror alone. The mirror holds 75,191 rows (64,333 tracks).

**§2 rules:**
- Files and tags are the truth.
- The app must never need Plex to delete media ("Allow media deletion" is off).
- When in doubt, keep. A missed re-add is harmless; an `empty_trash` during a mount blip drops unrelated items.

**Files:** `backend/app/plex.py`, `backend/app/metadata/plexsync.py`,
`backend/app/metadata/apply.py`; new `backend/tests/test_p0_plex_mirror.py`; update
`backend/tests/test_attention.py::ReaddTestCase.test_only_an_issue_that_outlived_its_scans_is_re_added`
(622-634). It must now fake the live check.

**Fix outline:**
1. `iter_items` sends `sort="id"`; per the lead, this is already verified on this server. Its other callers (`sync_recent`, `find_track_by_path`) inherit it.
2. In a full `sync`, count the **distinct** rating keys seen per type. Delete unseen rows **per type** (`DELETE … WHERE type = ? AND seen_at < ?`), and only for types where seen ≥ `counts[type]` (taken from `plex.count` at the start). Otherwise log a warning ("listing came up short: X of Y; unseen rows kept") and don't advance `plex_last_full`.
3. Live miss before a re-add.
   - Add `plex.folder_files(library_relpath) -> set[str] | None`. It walks the read-only folder view: `GET /library/sections/{id}/folder`, then `?parent=<id>` along each path component, matching `Directory.title` casefold and NFC. It returns the NFC library-relative paths of the tracks listed there (`Metadata[].Media[].Part[].file`), or **None on any error or unexpected shape**.
   - In `do_scan`, only paths missing from both the mirror and the live listing are re-added.
     - If `live` is None: raise `ApplyError("couldn't confirm with Plex that the files are missing; not re-adding")`. This fails safe.
     - If nothing is really missing: return a note that the mirror was behind. A partial `sync_recent` or `refresh_items` is optional.
   - The response shape is unverified on this server, and phase 0 makes no live Plex calls (lead's note). The fail-safe means a wrong guess can only stop re-adds, never cause one.
4. `readd` checks `library_online()` immediately before `plex.empty_trash()`. If the mount is offline, skip the empty and log it. The `finally` still restores the hidden files. `clean_ghosts` gets the same one-line guard; that overlaps P2-C8 and is harmless.

**Acceptance tests:** `tests/test_p0_plex_mirror.py`. These drive the real `PlexClient.iter_items`, `plexsync.sync` and `apply.do_scan`/`readd` against a fake HTTP layer: patch `PlexClient.get` or `request`, routed by path, and set `settings.plex_token` to a dummy.
- `PagingTestCase.test_iter_items_asks_for_a_stable_order`: every page request carries `sort=id`.
- `PagingTestCase.test_a_page_that_shifts_mid_sync_deletes_nothing`:
  - the fake server reports `totalSize = N` and shifts its order between pages unless `sort=id` is sent;
  - with sort, it also returns a short listing (N-1 distinct);
  - pre-existing mirror rows survive `sync(full=True)`, and `plex_last_full` is unchanged.
- `PagingTestCase.test_a_complete_listing_still_drops_what_plex_deleted`: when all N are seen, the stale rows go.
- `ReaddLiveTestCase.test_no_readd_without_a_live_miss`, with an issue older than `READD_AFTER` and the mirror lacking the path:
  - a live listing that contains the path: no `readd`;
  - `folder_files` returns None: `ApplyError` and no `readd`;
  - a live listing without the path: `readd` is awaited.
- `ReaddLiveTestCase.test_empty_trash_waits_for_the_library_sentinel`: a real `readd` on a temp library. The fake `wait_for_scan` deletes the sentinel, so `plex.empty_trash` is never awaited and the hidden file is back under its name.

**Risks:**
- No file moves, but the re-add hides files under dot names; that path now fires less often.
- Keeping unseen rows after a short listing can leave a stale mirror row or two. These can surface as `plex_ghost`, whose clean-up now also checks the sentinel before `empty_trash`.

---

## P1-G4 One Plex album, one unit: stop the match flip-flop (lane b, after P0-3)

**Line refs (re-verified):**
- `metadata/issues.py`:
  - the Plex section of `plan_unit` 443-511: the shared branch at 444-465 (only `shares_album`, which counts other release groups as foreign), and the album-level edits at 466-511 (unmatched 472-476, wrong_match 477-490, genres 491-503, moods 504-507, poster 508-511);
  - `KINDS` 30-118, `UNIT_KINDS` 119-122.
- `metadata/plexsync.py`: `album_for_paths` 338-366 (majority album, 357), `album_others` 369-382, `album_share`/`shares_album` 385-426.
- `metadata/apply.py`: `_shared` 998-1006, `do_genres` 1009-1029, `do_moods` 1074-1082, `_match` 1093-1126, `do_match` 1129-1136, `do_poster` 1156-1174.

**Premise check: holds.**
- Plex album 235578 holds 16 tracks of `Snoop Dogg/(1993) Doggystyle` and 12 of `…[2005 Reissue]`. Both units have `plex_key = 235578`.
- `md_changes` shows 13 alternating `match` edits on 235578, between `mbid://d7a96c61…` and `mbid://a2a412fb…`, from 10-14 to 11-01 22:32.
- A second flip-flop exists within 14 days: ABBA *Thank You for the Music* (rating key 202038, 3 matches, two units).
- `shares_album` misses both, because the other folder is the same release group.
- 200 Plex albums hold tracks from more than one raw folder; fewer once disc subfolders are folded into their unit.

**§2 rules:**
- Distinct editions stay separate.
- Never edit titles through the API.
- Treat a match edit as a possible merge (the unverified note).
- Point 3 of the invariant: an album holds no tracks from other folders.

**Files:** `backend/app/metadata/plexsync.py`, `backend/app/metadata/issues.py`,
`backend/app/metadata/apply.py`, `frontend/src/components/library/issues.js` (label
and help for `plex_foreign`, which then needs a frontend build check); new
`backend/tests/test_p0_plex_foreign.py`; update
`backend/tests/test_metadata.py::SharedPlexAlbumTestCase.test_the_same_albums_tracks_from_another_folder_are_not_another_album`
(621-624). It currently expects `unmatched` for an album holding another folder's
files. §2's invariant wins, so it now expects `plex_foreign` and no `unmatched`.

**Fix outline:**
1. **One small function for P1-C4 to replace:** `plexsync.foreign_files(info, key) -> list[dict]`.
   - It works from the mirror (`plex_items.parent_key` and `path`/`folder`): the Plex album's indexed tracks (`album_for_paths`' `others`, which already joins `library_index`, so ghosts drop out) that belong to **another unit**.
   - Another unit means a different `unit_dir(folder)`, compared casefold and NFC. For a split key (`F|x`), a same-folder file of another split unit also counts.
   - This avoids a false hit on our own just-imported file whose `library_index.dirpath` is still NULL.
2. **Plan.** When `foreign_files` is non-empty:
   - plan **no** unmatched, wrong_match, album genres, moods or poster;
   - keep track-level genres for our own tracks, as the shared branch does;
   - keep the existing `plex_shared` restore only for the `shares_album` case;
   - add `plex_foreign` (warning, `applyable: False`, in `UNIT_KINDS` so it closes once the album is clean). Its detail is `{rating_key, title, folders, units, reason: "shared"}`, naming the other units.
3. **Flip-flop breaker.**
   - `issues.match_flipflop(rk)` reads `md_changes` (`target='plex'`, `field='match'`, `ref=rk`, `reverted_at IS NULL`, `applied_at` within 14 days). It fires when the distinct `new_value` guids number two or more, and returns them with their `album_key`s.
   - `plan_unit` plans `plex_foreign` (reason `flip_flop`, both units named) instead of unmatched/wrong_match.
   - `apply._match` refuses with `ApplyError` before `plex.match`. That covers artist matches too, harmlessly.
4. **Apply-time guard for stale plans.** `apply._shared(ctx, rk)` also returns True when `foreign_files` is non-empty. Then `do_match`, `do_moods` and `do_poster` return `SHARED_NOTE`, and `do_genres` sets track genres only.

**Acceptance tests:** `tests/test_p0_plex_foreign.py`. Use a real temp DB, real `plex_items` and `library_index` rows, the real `album_for_paths`, `plan_unit` (or `Runner.check_unit` with MusicBrainz faked and `metadata_write_plex` on) and the real executors, with `apply.plex` faked to record its calls.
- `ForeignAlbumTestCase.test_an_album_holding_another_units_files_gets_no_album_edits`: album 50 holds unit X's and unit Y's tracks. X's plan has `plex_foreign` naming Y's folder, has no unmatched/wrong_match/moods/poster, and its genres issue is track-only.
- `ForeignAlbumTestCase.test_one_match_then_plex_foreign_and_no_flip_flop`, the Doggystyle shape:
  1. While album 50 holds only X's files, X is matched once (the fake `plex.match` is called once).
  2. Move Y's tracks into album 50 in the mirror.
  3. Re-check X and Y, and run any queued plex stage: both have `plex_foreign`, and `plex.match` is still at 1 call.
- `ForeignAlbumTestCase.test_a_stale_match_on_a_foreign_album_is_not_applied`: `do_match` returns `SHARED_NOTE` without calling `plex.match`.
- `FlipFlopTestCase.test_two_mbids_in_14_days_stop_further_matches`: seeded `md_changes` rows (d7a96c61 then a2a412fb on rk 235578). `_match` raises naming both album keys, `plex.match` is not awaited, and `plan_unit` yields `plex_foreign` with reason `flip_flop`.
- `FlipFlopTestCase.test_old_matches_do_not_trip_it`: older than 14 days, the match is allowed.

**Real-data replay** (`work/p0-b/`, /tmp DB copy, `metadata_write_plex=True` in memory with a fake `apply.plex`, no token):
- Run `Runner.check_unit` for both Doggystyle units and for the two ABBA units (202038).
- Both get `plex_foreign`, and nothing else album-level is planned.
- Running their plex stage makes **zero** `match`/`set_tags(ALBUM)`/`upload_poster` calls.
- Report the library-wide count of units that would get `plex_foreign`, from one pass of `foreign_files` over the units whose `plex_key` holds other units' tracks.

**Risks:**
- No file risk.
- Album-level Plex edits stop for about 160 albums until P1-C4 and P1-C5 reconcile them; existing locks stay.
- The plex_foreign issues appear only as albums are re-checked (no rules bump). The apply-time guard covers queued or stale plans.

---

## P0-5 Every skip records why (lane c, first)

**Line refs (re-verified):**
- `worker.py`: the gate at 362-372 writes `SKIPPED_DUPLICATE` with no `error_code` or message; `_handle_import_failure` `dest_exists` at 619-630 does the same.
- `library_index.py` `find_duplicate` 19-32 returns only the path.
- `ownership.py`: `_match` 150-158 has no match type; `lookup` 188-225 (video 202, exact 210-218, fuzzy 211-221).

**Premise check: holds.** Of 2,599 skipped tracks, only 1 has an `error_code`, a stale one from an earlier failure. The gate never clears it.

**§2 rules:** Safety, meaning every change is explained; P1-B3 later replaces the gate.

**Files:** `backend/app/ownership.py`, `backend/app/library_index.py`,
`backend/app/worker.py`, `backend/app/api.py` (only if `list_skipped` needs a
`reason` field), `frontend/src/components/SkippedList.jsx`; new
`backend/tests/test_p0_skips.py`. Lane c may adjust `backend/tests/test_dedup.py`
and `test_skipped.py` if needed; no other lane touches them.

**Fix outline:**
1. `ownership._match(..., via=)`, where `via` is `"video"` (`same_video`), `"exact title"` (the exact list minus `same_video`) or `"fuzzy"`. This is additive; `test_catalog.py:675` only reads `library`/`library_mismatch`.
2. `library_index.find_duplicate_match(...) -> dict | None` returns the full lookup when it is `have`. `find_duplicate` keeps returning the path for its other callers and tests. **The gate's decision does not change.**
3. The worker's skip sets `error_code='held_same_album'` and `error_message="Already in the library: <path> (matched by <via>[; version or length differs])"`, overwriting any stale code. `dest_exists` sets `error_code='dest_exists'` and `error_message=f"A file is already at {exc.path}"`.
4. SkippedList shows `t.error_message` under "Already in library".

**Acceptance tests:** `tests/test_p0_skips.py`
- `SkipReasonTestCase.test_a_gate_skip_records_its_reason`:
  - a real temp DB (job, track, and a `library_index` row on the same album), with the real `Worker._process_track`, `find_duplicate` and `ownership`;
  - the track ends `skipped_duplicate` with `error_code == 'held_same_album'`, and a message holding the path, "exact title" and the mismatch note;
  - `api.list_skipped()` returns that `error_message`.
- `SkipReasonTestCase.test_the_match_type_is_named`: video and fuzzy variants.
- `SkipReasonTestCase.test_dest_exists_records_its_reason`: `_handle_import_failure` with `ImportError_("dest_exists", path=…)`.
- `SkipReasonTestCase.test_a_stale_error_code_is_replaced`.

**Risks:** none to files. This is a message only.

---

## P0-4 Fix the Skipped list's "different album" label and its Re-check loop (lane c, after P0-5)

**Line refs (re-verified):**
- `api.py`: `_matched_album` 252-257, `_album_key` 260-273, `_different_album` 276-279. The last compares the raw folder name, so `(2019) X` ≠ `X`.
- `api.py`: `list_skipped` 282-308, `_cross_album_skips` 311-320, `skipped_recheck_preview` 323-327, `skipped_recheck` 330-.
- `library.album_identity` 182-195 strips years and `[…]` but keeps `(Deluxe …)`.
- `SkippedList.jsx`: banner 55-70, chip 111-113.

**Premise check: holds.**
- Of the 1,008 Download-missing skips (`jobs.target_dir` set), **558 matched a file inside the job's own `target_dir`**, and today's label calls nearly all of the 1,008 "different album".
- The Re-check preview re-queues every flagged skip whatever gate made it, so the gate skips them again and the banner returns.

**§2 rules:**
- A song on a single, a compilation or another album (another release group) is never a duplicate.
- "Nothing another edition holds is re-downloaded", so a cross-edition skip is information for the user, not a re-download candidate.

**Files:** `backend/app/api.py`, `frontend/src/components/SkippedList.jsx`; tests in
`backend/tests/test_p0_skips.py`. The existing `test_skipped.py` and
`test_dedup.py::RecheckTestCase` cases must stay true.

**Fix outline:**
1. `async _album_context(rows)` preloads:
   - for the matched paths: `library_index` `dirpath`, `album`, `artist_dir`, `mb_releasegroupid`;
   - for each job: `target_dir`, plus that folder's `md_albums.releasegroup_mbid` (key or dirpath equal).
2. `_different_album(row, ctx) -> str | None` returns a reason, in this order:
   1. **Download missing, folder:** the job has a `target_dir` and the matched file's `unit_dir` equals it (casefold, disc subfolders folded in): not different. Same release group but another folder: `"edition"`.
   2. **Download missing, release groups:** both release groups known and different: `"release group"`.
   3. **Fallback, names:** the wanted album's `album_identity` is disjoint from the matched file's `row_album_identity` (its tag and folder): `"album"`. `album_identity` strips the year and keeps edition words.

   `list_skipped` keeps `different_album: bool` and adds `different_reason`. The UI chip reads "other edition" when the reason is `edition`.
3. **Re-check loop.** `_cross_album_skips` leaves out skips with `error_code IN ('held_same_album','dest_exists')`: those were made by the current gate (P0-5's marker). It also leaves out `reason == "edition"`, because the gate would skip those again, so the banner's "these would download" would be false. Old unmarked skips that get re-queued come back stamped, so the loop converges after at most one round.

**Acceptance tests:** `tests/test_p0_skips.py`
- `DifferentAlbumTestCase.test_a_year_prefixed_folder_is_the_same_album`: `(2019) Rare Ass Shit` against `Rare Ass Shit`.
- `DifferentAlbumTestCase.test_a_download_missing_skip_in_its_own_folder_is_not_different`: a real DB with a job `target_dir`, the matched file in that folder's `Disc 2`, and different case.
- `DifferentAlbumTestCase.test_a_sibling_edition_is_labelled`: same release group, other folder, reason `edition`.
- `DifferentAlbumTestCase.test_another_release_group_is_different`.
- `DifferentAlbumTestCase.test_edition_words_count_without_release_groups`: `Seventy` against `Seventy (Deluxe Edition)` is different.
- `RecheckBannerTestCase.test_skips_made_by_the_current_gate_are_not_offered`:
  - a stamped skip and an `edition` skip are left out of `skipped_recheck_preview()` and `skipped_recheck()`;
  - a legacy compilation skip is still re-queued, which is today's `RecheckTestCase` behaviour.

**Real-data check** (`work/p0-c/`, /tmp copy):
- Classify all 1,008 Download-missing skips by ground truth: same folder, another folder in the same release group, another release group, or matched file gone (not indexed).
- Show the labelled set equals the other-folder classes (it should be about 352 + 35 cross-edition, plus any cross-release-group), and that none of the 558 same-folder ones is labelled.
- Also report the new Re-check count.

**Risks:** none to files. The Re-check now re-queues fewer tracks.

---

## P0-6 Remove the foot-guns (lane c, last)

**Line refs (re-verified):**
- `library_index.py` `rebuild` 77-122: `DELETE FROM library_index`, then 8-column rows with no `dirpath`. `units.all_units` reads `WHERE dirpath IS NOT NULL`, so `sync_registry` then deletes every `md_albums` row.
- `cli.py`: `_cmd_index` 48-62; `logging.basicConfig(level=logging.INFO…)` at **159**, with no httpx silencing (`main.py:30` has it). httpx logs full URLs at INFO, and `plex.request` puts `X-Plex-Token` in the query.
- Trash purge: `cli.py:117-125`, `api.py:886`, `library.py:1567`.

**Premise check: holds.**
- **What exists to tell "the app is up":** there is no pid file, lock or heartbeat in `data_dir` or the DB. `data/` holds only `mdl.db*`, `logs/`, `backups/`, `cookies/`, `legacy/` and the Spotify token.
- `md_state` and `md_runs` rows are written only periodically, and `app.log`'s mtime is unreliable.
- The one reliable signal that already exists is **the app's own HTTP listener on port 8000**:
  - uvicorn binds `0.0.0.0:8000` (`entrypoint.sh`), and the compose healthcheck already probes `http://localhost:8000/api/v1/health`;
  - the README's documented CLI path is `docker compose exec mdl-app … app.cli`, which runs inside that same network namespace.

**§2 rules:** Nothing is deleted; never purge Trash (ops note); never log the token.

**Files:** `backend/app/library_index.py`, `backend/app/cli.py`; new
`backend/tests/test_p0_footguns.py`.

**Fix outline:**
1. `library_index.rebuild()` becomes a thin alias for `library.scan(full=True, progress_cb=…)` (lazy import, which avoids the `ownership` cycle). `_cmd_index` prints the scan's counts. P3-H2 removes the dead name later.
2. Before the rebuild, `_cmd_index` calls `_app_running()`: a TCP connect to `127.0.0.1:APP_PORT` (module constant 8000) with a 1 s timeout. If it connects, print "the app is running: stop it first (docker compose stop mdl-app), then run this with docker compose run --rm mdl-app …" and return 1.
   - Optionally also try the compose host name `mdl-app:8000`; a DNS failure means not running.
   - Limitation to document: a separate container on another network can't see the live app. Since the rebuild is now a non-destructive full scan, that residual risk is small.
3. Move logging setup into `cli._setup_logging()`: `basicConfig` as now, then `logging.getLogger("httpx").setLevel(logging.WARNING)` (and `httpcore`).
4. The Trash ops note: no code. The reviewer checks that no phase-0 diff calls `purge_trash` and that the integrator's report repeats the note. A purge guard is not trivial: it needs a decision about the UI purge button. Not built.

**Acceptance tests:** `tests/test_p0_footguns.py`
- `RebuildTestCase.test_index_rebuild_keeps_every_album_record`:
  1. One real audio file in a temp library, then a real `library.scan`, then `units.sync_registry`, then set `md_albums manual=1`.
  2. Run `cli._cmd_index(True)` (with `_app_running` False), then `sync_registry` again.
  3. The row still exists with `manual=1`, and `library_index` keeps `dirpath`, title and `mb_*`. This fails before the fix: the row is gone.
- `RebuildTestCase.test_rebuild_refuses_while_the_app_is_up`: bind a listener on 127.0.0.1 with an ephemeral port and patch `APP_PORT`. The call returns non-zero, the patched `library.scan` is not awaited, and the index is unchanged.
- `CliLoggingTestCase.test_cli_logging_never_shows_the_plex_token`: after `cli._setup_logging()`, an `httpx.AsyncClient(transport=httpx.MockTransport(...))` GET with `X-Plex-Token=SECRET`, captured on a root handler, never contains `SECRET`. This fails before the fix.

**Risks:** a full scan reads every file over CIFS; it is slow but writes nothing to the library.

---

## Lanes

| lane | branch | depends_on | tickets (in order) | files |
|---|---|---|---|---|
| a | `lane/p0-a` | – | P0-1 | `backend/app/metadata/apply.py`, `issues.py`, `desired.py`, `runner.py`; `backend/tests/test_p0_oscillation.py` (+ `test_attention.py` only if an existing assertion must change) |
| b | `lane/p0-b` | a | P0-3, then P1-G4 | `backend/app/plex.py`, `backend/app/metadata/plexsync.py`, `apply.py`, `issues.py`; `frontend/src/components/library/issues.js`; `backend/tests/test_p0_plex_mirror.py`, `test_p0_plex_foreign.py`, `test_metadata.py` (SharedPlexAlbum 621-624), `test_attention.py` (ReaddTestCase 622-634) |
| c | `lane/p0-c` | – | P0-5, then P0-4, then P0-6 | `backend/app/ownership.py`, `library_index.py`, `worker.py`, `api.py`, `cli.py`; `frontend/src/components/SkippedList.jsx`; `backend/tests/test_p0_skips.py`, `test_p0_footguns.py` (+ `test_dedup.py`, `test_skipped.py` if needed) |
| d | `lane/p0-d` | – | P0-2 | `backend/app/metadata/missing.py`, `routes.py`, `views.py`; `frontend/src/api.js`, `components/library/DetailPane.jsx`, `components/library/AttentionSidebar.jsx`, `components/Library.jsx`; `backend/tests/test_p0_download_missing.py` |

Why these lanes:
- Lane b shares `apply.py` and `issues.py` with P0-1, so the phase note puts it on lane a's reviewed tip.
- G4 rests its mirror check on P0-3's paging fix.
- P0-4 needs P0-5's `error_code` marker.
- P0-6 shares `library_index.py` with P0-5.
- Lanes c and d share no file with a, b or each other, so they start at `f3586fd` in parallel with a.
- The frontend build check is needed for b (issues.js), c and d.

Integration order: a, then b; c and d at any point. Expect no conflicts, because the file sets are disjoint and b already contains a.

## Defaults chosen (the lead can override)

- **P0-1 pause length:** 14 days, the breaker's own window. There is no UI to lift it in phase 0. An explicit Apply still runs and is refused by the breaker while the A→B→A history is younger than 14 days.
- **P0-1 merge when `METADATA_WRITE_FILES` is off:** refused, because an untagged merge loops.
- **P0-2:** "tried" means a job was queued. A wanted-only issue (YouTube busy) is untried. Retry = `force`, from the album pane only. Bulk Apply by ids is not forced.
- **P0-3:** the live check uses the folder view and fails safe on anything unexpected. The lead may verify its shape with one hand-written read-only GET, but nothing depends on it.
- **P0-4:** a cross-edition skip is labelled "different album" (with reason `edition`), as the acceptance asks, but it is left out of the Re-check, which would re-skip it.
- **P0-6:** "the app is up" = something listening on `127.0.0.1:8000`, the same listener the compose healthcheck probes. No new lock or pid file.
- **G4:** `plex_foreign` can't be applied in phase 0; repair is P1-C5. Track-level genres still apply to our own tracks.
