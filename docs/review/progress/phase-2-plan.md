# Phase 2 plan: issues that close when they're fixed

Planner's re-verification of P1-A1, P1-A2, P1-A3 and P2-A4 against the phase
branch tip `263261c` (`fix/review-2026-11-p2`: phase 1's integrated tip plus
the ledger). The triage's line numbers are from before phases 0 and 1, so every
one was looked up again; where they moved, the new place is given. Counts come
from the shared copy `.cache/claude-sandbox/mdl-p2.db` (schema **v11**, the
live app's pre-phase-1 schema; its newest `md_changes` row is
2026-11-01T22:32:16Z). They are sizes, not test oracles.

- `progress.md`: no phase 2 ticket is done. Phases 0 and 1 are integrated.
- Baseline on the tip, run by the planner: **`Ran 533 tests in 215.674s OK`**.
  It must stay green.
- `phase-0-report.md` and `phase-1-report.md` were read. Their carry-overs are
  folded in as the lead asked:
  - into P1-A1: phase 1's 6.1 (journal what `merge_into` folds, replay it on
    undo) and phase 0's open item 1 (the breaker's wording);
  - into P1-A3: phase 1's unconfirmed `merge_artist_folders` spelling finding;
  - into P2-A4: phase 0's items 10 (`TAB_FOR_KIND` lacks `plex_foreign`) and
    22 (the `tried` help text).
- **No premise failed. No ticket needs a product decision that §2/§9 leaves
  open.** Two readings of the ticket text are judgement calls (the closing
  state for "no longer found", and how a first failure counts). They are
  listed under "Defaults chosen" at the end; the lead can override any of them.

## Common rules for every lane

- **Two lanes, in parallel, with disjoint files.** Lane a is P1-A1 → P1-A2 →
  P2-A4, in that order: the three share `issues.py`, `runner.py`, `views.py`,
  `routes.py` and `apply.py`. Lane b is P1-A3 alone: `api.py`, `library.py` and
  a new `metadata/recheck.py`. Neither lane edits a file the other touches,
  source or test. Both start from `263261c`; neither depends on the other.
- **Integration order is §6's:** lane a's A1, then A2, then lane b's A3, then
  lane a's A4. The files are disjoint, so each cherry-picks cleanly.
- **Lane b's tests must hold before and after A1.** A1 renames the states an
  issue closes with (`superseded`, `cleared`). So lane b asserts that an issue
  is no longer in `('open', 'queued', 'failed')`, never that it is `applied`.
- **Migrations.** Only A1 adds one: **v15**, through `db._migrate`. Time it on
  a container-`/tmp` copy, run it twice to show the second start is a no-op, and
  show a cut-off run leaves nothing behind (as phase 1's `MigrationTestCase`
  does). The shared copy is v11, so opening a `/tmp` copy through
  `db.connect()` runs v12-v15 (about 10 s). Report v15's own time separately.
- **No `RULES_VERSION` bump.** None of the four needs one. `desired` is rebuilt
  at every check (A2's label rule), the copies planner runs library-wide every
  cycle (A2), and the rest is state, not planning. A bump would re-plan every
  album with auto-apply on.
- **No new dependencies.** Stdlib, what the image has, and the npm packages
  already in `package.json`.
- **Nothing deletes or purges.** No lane calls `library.purge_trash` or unlinks
  under `.mdl-trash`. Reviewers grep each diff for `purge_trash`, `.mdl-trash`,
  `unlink` and `rmtree`. Only the existing, guarded uses may appear.
- **Real-data scripts** follow CONTEXT:
  - `--network none`; the shared copy mounted `:ro` and copied to `/tmp/data`
    only when app code must write; the library mounted `:ro`, or not at all;
  - every write flag `=false`, `PLEX_TOKEN=` empty, the httpx logger at
    WARNING;
  - `settings.data_dir` set before `db.connect()`, and `await db.close()` in a
    `finally`;
  - MusicBrainz from `meta_cache` only: `settings.musicbrainz_contact =
    "sandbox@invalid"` in memory, and `mb.client._fetch` patched to raise
    `MusicBrainzError` at once (phase 1's pattern, `work/p1-a/harness.py`);
  - pure SQL can read `file:…mdl-p2.db?mode=ro` from the host;
  - scripts and outputs go in `.cache/claude-sandbox/work/p2-<lane>/`.
- **Tests first.** Write the acceptance tests first, run them on the lane's
  starting tip, record which fail, and paste that into the commit body.
  - Tests use real code paths: the real scan, registry, checks, executors,
    handlers and real files where files move or tags change. Only
    MusicBrainz, Last.fm and Plex are faked.
  - Reuse the harnesses: `test_attention.DbTestCase`,
    `test_p0_oscillation.LibraryTestCase` (`services()`, `make_thriller`,
    `check_all`, `merge`, `apply`), `test_p1_unit_id.UnitTestCase`,
    `test_p0_plex_foreign.PlexAlbumTestCase` (seeded `plex_items` and
    `fake_plex()`), `test_p0_download_missing`'s queue harness, and
    `test_metadata.make_audio`.
  - Handlers are called directly (`routes.apply_issues(routes.ApplyIn(...))`,
    `api.library_trash(...)`), or through the router with
    `httpx.ASGITransport` (`test_p1_unit_id.py:259-275`).
- **Frontend build check** for lane a (A4 only):
  `docker build -t mdl-web-sandbox <worktree>/frontend`. Lane b is backend
  only.

---

## P1-A1 Honest issue states (lane a, M)

**Line refs (re-verified at `263261c`):**
- `metadata/issues.py`:
  - `save`/`_save` 179-253;
  - the re-open rule **211-218**: any state except `dismissed` and `queued`
    (or `failed` with the same detail) becomes `open`, `applied` included
    (triage's 183-188);
  - close-stale 234-253 sets `applied` with no reason (triage's 204-221);
  - `KINDS`/`UNIT_KINDS` 32-136.
- `metadata/units.py`:
  - `_merge_into` 239-299. It folds v's pin, ignore, no_release, overrides,
    pause and dismissals into k, and closes v's issues that k also has as
    `applied` "merged into …" (290-291). It writes **no `md_changes` row**.
  - `_sync`'s closings use `updated_at = datetime('now')` at **425** (gone
    units) and **431** (issues whose album key is gone). These are the
    triage's 186/192.
- `metadata/runner.py`:
  - `_mark` 991-993;
  - `_apply_simple` 995-1023, which marks `applied` straight after the
    executor (1007);
  - `_start_album`'s "album is no longer there" closings 956-957;
  - `_apply_plex`'s old-key closings 1045 and 1049, and its post-apply check
    1050;
  - `_check_unit`'s gone path 575 and its ignored path 586;
  - `request_apply`/`request_decide` 152-197 (they take `open` and `failed`);
  - `_resume_queue` 283-301; `publish_issues` 276-280; `_decide` 736-760.
- `metadata/missing.py`:
  - `refresh` 122-147 closes `applied` at 140-142;
  - `queue_download` 376-474: its already-tried branch is **420-436**. It
    leaves the state alone and writes the error "already tried: …". A fresh
    queue sets `open` at 467.
  - `wanted_downloads` 187-195.
- `metadata/apply.py`:
  - `refuse_oscillation` 466-484; the wording is at **483-484**: "…this
    album's fixes are not applied automatically for 14 days";
  - `rekey_unit` 152-176; `revert`/`_revert` 1544-1665.
- `metadata/routes.py`: `ISSUE_STATES` 253-255, `dismiss_issues` 323-340,
  `reopen_issues` 359-368, `revert_batch` 378-394. `revert_batch` re-checks
  only the batch's album keys.
- `metadata/views.py`: `OPEN_STATES` 21; the `tried` filter
  (`_DOWNLOAD_TRIED`) 90-115; `album()`'s missing pane 509-516.
- `relocate.py`: `relocate` 60-98; `_follow_folders` 182-226 calls
  `apply.rekey_unit` at 190 and 204.
- `db.py`: `MIGRATIONS` ends at v14 (543-548); `_migrate` 582-614.

**Premise check: holds.**
- The re-open rule is as the triage says (issues.py:211-218).
- On the copy: applied 52,968, dismissed 3,249, open 1,149, failed 1.
- **9,029 `applied` rows carry `datetime('now')`'s format**
  (`YYYY-MM-DD HH:MM:SS`, no `T`/`Z`): genres 5,007, moods 1,705, tags 675,
  unmatched 421, move 327, review_match 263, …
  - These are exactly the registry-sync closings §1 counts (9,028).
  - That format is **P1-R8's only way to find them**. So A1 changes how new
    rows are written, and **must not rewrite these old timestamps**.
- `merge_into` journals nothing, and `revert_batch` re-checks only the batch's
  keys. `apply._relocate` rewrites every row of its batch to the destination
  key (apply.py:543-546), so the merged-away key is never re-checked. That is
  phase 1's 6.1, confirmed.
- The breaker refuses an explicit Apply too. Every executor path calls
  `refuse_oscillation`, and nothing checks for auto mode. The wording claims
  otherwise.
- P0-2: the already-tried answer keeps the issue `open` with only an error
  string. `ISSUE_FILTERS["tried"]` means "a download was queued". There is no
  state for "Tried — N not available" (§2).

**§2 rules:** nothing is deleted and every change is journalled; Download
missing is never automatic; after a Download missing has fetched what YouTube
Music has, the leftovers go to a "Tried — N not available" bucket, outside the
Needs-attention count, never re-queued automatically, and the user can retry
or accept from there.

**Design:**

1. **History: an `md_issue_events` table, written by SQLite triggers.**
   Columns: `(id, issue_id, at, from_state, to_state, reason)`. Indexes on
   `(issue_id, id)` and `(to_state, at)`. Two triggers:
   - `AFTER INSERT ON md_issues` (from NULL);
   - `AFTER UPDATE OF state ON md_issues WHEN OLD.state IS NOT NEW.state`.

   Each trigger takes `at` from `strftime('%Y-%m-%dT%H:%M:%SZ','now')` and the
   reason from `NEW.error`.

   Why this and not `reopened_count`:
   - §7 asks for "re-opened from `applied` **within 24 h**". A counter can
     neither time a re-open nor be windowed by deploy time. Events can.
   - State is written from about 25 places in 6 modules (issues, units,
     runner, missing, routes, and the tests' fixtures). A trigger captures
     every one in the same transaction, and any future writer, with no
     call-site edits and no path to miss.
   - It needs only plain SQL. Phase 1's warning was about `path_key()`, which
     the host `sqlite3` lacks. v12 already added a trigger, so there is
     precedent.
   - The cost is one row per state change, not one per upsert. Its retention
     belongs to P2-D5 (say 180 days, as for `md_changes`).

   The §7 queries become:
   ```sql
   -- came back: applied -> open within 24 h (target 0)
   SELECT COUNT(*) FROM (SELECT issue_id, at, from_state, to_state,
       LAG(at) OVER (PARTITION BY issue_id ORDER BY id) AS prev_at
     FROM md_issue_events) WHERE from_state = 'applied' AND to_state = 'open'
     AND at > :deploy_time AND prev_at > strftime('%Y-%m-%dT%H:%M:%SZ', at, '-24 hours');
   -- fixes that didn't stick, the honest form of the same (watch, don't target 0)
   SELECT COUNT(*) FROM md_issue_events WHERE from_state = 'applied'
     AND to_state = 'failed' AND at > :deploy_time;
   ```

2. **States.** These stay: `open`, `queued`, `applied`, `failed`,
   `dismissed`. These are new:
   - `superseded`: the issue no longer applies here because its album moved,
     merged into another album (that one carries the issue) or is gone. The
     reason goes in `error`, e.g. "merged into Artist/(1982) Thriller 25", "the
     album is gone".
   - `cleared`: no longer found, and no fix of ours ran (default, see the end).
   - `tried`: Download-missing leftovers (§2's bucket).

   `applied` now means only "an executor of ours ran it". The post-apply check
   turns it into `failed` if the issue is still there.

3. **Columns (v15):** `md_issues.attempts INTEGER NOT NULL DEFAULT 0` and
   `md_issues.applied_at TEXT`.
   - No backfill: old `applied` rows keep `applied_at` NULL, so they re-open
     as they do today. The registry's 9,029 never fixed anything, and a
     `failed` "still present after the fix" would be false for them.
   - v15 is O(1) DDL (`ADD COLUMN` with a constant default doesn't rewrite the
     table). Expect well under 0.1 s.

4. **`_save` rules.** One function decides the new state of a re-planned row:

   | existing state | re-planned → |
   |---|---|
   | `dismissed`, `queued` | unchanged (as now) |
   | `failed`, same detail | unchanged (as now; auto-apply never retries it) |
   | `applied`, `applied_at` within `REOPEN_WINDOW` (24 h) | **`failed`**, error "still there after the fix", attempts kept |
   | `applied` older than the window, or `applied_at` NULL | `open` (a new problem) |
   | `tried` (missing_tracks) | `tried` while the new detail's missing set is a subset of the old one; `open` if anything new is missing |
   | `superseded`, `cleared`, `failed` (other detail) | `open` |

   Close-stale (234-253) closes `open`, `failed` and `tried` rows as
   **`cleared`**, with reason "no longer found". It takes an optional
   `close_as`/`reason`, so the gone paths can pass `superseded`.

5. **Executors.**
   - `_mark(issue, state, error)` sets `applied_at = now` and
     `attempts = attempts + 1` when an executor finished: on `applied`, and on
     `failed` from an exception or the breaker.
   - `KeepOpen` (a download queued, a scan asked for) doesn't count.
   - The closings that name no executor become `superseded`, with a reason:
     - `_start_album` 956-957: "the album is no longer there";
     - `_apply_plex` 1045/1049: "planned again under the album's new key";
     - `_check_unit`'s gone path 575: "the album is gone";
     - `_sync` 425/431, with `utcnow()` passed as a parameter.
   - `_check_unit`'s ignored path (586) closes as `cleared` with "the album is
     ignored".
   - `missing.refresh` (140-142) closes as `applied` when the issue has
     download attempts (our download filled it), otherwise `cleared`.
   - `_decide`: if the check inside `_decide_one` planned the decided kind
     again, the decision is `failed` "still there after the decision", not
     `applied`.

6. **`tried` takes over P0-2's "already tried".**
   - The already-tried branch of `queue_download` (missing.py:420-436) sets
     `state = 'tried'`, from `open`/`failed`/`queued` (never from
     `dismissed`). It keeps its "already tried: …" error, so P0-2's assertion
     still holds.
   - `_apply_simple`'s KeepOpen handler only reopens rows still `queued`
     (1010), so `tried` stays.
   - Retry (`force=True`) sets `open` (467), as now.
   - `request_apply` and `wanted_downloads` take `open`/`failed` only, so
     `tried` is never re-queued automatically.
   - `OPEN_STATES` (views.py:21) gains `tried`. The album pane, the artist
     page and Dismiss ("Accept incomplete") then still see it. The counts are
     A4's.
   - `detail.download.attempts` stays: it is the per-video data
     `queue_download` needs. `md_issues.attempts` counts fixes of ours that
     ran.
   - A job ending doesn't move an issue to `tried` yet. That is P1-B5's
     job-terminal hook.

7. **`merge_into` journals what it folds, and Undo replays it.** This is
   phase 1's 6.1.
   - `units.merge_into(v, k, *, reason, batch=None)` writes one `md_changes`
     row in its transaction: `target='unit'`, `field='merge'`, `ref=v_uid`.
     - `old_value` holds v's whole record (key, dirpath, artist_dir, manual,
       release_mbid, no_release, ignored), its overrides, its pause, and each
       of its UNIT_KINDS issues (id, key, state, error).
     - It also holds k's values before and after the merge, for each field
       the merge changed, and the ids of v's issues that were re-keyed to k.
     - `new_value` is `{"into": k_uid, "key": k_key}`. Store everything in
       the JSON: `_relocate` rewrites the batch's `album_key`/`summary`
       afterwards. Use a NULL summary inside a move batch, so
       `views.changes`' `MAX(summary)` keeps the batch's label.
   - **Which batch:** `relocate.relocate` looks up the batch that journalled
     its moves, through `idx_changes_target_ref`:
     `SELECT batch_id FROM md_changes WHERE target='move' AND ref=? AND
     new_value=? AND reverted_at IS NULL ORDER BY id DESC LIMIT 1` on the
     first pair.
     - It passes the batch through `_follow_folders` → `apply.rekey_unit(…,
       batch=)` → `merge_into`.
     - `apply._relocate`, `do_album_copies`/`do_merge` and
       `library._merge_artists` all journal their moves before they call
       `relocate`. So an artist merge's folds join the artist-merge batch
       **without lane a touching `library.py`**.
     - Registry and `follow` merges have no moves. They get a batch of their
       own, which `views.changes` hides (`HAVING SUM(target != 'unit') > 0`):
       there is nothing to undo on disk.
   - **Replay:** `_revert` sets the batch's `unit` rows aside. After the files
     are back and `relocate.relocate(moved_back)` has run, it calls
     `units.unmerge(change)`, newest first.
     - If v's key holds files again, v's record is re-created there **with its
       own unit id**: insert a row if none, or re-id a record the undo just
       made.
       - Its files are stamped, and the alias is deleted.
       - Its decisions, overrides and pause are restored.
       - v's issues that went to k move back to `kind:v_uid`, and v's
         superseded rows get their pre-merge state back.
     - k's fields go back to their before-values **only where they still hold
       the after-value**, so a later decision of the user's stays.
     - The change is then marked reverted.
     - If v's key holds no files, the row stays unreverted, and the failure
       says why.
   - `routes.revert_batch` also re-checks the keys `_revert` reports restoring
     (`result["restored_keys"]`).

8. **The breaker's wording** (apply.py:483-484); the behaviour stays. It
   becomes: "{reason}: nothing was moved. Moves and merges of these files are
   refused, automatic or applied by hand, while this back-and-forth is within
   the last 14 days; undo one of the moves in Activity to lift it sooner." The
   pause sentence goes, because the refusal is the binding part.
   `test_p0_oscillation` asserts only "oscillating between", which stays.

9. **Timestamps:** every new write is `utcnow()` ISO-Z (units.py:425/431 are
   the only `datetime('now')` writes to `md_issues`). Old rows are left as they
   are (see the premise check).

**Files:**
- `backend/app/db.py` (v15);
- `backend/app/relocate.py` (the batch of the moves → `merge_into`);
- `backend/app/metadata/issues.py`, `units.py`, `runner.py`, `missing.py`,
  `apply.py`, `routes.py`, `views.py` (`OPEN_STATES`, `ISSUE_STATES` "all"
  and the new state keys, and `changes()` hiding batches made only of
  unit-merge rows);
- new `backend/tests/test_p2_issue_states.py`.
- Fixture updates, each named in the commit body:
  - `test_metadata.py` 1236-1262: two tests enshrine "closed as `applied`" for
    gone albums; they now expect `superseded` with a reason;
  - `test_p0_download_missing.py` (the already-tried tests also see
    `state == 'tried'`);
  - `test_p1_unit_id.py`, `test_attention.py`, `test_p0_oscillation.py` and
    `test_p1_settling.py`, only where a test re-checks right after an apply
    and the new `failed`/`superseded` states show.

**Acceptance tests (write first), `tests/test_p2_issue_states.py`:**
- `ReDetectTestCase` (on `LibraryTestCase`, real files):
  - `test_a_fix_that_does_not_stick_fails_instead_of_reopening`, the ticket's
    first:
    1. A resolved album with a planned `tags` fix is applied through the real
       file stage.
    2. An outside writer (`tagio.write`, standing in for another tagger) puts
       the old tags back.
    3. The real post-apply check runs (`_apply_plex` → `_check_unit`).

    The issue is `failed`, not `open`, with "still there after the fix",
    `attempts == 1`, and an `applied → failed` event. On the tip it ends
    `open`.
  - `test_a_failed_fix_is_not_applied_automatically`: the next `check_unit`'s
    `auto_ids` leaves it out, and its state stays `failed`.
  - `test_a_problem_found_after_the_window_is_new`: with `applied_at` 25 h old,
    re-detection gives `open`.
- `SupersedeTestCase`:
  - `test_a_merge_into_another_album_supersedes_and_the_keeper_keeps_the_dismissal`,
    the ticket's second. Two albums each have an `unresolved` issue; the mover's
    is dismissed. The mover's files are moved into the keeper's key through
    the real `do_move` → `relocate` → `rekey_unit` → `merge_into`. The
    mover's row is `superseded` (error names the keeper), and the keeper's row
    is `dismissed`. On the tip the mover's row is `applied`.
  - `test_an_album_whose_files_are_gone_is_superseded_with_iso_time`: after
    `sync_registry`, its issues are `superseded` and `updated_at` ends in `Z`.
- `UndoMergeTestCase.test_undoing_a_merge_gives_the_album_back_its_decisions`
  (phase 1's 6.1). On Thriller:
  1. ORIG is pinned, has a dismissed issue and a genres override.
  2. The copies merge, and the registry folds ORIG into KEEP.
  3. `routes.revert_batch` undoes the merge.

  ORIG has its old unit id, its pin, its dismissal and its override again;
  KEEP's record is as it was before the merge; the `unit` row is reverted.
  On the tip ORIG comes back with a new id and none of it.
- `TriedTestCase` (the `test_p0_download_missing` harness):
  - `test_already_tried_goes_to_the_tried_bucket`: a second `queue_download`
    after a fully skipped job leaves the issue `tried`, and neither
    `request_apply` nor `wanted_downloads` takes it;
  - `test_retry_takes_it_out_of_the_bucket`: `force=True` gives `open`;
  - `test_a_new_gap_reopens_it`: `refresh` with one more missing track gives
    `open`.
- `EventsTestCase.test_every_state_change_is_recorded`: open → queued →
  applied → failed, each as one event, and the §7 "came back" query returns 0.
- `BreakerWordingTestCase.test_the_refusal_says_an_apply_by_hand_is_refused_too`:
  a real `request_apply` of a refused move gives `failed`, and the error says
  it is refused when applied by hand too.
- `MigrationTestCase`: `test_v15_adds_the_columns_table_and_triggers_once`
  (a second start is a no-op) and `test_a_cut_off_v15_leaves_nothing`.

**Dry run (real data):** migrate a `/tmp` copy and time v15 alone. Then run
phase 1's parity method (`work/p1-integration/scripts/parity.py`: one
`sync_registry`, then `check_unit` for the calibration sample plus the md5
sample, plan only):
- plans must be identical, because A1 changes states, not plans;
- report the state transitions the run produced, by `from → to`, from
  `md_issue_events`;
- expect no `failed` from re-detection, since old rows have `applied_at` NULL.

**Risks:**
- No file is moved, tagged or trashed by anything new.
- The replay only restores records, and only when the undo itself has put
  v's files back.
- Behaviour the user will see: a fix that doesn't stick shows `failed` with a
  reason and is not retried automatically. A slow Plex scan after `not_in_plex`
  can show `failed` until a later check finds the files, which then clears it
  (`_due_after_plex_changes` makes those albums due).
- The track_order interaction is fixed in A2, the next commit.
  - Until A2, a two-stage `track_order` goes `applied` → `failed` →
    `applied`. The lane must keep the suite green at A1's commit.
  - If a test catches that, the A2 fix moves forward into A1, and the commit
    body says so.
- Event growth is one row per transition; retention is P2-D5's.

---

## P1-A2 Fix the fixes that don't converge (lane a, M)

**Line refs (re-verified):**
- `engine/tagio.py:246-247` (Vorbis `label`/`organization`/`publisher`
  flagged as junk when `SPAM_RE` matches). The same rule exists for MP4's
  freeform label at 353-355. `SPAM_RE` (98-100) matches
  `\b[\w-]+\.(com|net|org|ru|to|cc)\b`.
- `metadata/desired.py:322-324` (the label from `label-info`) and 387 (written
  as the `label` tag).
- `metadata/issues.py` `plan_album_copies` 957-1021, `_sharing` 1024-1043,
  `_keeper` 1046-1064.
- `metadata/apply.py` `copies_plan` 765-845, `do_album_copies` 848-.
- The triage's `apply.py:1235-1239` (track_order marked `applied` after the
  file stage) is now in the runner:
  - `_start_album` puts a `track_order` issue in **both** `state["files"]` and
    `state["plex_stage"]` (runner.py:966-967), because the kind is in
    `FILE_STAGE` and `PLEX_STAGE` (apply.py:1507-1520);
  - `_apply_simple` marks it `applied` after the file part (1007), before
    `_apply_plex` runs its Plex part.

**Premise check: holds.**
- **Junk label.** DMX *(2010) Mixtape* has 23 tag batches from 10-14 to 10-22.
  The last writes `"label": ["Siccness.net"]`, and every index row there
  reads back `label = Siccness.net`, `junk = ["label"]`.
  - Two resolved albums want a label the writer strips: DMX *Mixtape*
    (`Siccness.net`) and R.E.M. *Live in Greensboro* (`nugs.net`).
  - 38 index rows carry label junk.
- **album_copies.** 3 are open on the copy: *Thriller 25*, *My Head Is an
  Animal* and *Dreaming Out Loud*. Phase 0 (open item 5) showed *Thriller 25*
  re-planned every pass with 0 moves, because 5 files are left behind, and
  applying it does "nothing to merge".
- **track_order.** As above. There are no open two-stage `track_order` issues
  on the copy today (0 with a non-empty `detail.plex`), but every new one
  goes through this path.
- **Scope query** (albums with more than 3 batches of one field in any 7-day
  window, over the whole journal):

  | target/field | albums | worst | cause |
  |---|---|---|---|
  | plex/genre | 672 | 72 (Peking Duk *Paradise*, 10-19..10-21) | the genre loop fixed on 10-21; since 10-23 no album has more than 2 |
  | file/tags | 53 | 22 | the DMX label above, and the pre-10-21 loops |
  | plex/match | 14 | 5 | the Doggystyle/ABBA flip-flop, fixed by P1-G4 |
  | move/file | 9 | 7 | the merge ↔ move oscillation, fixed by P0-1 |
  | move/sidecar | 3 | 5 | follows move/file |
  | plex/index | 1 | 6 | look at it (10-21) |

  - In the last 7 days of the copy, no album passes 3.
  - Since 10-23 the only repeats are pairs:
    - Bob Dylan's merge, which the P0-1 breaker now refuses;
    - Doggystyle's match (G4);
    - imports retagged once more;
    - a resolver mis-identification (Holly Clausius/Blackalicious), which
      is not a convergence bug.
  - So the scope work is to re-run the query, classify it, and record the
    result in the commit body. The three named fixes are the ones still live.
    Check the plex/index album (`md_changes` from 2026-10-21T02:40) and fix
    it only if it is still a live loop.

**§2 rules:** a duplicate is the same recording in the same release group;
the best file is kept; when in doubt, don't pair. Nothing is deleted. Every
change is journalled.

**Fix outline:**
1. **Label:** the planner never wants a label the writer would strip.
   - `desired.build_album` drops a label value that tagio's spam rule flags.
     Expose it as `tagio.spam(value) -> bool`, built on `SPAM_RE`/`_spammy`,
     so there is one rule.
   - `tag_diff` then plans removing the junk label once (the writer strips it
     anyway), and the next check is clean.
   - The alternative, tagio exempting a "desired" label, is rejected: tagio
     has no idea of desired, and URL-like labels in every other file must
     still go.
2. **album_copies:** factor `copies_plan`'s per-file decision (move / trash /
   leave) into one pure helper. Both `copies_plan` and `plan_album_copies` use
   it.
   - The planner keeps a copy in a set only if the merge would move or trash
     at least one of its files. A set with nothing to take is not planned, so
     close-stale closes its issue.
   - The detail and summary say what the merge would do ("moves N, trashes
     M, leaves K").
   - `_sharing` is unchanged (B2 owns it).
3. **track_order:** a kind in both stages is applied once its Plex part has
   run.
   - The file stage runs the file part and leaves the issue `queued`.
   - `_apply_plex` applies the Plex part from the fresh plan and marks it
     `applied`. If the fresh plan no longer has it, the files pass ran its
     executor, so it is `applied` there.
   - An error in either part is `failed`, as now.
4. Anything else the scope query shows to be live.

**Files:**
- `backend/app/engine/tagio.py` (a public `spam()`; no behaviour change);
- `backend/app/metadata/desired.py`, `issues.py`, `apply.py`, `runner.py`;
- new `backend/tests/test_p2_converge.py`.

**Acceptance tests (write first), `tests/test_p2_converge.py`:**
- `ConvergeTestCase.test_five_auto_apply_cycles_change_nothing_after_the_first`,
  the ticket's, on `LibraryTestCase` plus `PlexAlbumTestCase`'s Plex fake. The
  fixtures:
  - Thriller's copies plus one left-behind copy whose length disagrees;
  - an album whose release label is `Siccness.net`;
  - an album numbered wrong both in its files and in the seeded Plex mirror.

  Each cycle is `check_all` plus every auto fix through `_files_pass` and
  `_plex_pass`. Assert:
  - `md_changes` gets no batch in cycles 2–5;
  - no issue goes `open → queued` in cycles 2–5, by `md_issue_events`.

  On the tip, the label case writes again in every cycle, and album_copies is
  re-planned.
- `test_a_label_the_writer_calls_junk_is_never_wanted`: desired has no
  `label`; after one tags fix the check plans no `tags`.
- `test_copies_the_merge_would_leave_are_not_an_issue`: a set whose only
  shared files would be left plans no `album_copies`, and an existing open
  one closes.
- `test_track_order_is_applied_once_its_plex_part_has_run`: after the files
  pass it is `queued`, and after the Plex pass it is `applied`. There is no
  `failed` and no `applied → open` event in between.

**Dry runs (real data, plan only):**
- the 3 open `album_copies` through the new planner: which stay, with their
  move/trash/leave counts (expect *Thriller 25* to go);
- desired for DMX *Mixtape* and R.E.M. *Live in Greensboro*: no label, and the
  `tags` plan that follows;
- the scope query's output in the commit body.

**Risks:**
- The copies planner can only plan fewer merges, never more, so it keeps
  files.
- The label change writes one tag fix on about 2 albums (38 files at most),
  removing a label the writer strips anyway.
- track_order changes no file.

---

## P1-A3 Duplicates-manager actions update issues immediately (lane b, S)

**Line refs (re-verified):**
- `api.py` (triage's 823-897):
  - `library_trash` 907-926 (`trash_paths`, and `add_exclusions` for
    `keep_together`);
  - `library_add_exclusion` 929-938;
  - `library_trash_restore` 957-962;
  - `library_merge_artists` 974-980.
- `library.py`:
  - `trash_paths` 1147-1161 → `_trash` → `relocate.forget` 1274;
  - `_restore` → `relocate.unforget` 1345;
  - `merge_artist_folders` 1396-1418 → `_merge_artists` 1435-1557, with
    `relocate.relocate` at 1517 and 1529;
  - `destination = target_dir / child.name` at **1470**, `final =
    destination / inner…` at **1477**;
  - `_analysis_token` 1707-1716.
- `metadata/dupes.py` `sync` 21-64. It is called only from the cycle's step 3
  (runner.py:425-435).

**Premise check:**
- "Go through relocate/forget" is **already done by P1-F3** (library.py:1274,
  1345, 1517, 1529). Nothing is left to do there.
- "Update issues immediately" **holds**:
  - none of the four handlers touches `md_issues` or schedules a check;
  - `dup_*` issues change only at the next cycle's `dupes.sync`, and unit
    issues only when the album comes due.
- **The dup-analysis cache doesn't see a relocate.** `_analysis_token`
  counts index rows and reads `MAX(scanned_at)`, and a path rename changes
  neither.
  - A `dupes.sync` right after an artist merge would reuse the old groups.
  - The cache has to notice, or the "at once" can't hold.
  - P2-D3 (phase 6) re-keys the cache on `library_generation`. A3 only adds
    the generation to the token.
- **The `merge_artist_folders` spelling finding is real by code reading, and
  can't be reached on today's data.**
  - On the case-insensitive share, `destination.exists()` (1472) is true for a
    target album folder that differs only in case. The files then go into the
    target's folder, but `final` (1477) and so the index rows name the
    **source's** spelling.
  - On the copy there are 2 open `split_artist` groups, and 0 album folders
    in them differ only in case. Fix it anyway: it is one call, and the
    importer already does the same (`relocate.on_disk`).
- The trash Restore handler has the same gap as Trash. It is included: same
  pattern, and it undoes a Trash.

**§2 rules:** `/library` is case-insensitive: reuse the on-disk spelling.
Nothing is deleted. Box sets and the duplicate rules are unchanged: A3 changes
no finder.

**Fix outline:**
1. New `backend/app/metadata/recheck.py`:
   `after_library_change(folders, *, artist_dirs=(), cap=50) -> dict`.
   1. `dupes.sync()`: the dup issues, library-wide.
   2. Every `md_albums` row whose folder is one of `folders` or under it,
      plus the units now on disk there (`units.rows_for_folders` →
      `build_units`): `runner.check_unit(key)`.
      - A key with no files left follows its files or closes its issues.
      - Albums past the cap are only marked due (`next_check = 0, priority =
        10`), and the runner is woken.
   3. For each artist folder: `runner.check_artist(a)` if it still has
      albums; otherwise its artist issues close (`issues.save([],
      scope_kinds=ARTIST_KINDS, artist_dir=a)`).
   4. `runner.publish_issues()` and one `library.changed`.

   It applies nothing: auto fixes are queued by the next cycle, as for the
   album page's own actions, so no file changes come from it.
2. The four handlers in `api.py` call it after their action, with the folders
   they touched: the trashed paths' folders; the exclusion's keys (album
   scope: folders; track scope: their folders); the restored paths' folders;
   the merge's source and target artists. They return its counts beside their
   result.
3. `library._analysis_token` adds `md_state['library_generation']`.
4. `_merge_artists` builds `destination` from `await
   relocate.on_disk(f"{target}/{child.name}")`. Then a case-only twin takes
   the merge-file-by-file branch, under the target's spelling, on any
   filesystem.

**Files:**
- `backend/app/api.py`, `backend/app/library.py`;
- new `backend/app/metadata/recheck.py`;
- new `backend/tests/test_p2_dup_actions.py`;
- `backend/tests/test_library.py`, only `MergeArtistsTestCase`'s stubs (it
  stubs `db.fetchall`/`execute`; `on_disk` needs `fetchone` or a patch).

**Acceptance tests (write first), `tests/test_p2_dup_actions.py`:** real
scan, real Trash and real handlers. Assert "no longer in
`('open','queued','failed')`", never a state name.
- `test_trashing_a_duplicate_closes_its_issue_at_once`: two copies of a track
  in one album, then a scan and `dupes.sync`, so `dup_track` is open. Then
  `api.library_trash(TrashIn(paths=[copy]))`. The issue is closed when the
  call returns, and the album's issues have been re-planned. On the tip it is
  still open.
- `test_not_duplicates_closes_the_group_at_once`: a `dup_album` group, then
  `api.library_add_exclusion(...)`. The issue is closed.
- `test_an_artist_merge_closes_the_split_and_moves_the_albums_issues`: a
  `split_artist` group, then `api.library_merge_artists(...)`.
  - The `split_artist` issue is closed.
  - The merged album's open unit issues name its new key.
  - No issue names a key that has no files.
  - On the tip, the stale dup analysis keeps the group.
- `test_a_restore_reopens_what_the_trash_closed`: trash, then Restore. The
  album's issues are planned again at once.
- `test_an_artist_merge_files_into_the_targets_spelling`:
  - target `In the Lonely Hour/` beside a source `In The Lonely Hour/`;
  - after the merge, every moved file is indexed under `…/In the Lonely
    Hour/…` and is there on disk.
  - On the tip, the case-sensitive test filesystem gets a second folder with
    the source's spelling, and the index names it. On the share the same code
    put the files in the right folder under the wrong index path. Both are
    the same defect, and the test catches the reachable one.

**Dry run:** on a `/tmp` copy with only the 2 open `split_artist` groups'
folders copied into a container-local `/library`, run the merge and the
recheck. Report the issues closed and re-planned. The rest of the library
looks "missing" in such a replay; that is an artefact.

**Risks:**
- The actions themselves are unchanged: Trash and the artist merge already
  ran.
- The recheck only plans: no executor, and no auto-apply.
- `on_disk` can only send files to a folder that already exists under
  another case, which the share already does today. The index now says so.
- The inline re-check makes these calls slower. With a warm `meta_cache` a
  check takes well under a second, and the cap bounds the rest.
- Lane a changes `publish_issues`' payload and the close-state names. Lane b
  calls only stable functions (`runner.check_unit`, `check_artist`,
  `publish_issues`, `issues.save`, `dupes.sync`).

---

## P2-A4 The UI reflects the true state (lane a, M)

**Line refs (re-verified):**
- `views.py:151-160` is now **`views.attention` 179-210**. It counts
  `open`+`failed`+`queued` and returns `failed_count`/`queued_count`.
- The same set is counted in `views.overview` 62-65,
  `_issue_counts_by_artist` 77-86 (the artist list's badge) and
  `runner.publish_issues` 276-280.
- `runner.py:973-979` is now `_apply_loop` 699-734 and `_files_pass`
  788-852. The files pass publishes nothing; only `_plex_pass` (946),
  `_decide` (761) and the cycle (395, 435, 543) do.
- `IssueList.jsx:278-283` is now `IssueListDialog`:
  - `load` 248-258 asks `state: 'open'`, which the server reads as
    `OPEN_STATES`, `queued` included;
  - `act` 285-296 reloads at once (`load(0)`, 293), so applied rows come
    back as `queued`;
  - the per-row removal is at 443-447;
  - there is no SSE subscription.
- `routes.py:249` is now `list_issues` 259-268, `_selected` 270-286 and
  `apply_issues` 291-297.
  - The dialog's total counts `queued`, but `apply_issues` takes
    `open`/`failed` only.
  - `dismiss_issues` (323) takes `OPEN_STATES`, `queued` included, so "select
    all" + Dismiss also dismisses rows the dialog doesn't show.
- Swallowed errors:
  - `NeedsReview.jsx:16-19`: `act`, no catch; its refresh at 12
    `.catch(() => {})`;
  - `SkippedList.jsx:26-29` (`act`), 31-37 and 39-50 (`try/finally`, no
    catch), 21-22;
  - `JobList.jsx:53` and 59 (`.then(onChanged)`, no catch).
- `Library.jsx:24-32` `TAB_FOR_KIND` has no `plex_foreign`.
  `Library.jsx:411` has the bulk-count fallback.
- `issues.js:92-93`: the `tried` filter's help text ("A download ran; …").
- `AttentionSidebar.jsx:26` (bulk count) and 124 (total);
  `DetailPane.jsx:55` (`IssuesSection`'s state filter) and 151-202 (the
  missing pane's "already tried").

**Premise check: holds.** Each item above was read in the code.

**§2 rules:** the "Tried — N not available" bucket sits outside the
Needs-attention count and is never re-queued automatically. The user can
retry or accept from there.

**Fix outline:**
1. **Counts** (`views.py`, `runner.py`): one definition.
   - `ATTENTION_STATES = ("open", "failed")` is the count: what needs the
     user. A failed fix shows its reason and attempts (A1).
   - `attention()` returns per group `count` (open+failed), `failed_count`,
     `queued_count` ("N in progress") and `tried_count`. It selects
     `open`/`failed`/`queued`/`tried`, so a group with only queued or tried
     rows still shows.
   - `total` is the sum of `count`.
   - `overview()`, `_issue_counts_by_artist()` and `publish_issues()` use the
     same: `{"open", "by_kind", "in_progress", "tried"}`.
2. **Publish after the files pass:** `_files_pass` ends with
   `publish_issues()` (and `library.changed` for the albums it changed).
3. **List = action:**
   - Add `ISSUE_STATES["actionable"] = ("open", "failed")`, plus `"tried"`
     and `"queued"` keys.
   - `SelectIn` gains `state: str | None`. `_selected` uses that state's set,
     cut to what the action may take: apply and decide take open/failed;
     dismiss takes open/failed/tried.
   - So the dialog's "Select all N that match" is exactly what Apply,
     Decide or Dismiss takes.
4. **Review dialog** (`IssueList.jsx`):
   - it lists `state: 'actionable'` and passes `state` in `selection()`;
   - it subscribes to `library.issues` and `library.changed` (debounced
     reload, keeping the picks), so applied rows drop as the runner works and
     queued ones don't come back;
   - it shows attempts on a failed row ("failed ×2").
   - The single-row removal stays.
5. **Sidebar** (`AttentionSidebar.jsx`, `Library.jsx`):
   - the chip shows `count`, with a secondary line "N in progress · N tried";
   - "N tried" opens Review on `state: 'tried'`, where Dismiss reads "Accept
     incomplete". Retry stays per album (the pane's "Retry download"); bulk
     retry is P2-E4.
   - The bulk-count fallback becomes `group.bulk_count ?? group.count`.
   - `TAB_FOR_KIND.plex_foreign = 'tracks'`.
6. **Album pane** (`DetailPane.jsx`):
   - `IssuesSection` also lists `tried`;
   - the missing pane says "Tried — N not available" for a `tried` issue
     (`views.album`'s missing dict gains `state`), and keeps Retry and Accept
     incomplete.
7. **Errors shown:** `NeedsReview`, `SkippedList` and `JobList` catch their
   action errors and show them in an `Alert` (these components have no
   `notify`).
8. **Help text** (`issues.js`): the filter keeps its value `tried` but is
   relabelled "Download ran", with the help "A download was queued; what is
   left may not be on YouTube Music, may have been skipped as already held,
   or may still be coming. Download missing again moves it to Tried."
   - A comment notes that the `tried` filter (a download ran) is not the
     `tried` state (nothing left to fetch).
   - The `tried` state gets an entry in the state chips, and `superseded`
     and `cleared` get neutral chips.

**Files:**
- `backend/app/metadata/views.py`, `routes.py`, `runner.py`;
- `frontend/src/components/library/{AttentionSidebar,IssueList,DetailPane}.jsx`,
  `frontend/src/components/library/issues.js`,
  `frontend/src/components/Library.jsx`,
  `frontend/src/components/{NeedsReview,SkippedList,JobList}.jsx`;
- new `backend/tests/test_p2_attention.py`.

**Acceptance tests (write first), `tests/test_p2_attention.py`:**
- `test_the_count_is_what_needs_the_user`: one issue each of `open`,
  `failed`, `queued` and `tried`, in one kind. `views.attention()` gives
  `count == 2`, `queued_count == 1`, `tried_count == 1` and `total == 2`, and
  `publish_issues` sends the same. On the tip it counts 3, with no `tried`.
- `test_issues_are_published_after_the_file_pass`: the real `_files_pass` on
  one `tags` fix, with `bus.publish` recorded. A `library.issues` event comes
  before any Plex pass, without the applied fix. On the tip there is none.
- `test_select_all_counts_what_apply_takes`: `open`, `failed`, `queued` and
  `applied` issues of one applyable kind.
  `routes.list_issues(kind, state='actionable')["total"]` equals
  `routes.apply_issues(ApplyIn(kind=…, state='actionable',
  only_auto=False))["queued"]`. On the tip the list counts the queued one too.
- `test_dismiss_all_takes_only_what_the_list_shows`: a queued issue isn't
  dismissed by a select-all on `actionable`.
- Frontend: the build check, plus the screenshots below.

**Screenshots** (section 8 harness; save as
`.cache/claude-sandbox/p2-sidebar.png` and `p2-review.png`):
- **API container** (`-d`, `--network none`, write flags `=false`,
  `PLEX_TOKEN=`): a script copies `/shared/mdl.db` to `/tmp/data`, sets
  `settings.data_dir`, and migrates through `db.connect()`.
  - In the copy only, it seeds a few states so the counts show: some
    `queued`, a `failed` with attempts 2, a `tried` missing_tracks.
  - It serves a bare FastAPI app with `api.router` and `routes.router` on
    `127.0.0.1:8001` (uvicorn).
  - It has **no lifespan**, so no worker, scanner or runner starts and nothing
    touches a library: don't mount the library, or mount it `:ro`.
- **vite** (`node:22-alpine`, `--network container:<api>`), with
  `node_modules` filled beforehand in a temporary named volume by a container
  that has network. A sandbox-only config file in the scripts dir points the
  `/api` proxy at 8001; `vite.config.js` isn't edited.
- **chrome** (`zenika/alpine-chrome`, `--shm-size=1g
  --disable-dev-shm-usage`, `--network container:<api>`). Drive it with
  `.cache/claude-sandbox/ui/shot.mjs` (puppeteer-core) or an adapted copy.
  It never clicks an action button.
- Remove the containers and the volume afterwards.

**Risks:**
- No file action anywhere.
- The count drops by the queued (and later tried) rows, which is the point.
- The Review dialog reloads on SSE; debounce it, so a busy apply doesn't
  reload many times a second.

---

## Lanes

| lane | branch | depends_on | tickets (in order) | files |
|---|---|---|---|---|
| a | `lane/p2-a` | – | P1-A1, P1-A2, P2-A4 | backend `app/db.py`, `app/relocate.py`, `app/engine/tagio.py`, `app/metadata/{issues,units,runner,missing,apply,routes,views,desired}.py`; frontend `components/library/{AttentionSidebar,IssueList,DetailPane}.jsx`, `components/library/issues.js`, `components/Library.jsx`, `components/{NeedsReview,SkippedList,JobList}.jsx`; tests: new `test_p2_issue_states.py`, `test_p2_converge.py`, `test_p2_attention.py`; fixture updates only in `test_metadata.py`, `test_p0_download_missing.py`, `test_p0_oscillation.py`, `test_p1_unit_id.py`, `test_attention.py`, `test_p1_settling.py`, `test_p0_plex_foreign.py`, `test_p1_transactions.py` |
| b | `lane/p2-b` | – | P1-A3 | backend `app/api.py`, `app/library.py`, new `app/metadata/recheck.py`; tests: new `test_p2_dup_actions.py`; fixture updates only in `test_library.py` |

- The two file sets are disjoint, tests included. Lane a never edits
  `api.py`, `library.py`, `recheck.py` or `test_library.py`. Lane b never
  edits anything in lane a's list.
- One commit per ticket, with the subject "`<id>: <imperative summary>`". The
  body holds what changed, the tests that failed before, the suite line and
  the dry-run numbers. A behaviour change goes under "Behaviour changes:".

## Defaults chosen (the lead can override any)

1. **History** is `md_issue_events`, written by triggers, not
   `reopened_count`. A counter can't answer §7's "within 24 h", and a trigger
   can't miss a writer.
2. **"No longer found, and no fix of ours ran"** closes as a new state,
   `cleared`. The ticket says `applied` is only for executors, and names no
   state for this case. Alternative: keep `applied` here, and let events tell
   the cases apart.
3. **A unit gone, merged or moved** closes as `superseded`, with a reason,
   even with no successor. That matches P1-R8's suggestion for the registry's
   9,028.
4. **Failed fixes** are counted in Needs attention from the first failure,
   with the reason and attempt count shown. Auto-apply never retries one, as
   today, and as the ticket says.
   - The ticket's "after 2 failed attempts, show it as needing attention"
     would hide a first failure that nothing retries.
   - The alternative, one automatic retry before it counts, means more file
     changes, so it was not chosen (keep-files rule).
5. **The re-open window** is 24 h, the same as §7's check.
6. **The 9,029 `datetime('now')`-format rows keep their timestamps.** P1-R8
   finds them by that format.
7. **`tried` is set only by `queue_download`'s already-tried answer** in this
   phase. P1-B5 adds the transition at job end. It stays `tried` while no new
   track goes missing.
   - Deploy note: the about 250 open missing-tracks issues with an earlier
     download stay `open` until the user asks again. Review → "Download ran"
     → select all → Download missing moves each to Tried at once, and queues
     nothing already tried.
8. **The merge replay runs on Activity Undo only.** Registry-only merges are
   journalled (an audit trail), but hidden from Activity: there is nothing on
   disk to undo.
9. **A3's re-check runs inline**, up to 50 albums, and applies nothing.
10. **`plex_foreign` opens the `tracks` tab:** it is about which tracks Plex
    lumps together.
11. **The `tried` filter keeps its value** and is relabelled "Download ran".
    The Review view of `tried` offers Accept incomplete; Retry stays per
    album until P2-E4.

## Deploy notes (for the phase report)

- v15 (A1) is DDL only and fast. Phase 1's "back up first" note still
  applies, because v12 is one-way.
- No new settings, environment variables or dependencies, and no
  `RULES_VERSION` bump.
- What the user will see:
  - A fix that doesn't stick shows **failed** with "still there after the
    fix", and auto-apply leaves it.
  - Needs attention counts open and failed only. "N in progress" and "N
    tried" are shown beside it.
  - Missing tracks that were already tried move to **Tried** when asked
    again.
  - The breaker's message now says it refuses a manual Apply too.
  - Undoing a move that merged an album gives the album back its pin,
    dismissals and overrides.
  - Trash, "not duplicates", Restore and artist merges update Needs
    attention when they finish.
- After deploy, run the §7 "came back" query above (target 0), and the
  applied → failed query (watch it).
