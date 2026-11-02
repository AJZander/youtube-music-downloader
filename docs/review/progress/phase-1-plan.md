# Phase 1 plan: foundations

Planner's re-verification of P1-F1 … P1-F5 against the phase branch tip
`20cf111` (`fix/review-2026-11-p1`, which is phase 0's integrated tip plus the
ledger). The triage's line numbers are from before phase 0, so every one was
looked up again; where they moved, the new place is given. Counts come from the
shared copy `.cache/claude-sandbox/mdl-p1.db` (schema v11, 2026-11-02 13:23).
They are sizes, not test oracles.

- `progress.md`: no P1-F ticket is done. Phase 0 (7 tickets) is integrated; the
  suite is **451 tests OK** and must stay green.
- `phase-0-report.md` was read. Its carry-overs are folded in below: P1-F1
  (rockstar), P1-F2 (md_state keys, plex_foreign detail), P1-F4 (races), P1-F5
  (journal indexes).
- **No premise failed. No ticket needs a product decision that §2/§9 leaves
  open.** The judgement calls are listed under "Defaults chosen" at the end; the
  lead can override any of them.

## Common rules for every lane

- **Chain.** This phase is one chain, as the lead asked: lane a (F1) → lane b
  (F2) → lane c (F3) → lane d (F4, then F5). Each lane starts from the previous
  lane's reviewed tip. The file sets overlap on purpose.
- **Behaviour stays identical** except where a ticket says otherwise. Every
  intended behaviour change is named in its commit body under "Behaviour
  changes:", with the dry-run numbers that size it.
- **Migrations.** Schema changes go only through `db._migrate`. F2 adds v12, F3
  v13, F5 v14, in that order (the chain guarantees it). Lane b first makes
  `_migrate` run each pending migration atomically (below), and the later lanes
  rely on that.
  - Time each migration on a container-`/tmp` copy of the shared DB. Run it
    twice to show the second run is a no-op. Show an interrupted run leaves
    nothing half-done. Report the duration in the commit body.
- **No `RULES_VERSION` bump** in this phase. None of the tickets needs one:
  - F2 rewrites the issue keys in its migration;
  - the F1 rule changes reach albums as they come due, which is how phase 0 shipped.
  - A bump would re-plan every album with auto-apply on.
- **No new dependencies.** Python stdlib plus what the image has; no npm
  packages.
- **Nothing deletes or purges.** No lane calls `library.purge_trash` or unlinks
  under `.mdl-trash`. Reviewers grep each diff for `purge_trash`, `.mdl-trash`,
  `unlink` and `rmtree`. Only pre-existing, already-guarded uses may appear.
- **Real-data scripts** follow CONTEXT:
  - `--network none`; the shared copy mounted `:ro` and copied to `/tmp/data`
    only when app code must write; the library mounted `:ro`;
  - every write flag `=false`, `PLEX_TOKEN=` empty, the httpx logger at WARNING;
  - `settings.data_dir` set before `db.connect()`, and `await db.close()` in a
    `finally`;
  - MusicBrainz from `meta_cache` only: `settings.musicbrainz_contact =
    "sandbox@invalid"` in memory, and `mb.client._fetch` patched to raise
    `MusicBrainzError` at once.
  - Pure computations (F1's comparisons) can read
    `file:/shared/mdl.db?mode=ro` with `sqlite3` directly, with no copy.
  - Scripts and outputs go in `.cache/claude-sandbox/work/p1-<lane>/`.
- **Tests first.** Write the acceptance tests first, run them on the lane's
  starting tip, record which fail, and paste that into the commit body.
  - Tests use real code paths: the real scan, registry, checks and executors,
    with real files where files move.
  - Only MusicBrainz, Last.fm and Plex are faked. Reuse the harnesses:
    `test_attention.DbTestCase`, `test_p0_oscillation.LibraryTestCase`
    (`services()`, `make_thriller`, `check_all`) and `test_metadata.make_audio`.
- **Frontend build check** (`docker build -t mdl-web-sandbox <worktree>/frontend`)
  for lane a (ReleaseRow) and lane b (album URLs). Lanes c and d are
  backend-only.

---

## P1-F1 One identity module (lane a, L)

**Line refs (re-verified at `20cf111`):**
- `library.py`:
  - text keys: `strict_key` 93, `fold` 100, `title_keys` 109, `keys_for` 147;
  - album keys: `album_key` 161, with `_DIR_NOISE` 83-91, whose `[\[\{][^\]\}]*[\]\}]` at 85 strips every bracket, `[Single]` included; `album_identity` 182, `row_album_identity` 198;
  - `unit_dir` 218, `edition_words` 236, `row_edition` 257, `title_looks_like_junk` 268;
  - scoring: `format_quality` 654, `score_file` 681;
  - matching: `same_recording` 883, `match_in` 894;
  - duplicates: `find_album_duplicates` 980 (`_same_kind` 973), `find_track_duplicates` 1213 (it lazily imports `ownership.version_markers`).
- `ownership.py`:
  - helpers: `version_markers` 72, `album_markers` 80, `title_keys` 93, `library_keys` 102, `credits` 120;
  - `LibraryIndex` 163; `lookup` 185-233: the exact-match loop at **223-225** returns `have` with `mismatch=not agrees(info)` (triage's 216-218).
- `metadata/missing.py` `_match` 211-251 (`_similar` 206): ranks 1 (position) and 2 (similar title) at **236-240** don't compare version markers.
- `metadata/resolver.py` `match_tracks` 122-158: the mistitled rank at **142** needs `gap <= 2.5` (Come/Came is 2.7 s).
- `metadata/units.py` `split_key` 39-46, with `_SUFFIX` 36: it strips any trailing bracket, so `1989 (Taylor's Version)` folds into `1989`.
- `metadata/apply.py` `_audio_quality` 585-593.
- `metadata/issues.py` `plain_recordings` 886, `matched_recordings` 902, `plan_album_copies` 930.
- `api.py` `_different_album` 301-343, `_album_context` 264.
- `metadata/plexsync.py` `album_share` 448-481.
- `discover.py` `owned_albums` 397-422, `_owned_by` 447, the `_identity` cache 443.
- `tests/test_catalog.py:675`: `self.assertEqual((out["library"], out["library_mismatch"]), ("have", True))`. It is the length-ignoring rule: Outnumbered held at 265 s against 295 s.

**Premise check: holds.**
- At least 20 matchers, as listed above, each with its own keys and tolerances.
- The P0-4 carry-over is confirmed. `Post Malone/(2017) rockstar` (release group
  `9af98551…`, resolved) holds `01 - rockstar.m4a`. Two skips of a YouTube
  Music single titled `rockstar (feat. 21 Savage)` matched it.
  - The YouTube side has no release group, so this is the names-only case:
    `album_identity` keeps `(feat. …)` and calls it another album.
- Grinspoon: both Download-missing jobs into `(2005) Best in Show` asked for
  `07 - Black Friday (Live)` (`skipped_duplicate`) for the studio track. That
  is rank 1 by position, with no marker check.

**§2 rules:** a duplicate is the same recording in the same release group;
different recordings (remix, live, demo, clean versus explicit) are never
duplicates; a song on a single, a compilation or another album is never a reason
to skip; when in doubt, don't pair. §9: lengths must agree within 5 s, or 30 s
for an old YouTube upload; otherwise it's a different file, and the album
version downloads.

**Files:**
- new `backend/app/identity.py`;
- `backend/app/library.py`, `ownership.py`, `api.py`, `discover.py`;
- `backend/app/metadata/missing.py`, `resolver.py`, `units.py`, `issues.py`,
  `apply.py`, `plexsync.py`;
- `frontend/src/components/artist/ReleaseRow.jsx` (one tooltip);
- new `backend/tests/test_p1_identity.py` and `backend/tests/identity_golden.py`
  (data only; not named `test_*`, so discovery doesn't load it as tests);
- `backend/tests/test_catalog.py` (line 675 only).

**Fix outline:**
1. **`app/identity.py`, a pure module.** It imports only `engine.matcher.normalize` and the stdlib: no DB and no I/O. It sits below `library` and `ownership`, which ends the `library`↔`ownership` lazy import.
   - Text and paths:
     - `strict_key`, `fold`;
     - `key_text(text)`: normalize plus the remaster cut, version words kept (today's `song_key ∘ normalize`);
     - `path_key(path)`: NFC + casefold, `/` kept. Lane c uses it.
   - Artists:
     - `split_credit(credit)`: one splitter for `resolver._SPLIT_CREDIT` and `discover._CREDIT_SPLIT`;
     - `artist_keys(credit)`: `discover._artist_keys`;
     - `credits(norm_artist, key)`.
   - Tracks:
     - `title_keys` (file side: the library version, with artist hints), `release_title_keys` (`ownership.title_keys`), `library_keys`;
     - `version_markers`, `album_markers`, `LIVE_RE`, `title_looks_like_junk`;
     - `DURATION_TOLERANCE`, `UPLOAD_TOLERANCE`;
     - `lengths_agree(a, b, youtube_upload=False)`: an unknown length agrees, as today;
     - `same_recording(a, b) -> Verdict`.
   - Albums:
     - `album_keys(name, hints=(), *, qualifiers="keep")`, `row_album_keys(row)`, `edition_words`, `row_edition`, `unit_dir`;
     - `same_album(a, b, policy) -> Verdict`.
   - Files: `file_quality(row, reference=None, *, tags=True) -> (score, reasons)`, plus `format_quality`.
   - The matcher: `assign(wanted, held, *, context=...) -> list[pairs]` (step 3).
   - `Verdict` is a small frozen dataclass: `same: bool | None` and `reason: str`. `None` means unsure, and every caller treats unsure as "not the same". That keeps files.

   `library`, `ownership`, `units` and `resolver` re-export the moved names, so every import site and existing test keeps working. P3-H1 removes the aliases later.
2. **Rules: which ones change and which stay.** Each change below is a named behaviour change in the commit body.
   - `same_recording(a, b)`, in order:
     1. Both recording MBIDs known: equal means same, different means different (`mb_trackid` / `recording_mbid`).
     2. Otherwise: title keys intersect, version markers are equal (album-wide markers of each side's album or folder included; `clean`/`edited`/`censored` read from album qualifiers only), and `lengths_agree`.
     3. Two release tracks of one release (`library._two_tracks`) are different.

     An "explicit" disambiguation is not consulted here. That is P1-B2/G1.
   - **Changed:** `album_keys(qualifiers="keep")` strips the year prefix, YouTube junk, scene and quality brackets, and remaster and reissue qualifiers, as today. It **keeps** bracketed edition words (`library._EDITION_WORDS`) and release-type brackets (`[Single]`, `[EP]`).
     - So `(1980) The Boys Light Up [Single]` is no longer `The Boys Light Up`.
     - `(2017) Album [Complete Edition]` is no longer `Album`, which agrees with what `find_album_duplicates` already decides through `row_edition`.
     - `[2005 Reissue]` and `[2001 Remaster]` still fold.

     `qualifiers="strip"` is today's `album_identity`. It stays the `library.album_identity` alias and is used only by `resolver._title_similarity` and `missing.find_on_youtube`'s search filter, so resolution scores and YouTube searches don't move. P3-H3 retires name heuristics for identified albums.
   - `same_album(a, b, policy)`:
     - Both release groups known: different groups are different albums.
       - `policy="release_group"`: one group is one album, whatever the edition.
       - `policy="edition"`: one group is one album only when the edition words also agree.
     - Otherwise, names: `album_keys` intersect (edition policy, plus edition words agree).
     - **rockstar:** with the release group known on both sides it is the same album, decided by group. With names only (the YouTube single has no group), today's answer stands: a different album. That is a **known gap** and goes in the golden table with that note; there is no new rule.
   - **Changed:** `ownership.lookup` gives `have` for a **same-title** match on the same album only when `agrees()`. A same-title copy whose length or version disagrees becomes `elsewhere`, with `library_mismatch=True` and `library_folder` set, so the album version downloads (§9).
     - A same-**video** match stays `have`: it is the identical audio.
     - `test_catalog.py:675` becomes `("elsewhere", True)`.
     - `ReleaseRow.jsx`'s `elsewhere` tooltip says "a different version or length" when `library_mismatch` is set and the folder is the album's own, instead of "on another album".
   - **Changed:** `units.split_key` still folds disc suffixes and edition or remaster suffixes. `Against All Oddz (Collector's Edition)` stays one unit, as `test_metadata.UnitsTestCase` requires. It no longer folds a qualifier that names other recordings:
     - album-wide version markers (`Live`, `Instrumental`, `Remixes`, `Acoustic`, …);
     - `Taylor's Version` (re-recordings).

     So `1989` and `1989 (Taylor's Version)` in one folder are two units.
   - `file_quality` is today's `format_quality` plus truncation, plus `tag_quality` and the reasons when `tags=True`. `library.score_file` is `file_quality(tags=True)`; `apply._audio_quality` is `file_quality(tags=False)[0]`. Every number stays the same.
3. **One assignment matcher, `identity.assign`,** used by `missing._match` and `resolver.match_tracks`. It is a global greedy pass over scored pairs; each caller adapts its rows into `{keys, marks, length, number, disc, position, upload}`.
   - Marker rule, at **every** rank: `want.marks ⊆ held.marks ⊆ want.marks ∪ context`. The context is the release's album-wide markers: a Live release-group type, the medium title. The resolver already uses this rule for rank A only.
   - Ranks:
     - **A:** title keys meet, and `lengths_agree` (5 s, or 30 s for an upload). Quality is 3, +1 for the same number, minus gap/10.
     - **B:** same number and disc, gap ≤ 2.5 s. Quality is 1.5 minus gap/10. This is resolver 142 and missing rank 1.
     - **C (changed, new for the resolver):** a near-identical title and gap ≤ 5 s. Quality is 1.0 minus gap/10.
       - "Near-identical": SequenceMatcher ≥ 0.75 on `key_text`, the same token count, and the differing tokens are not numerals (digits or roman numerals).
       - So `Come`/`Came` match, and `Part I`/`Part II` don't.
       - Missing's rank 2 becomes this rank.
   - **Changed:** the picker no longer takes `Black Friday (Live)` for the studio `Black Friday`. The resolver now matches `Come`/`Came` at 2.7 s.
4. **Migrate the callers** to identity, one per sub-step:
   - `ownership.lookup`: its album match goes through `same_album(policy="edition")` on names.
   - `library.find_track_duplicates` and `find_album_duplicates`: through `same_recording` and `same_album`.
   - `apply.copies_plan` and `merge_plan`: `lengths_agree` and `file_quality`.
   - `issues.plan_album_copies`: recording sets through identity helpers (`plain_recordings` and `matched_recordings` move there), with **no** rule change. P1-B2 owns the explicit rule.
   - `units.split_key`;
   - `api._different_album`: `same_album`, release group first (P0-4's order is kept);
   - `plexsync.album_share`: `same_album` on tags;
   - `discover._owned_by` and `owned_albums`: `album_keys`, with the release's type passed, so a single still matches a `[Single]` folder and its plain key.
5. **No other rule changes.** If an existing test fails for a reason outside the five changes named above, the migration is wrong. Fix the code, not the test.

**Acceptance tests (write first; record what fails):** `tests/test_p1_identity.py`, over `tests/identity_golden.py`.
- **The golden table.** At least 60 rows `(kind, a, b, policy, expected, case)` taken from the triage. Kinds are `recording`, `album`, `path`, `artist` and `quality`.
  - Recording rows:
    - Black Friday studio vs `(Live)`; Come/Came (same position, 2.7 s);
    - `Wake Up Dead` disc 1 vs `Disc 2 - Live`; Rammstein 245 vs 275 s;
    - Outnumbered 265 vs 295 (different) and Chemical 230 vs 231 (same);
    - Hilltop Hoods upload `- Never Coming Home (Official Video)` 22 s off (same, with the upload slack) vs a clean title 22 s off (different);
    - `Exit Sign (Instrumental)` in the Instrumental Edition vs the vocal one; `04 - Bicycle Race (Live)`;
    - `Song - 2011 Remaster`; `7 Rings`; Keep On Moving 4:20 vs 6:25; `ON` vs `ON (feat. Sia)` on one release;
    - clean vs explicit; equal recording MBIDs with different titles (same); different MBIDs, same title and length (Doggystyle with-intro vs plain: different);
    - remix, acoustic and sped-up vs plain; Paul Kelly & The Messengers credit; `Ariana Grande - Baby I (Official Video)`; `SALMO FEAT. ENIGMA - MUNCHIES`; `Kapitel 2 - Chronicles, Vol. 1`;
    - All Bout U / All About U; Part I / Part II; Dead & Alive (Live type, plain title: same in context).
  - Album rows:
    - `(2019) Rare Ass Shit`; Seventy vs `(Deluxe Edition)`; `[2001 Remaster]`; `[Complete Edition]`; The Boys Light Up `[Single]`;
    - `1989 (Taylor's Version)`, both for `same_album` and for `split_key`;
    - rockstar with release groups equal (same) and with names only (different, **known gap**);
    - `Doggystyle [2005 Reissue]` (same by name and group); Rolling Papers vs `(Deluxe Version)` (different under `edition`, same under `release_group`); Thriller 25 vs Thriller (the same pair);
    - the Yours Truly conventions; `[FLAC 24 bit-44100Hz]`; My Everything (Deluxe); The Great Expanse (Instrumental Edition); `(Full Album)`;
    - `Live at Wembley` (the title's own words) vs `Album - Live`;
    - Against All Oddz (Collector's Edition) and `(Disc 2)` (same, `split_key` only);
    - a single and an album of one name in different groups (different).
  - Path, artist and quality rows:
    - `SAM SMITH/…` vs `Sam Smith/…`; NFC vs NFD `Måneskin`;
    - `AC/DC` vs `ACDC`; `Will Sparks, AnGy KoRe` split;
    - flac 16/44 > mp3 320; flac 24/96 > flac 16/44; m4a 256 > opus 129; a truncated flac (−20 s) < mp3.
- `GoldenTestCase.test_identity_agrees_with_the_table`: one `subTest` per row, against `identity`. It fails before, because the module doesn't exist.
- `CallersTestCase`, each through the real caller:
  - `test_ownership_lookup_follows_the_table`: a real `LibraryIndex` from rows inserted into a temp DB and loaded with `ownership.load_index`. `have` exactly when the recording and the album are both "same". Outnumbered gives `("elsewhere", True)`. It **fails before**.
  - `test_the_gate_lets_a_different_length_through`: `library_index.find_duplicate_match` on the real DB; a 30 s gap is not a skip. It **fails before**.
  - `test_the_picker_and_the_resolver_share_one_matcher`: `missing._match` and `resolver.match_tracks` over the recording rows that have positions. Black Friday (Live) is not picked; Come/Came matches; Part I/II doesn't. **Fails before.**
  - `test_track_duplicates_follow_the_table` (`library.find_track_duplicates`).
  - `test_album_duplicates_follow_the_table` (`find_album_duplicates`): a 1-track `X [Single]` folder inside album X is not a group. It **fails before**.
  - `test_split_key_keeps_taylors_version_apart`: `units.split_folder`. It **fails before**, while the AAO case stays one unit.
  - `test_different_album_follows_the_table` (`api._different_album` with an `_album_context` ctx).
  - `test_album_share_follows_the_table` (`plexsync.album_share`).
  - `test_discover_owned_follows_the_table`: `discover.owned_albums` from a temp DB, then `_owned_by`.
  - `test_one_quality_scorer`: `library.score_file` and `copies_plan`'s keeper choice rank the quality rows as `identity.file_quality` does.
- `test_catalog.py:675` updated to `("elsewhere", True)`. The commit says so.

**Real-data checks** (`work/p1-a/`, read-only sqlite, old vs new on the same rows):
1. Re-gate all 2,599 skips (the 1,008 Download-missing ones apart) with `lookup` before and after.
   - Count `have`→`elsewhere` by cause. Expected: about the 158 "length off" and part of the 72 "same title, other track".
   - Report the P0-4 Re-check preview: 102 tracks before; rockstar stays as the known gap.
2. **The picker.** Replay `missing._match` on the 346 Download-missing jobs' YouTube Music albums from `catalog_cache`. Count the picks that change and list the ones rejected for markers, Grinspoon among them.
3. **The resolver (the risky one).** For every `resolved` and `review` unit, run `score_release(rows, cached release)` with the old and new `match_tracks`.
   - Count the units whose mapping changes, and those whose score crosses the resolve or review thresholds. List every status flip.
   - If more than a handful flip from review to resolved, stop and report before committing: with auto-apply on, those albums get tags and moves.
4. **`split_key`.** Count the folders whose unit split changes and list them. Each one is a re-keyed unit until F2's successor matching lands in the same phase.
5. **`album_keys`.** Count the Discover `owned` flags that flip, the album duplicate groups before and after, and the `_different_album` labels on the 1,008 skips (257 after P0-4).

**Risks:**
- Matching decides downloads, merges and Trash. Every change must move toward fewer pairings in the destructive paths (`merge_plan`, `copies_plan`, `find_*_duplicates`). The golden table and dry runs 1–5 show it does.
- The gate now downloads mismatched same-title tracks. That adds files (at worst a harmless duplicate) and never removes one.
- The resolver's rank C can raise coverage and confidence, which feeds auto-apply: see dry run 3.
- `split_key` re-keys a few units. F1 and F2 deploy together, so F2's successor matching keeps their decisions; deploy them only together.

---

## P1-F2 Stable album identity (lane b, L, after a)

**Line refs (re-verified):**
- `db.py`:
  - `md_albums` v9 at 284-309 (key TEXT PRIMARY KEY, path-valued); `md_issues` key UNIQUE at 323-338; `md_overrides` PK `(entity, ref, field)` at 385-392; `md_state` at 394;
  - `_migrate` 486-499: one `executescript` per migration, then the version row in a second statement, so an interruption between them reruns a non-idempotent `ALTER`.
- `metadata/units.py`: `split_folder` 49-66 (key `folder|album`), `load_unit` 134, `sync_registry` 155-197 (inserts new keys at 171-174, deletes and closes vanished keys at 181-195).
- `metadata/apply.py`:
  - `rekey_unit` 152-173: if the new key exists, it deletes the moving row (157), and it marks the old key's open issues `applied` (168-170);
  - `auto_paused` 394 and `refuse_oscillation` 448-463 key md_state by **album key** (`AUTO_PAUSED + ctx.key`);
  - `do_album_copies`' pin 905-907; the `merge_keeper` row is keyed by folder (909-913).
- `metadata/issues.py`:
  - `planned` 156-163: `key = f"{kind}:{ref}"`, and every unit kind uses `ref = key` (`add()` 321);
  - `save` 166-230: dismissed and queued stay as they are, everything else re-opens;
  - `foreign_detail` 564-583 and the flip-flop branch 519-530 name album keys in `detail.units`;
  - the `album_copies` key is `f"{group}:{min(c['key'])}"` (974).
- `metadata/runner.py` `_check_unit` 544-624: with no rows it deletes the row (547); `overrides_for("album", key)` 578; `auto_paused(key)` 619. Also `_decide_one` 724-741, `_files_pass` 747-799 (the copies' keys at 776-777), and `_apply_plex` 944-984, which marks a plex-stage issue `applied` by album_key at 957-963.
- `metadata/routes.py`: album routes 148-228 take `key`; overrides 386-409 use `ref = key`.
- `metadata/views.py`: `_album_summary` 273-295, `artist` 298, `_issue_album_targets` 345-352, `album(key)` 369-512.
- `library.py` `merge_artist_folders` 1612-1691: **`DELETE FROM library_index WHERE artist_dir = ?` at 1666**, so the moved files come back as new rows at the next scan.
- Frontend:
  - `components/library/issues.js` `parseHash` and `buildHash` 152-164 (`#/library/<artist>/<album key>`);
  - `Library.jsx` (`albumKey` state 49; `navigate` 115-131; `navigateIssue` 152-163; the album fetch 190-200; follow-renamed 226-234);
  - `ArtistView.jsx` (select by `album.key` 291-417); `DetailPane.jsx` (album API calls by `album.key`; overrides `refKey: album.key` 675, 745);
  - `api.js` `libraryAlbum(key)` 187.

**Premise check: holds.**
- Bliss n Eso *Circus in the Sky*:
  - `review_match:Bliss n Eso/Circus In The Sky` was `applied` (accepted) at 10-26 06:01;
  - `review_match:Bliss n Eso/(2013) Circus in the Sky` was open again at 06:22;
  - today the row has `manual=0, status=review`.
- 163 dismissed unit issues point at album keys no longer in `md_albums`: 107 unresolved, 42 extra_tracks, 14 folder_artist. They are decisions lost to key changes.
- Sizes on the copy:
  - 8,928 `md_albums` rows (154 split keys `…|…`; manual 545, no_release 207, ignored 1);
  - 57,367 issues, of which 47,523 are unit kinds; **19,795** of those join a live `md_albums` key (applied 16,496, dismissed 2,514, open 784, failed 1). The other 27,728 are history for vanished keys;
  - `md_overrides` 0 rows; `md_state` has no `auto_paused:` rows yet (phase 0 isn't deployed).

**§2 rules:** decisions are the user's and must stick. A missed duplicate is
harmless; a false one deletes a file. So a union of decisions never lets one
album's pinned release reach another album's files.

**Files:**
- `backend/app/db.py`;
- `backend/app/metadata/units.py`, `apply.py`, `issues.py`, `runner.py`, `routes.py`, `views.py`, `art.py`, `desired.py` (only if `overrides_for` changes);
- `backend/app/library.py` (`merge_artist_folders` keeps index rows);
- `frontend/src/api.js`, `components/Library.jsx`, `components/library/ArtistView.jsx`, `DetailPane.jsx`, `issues.js`;
- new `backend/tests/test_p1_unit_id.py`;
- fixture-only updates, if needed: `test_p0_oscillation.py` (it reads `auto_paused:{key}` at 348, 352 and 398) and `test_p0_plex_foreign.py` (an override `ref` at 162). Name them in the commit.

**Fix outline:**
1. **Atomic `_migrate`** (it also takes the BEGIN/COMMIT half of P2-D8). Each pending migration runs as `executescript("BEGIN;" + sql + "; INSERT INTO schema_migrations … ; COMMIT;")`. On error: `ROLLBACK`, then re-raise. An interrupted or failed migration leaves no trace and simply runs again.
   - Allow a migration entry to be a Python callable `async def (conn)`. It runs inside the same BEGIN/COMMIT with its version row. Lane c needs this.
2. **Migration v12** (SQL; idempotent by atomicity, guards anyway):
   - `md_albums.unit_id TEXT` (16 hex, `lower(hex(randomblob(8)))` for every row); `CREATE UNIQUE INDEX idx_md_albums_unit`.
   - An `AFTER INSERT` trigger fills a NULL `unit_id`. Every insert path, including tests' raw INSERTs, then gets one; app code also sets it.
   - `library_index.unit_id TEXT` plus an index. This is the file-identity carrier: an app move UPDATEs the row's path, and the `unit_id` stays on it.
   - `CREATE TABLE md_unit_aliases (unit_id TEXT PRIMARY KEY, successor TEXT NOT NULL, old_key TEXT, reason TEXT, created_at TEXT NOT NULL)`.
   - Rewrite unit-kind issue rows whose `album_key` is a live key and whose key is `kind:album_key`: `key = kind||':'||unit_id`, `ref = unit_id`. The `album_key` stays the location, and history rows stay as they are.
   - `md_overrides`: `ref` key → unit_id, for `entity='album'` and a live key.
   - `md_state`: `auto_paused:<key>` → `auto_paused:<unit_id>`. `merge_keeper:<folder>` stays: it guards the folder.
   - `missing_tracks:<release>` and `album_copies` are left alone. Missing tracks are already move-stable (see Defaults). The 3 open `album_copies` rows are re-planned under the new key next cycle, and `save(all_scope)` closes the old ones.
   - `library_index.unit_id` is stamped lazily by the next `sync_registry`, which is the first step of every cycle, not in the migration.
3. **The registry keeps identities** (`units.sync_registry`, plus a `units.successor(unit_id)` used by both the registry and `_check_unit`):
   - A known key keeps its row; stamp its rows' `library_index.unit_id`.
   - For each vanished key V: its successor is the new or existing key holding the plurality of the rows stamped V.
     - A new key: UPDATE V's row (`key`, `dirpath`, `artist_dir`, `next_check=0`), and re-point `md_issues.album_key`, `md_issue_refs` and `md_changes.album_key`, as `rekey_unit` does.
     - An existing key K, or a second vanished unit landing on the same key: **merge** V into the survivor (step 4).
     - No successor: drop it as today.
   - New keys with no stamped rows get a fresh unit_id. A split's smaller piece gets a fresh unit_id, and the larger piece keeps the folder's.
4. **Merging units: union decisions, never conflicting ones** (`units.merge_into(v, k, reason)`, used by the registry and by `apply.rekey_unit`).
   - `ignored`: either one. `no_release`: the survivor's, else V's when the survivor has no pin.
   - `manual` + `release_mbid`: the survivor's when it has a pin. Else V's, but only when the survivor's release group is unknown or equal to V's.
   - `md_overrides`: per field, the survivor's wins.
   - `auto_paused`: the later `until`.
   - V's unit-kind issues: re-key `kind:V` → `kind:K` where `kind:K` doesn't exist. Where it does, a dismissal on either side survives on K's row, and V's row is closed `applied` with `error="merged into <K>"`.
   - Write `md_unit_aliases(V → K)`, then delete V's `md_albums` row.
5. **Movers:**
   - `apply.rekey_unit`: when the new key exists, call `merge_into` instead of deleting. Don't mark the old key's issues `applied`: re-point their `album_key`, and the re-check upserts the same rows. Also stamp the moved rows' `unit_id`.
   - `_check_unit` with no rows: try `units.successor(unit_id)`; only with none does it close and drop, as today.
   - `library.merge_artist_folders`: UPDATE `library_index` `path`, `dirpath` and `artist_dir` for every moved file (exact pairs), instead of deleting the source artist's rows. Delete only the leftovers it trashes. Lane c replaces this with `relocate()`.
6. **Keys on unit_id everywhere a unit is meant:**
   - `plan_unit(..., unit_id=None)`: `add()` uses `ref = unit_id or key`. The default keeps the existing tests' direct calls as they are.
   - `_check_unit` passes `record["unit_id"]`, `overrides_for("album", unit_id)`, `auto_paused(unit_id)`.
   - `refuse_oscillation` writes `AUTO_PAUSED + unit_id`.
   - `album_copies`: key `f"{group}:{min(unit_ids)}"`, with `detail.copy_units` and `detail.keep_unit`. `_files_pass` and `copies_plan` resolve unit_ids to current keys when they apply.
   - `plex_foreign`: `detail.unit_ids` beside the keys and folders (the shared and flip-flop branches). `_issue_album_targets` maps `unit_ids` to the current keys.
   - `_apply_plex`: decide the "planned again under the new key" closing by **issue id** (an id that is in `fresh` is not marked), not by `album_key`. With stable keys the same row is re-planned, and the old check would overwrite a `failed` with `applied`.
7. **The API and the UI:**
   - `units.resolve(ref) -> {key, unit_id} | {redirect: {unit_id, key, artist_dir}} | None`. `ref` is a unit_id, an alias (followed for up to 10 hops), an alias's `old_key`, or a live key (old bookmarks).
   - Every album route resolves its `key` parameter through it: `/album`, `/album/recheck`, `/album/match`, `/album/no-release`, `/album/ignore`, `/album/candidates`, `/album/download-missing`, `/album/seed`, `/art?key=`, and overrides (`ref` → unit_id).
   - `views.album` returns `{"redirect": …}` for a merged-away album. Album summaries and issue rows carry `unit_id` (issues joined on `album_key`).
   - Frontend:
     - the URL's second part and the selection are `album.unit_id` (a `key` fallback for rows without one);
     - API calls pass `unit_id`; overrides use `ref: album.unit_id`;
     - `navigateIssue` uses `issue.unit_id || issue.album_key`;
     - a `redirect` reply navigates there with `replaceState`;
     - `library.changed` events add `unit_ids`.

**Acceptance tests:** `tests/test_p1_unit_id.py`. Real files, scan, registry, `check_unit` and executors; MusicBrainz faked.
- `DecisionsTestCase.test_a_dismissal_survives_a_move`:
  - setup: a resolved unit with an unmatched extra file, which plans `extra_tracks` plus a layout `move`;
  - run: dismiss `extra_tracks`; apply the move with the real `do_move`; then scan, `sync_registry` and `check_unit` at the new key;
  - assert: the same issue id is still `dismissed`, its `album_key` is the new key, and no open `extra_tracks` exists for the unit.
  - It fails before: the old row ends `applied` and a new open row appears.
- `test_an_accepted_match_survives_a_move_into_an_existing_key`:
  - setup: units A and B of one release in two folders (disc-folder case, different tracks); A is accepted (`manual=1`, release R) through the real `_decide_one`; A's layout move goes into B's folder;
  - assert: B's row ends `manual=1` with R, `md_unit_aliases` has A→B, and `review_match` doesn't re-open.
  - It fails before (`rekey_unit` deletes A's row).
- `test_an_artist_merge_keeps_the_albums_decisions`:
  - setup: `Old/Album` with `ignored=1` (or manual), a dismissed issue and a genres override;
  - run: the real `merge_artist_folders(["Old"], "New")`, then scan and `sync_registry`;
  - assert: the same unit_id at `New/Album`, every decision, the issue still dismissed, and `overrides_for("album", unit_id)` returning the override.
  - It fails before.
- `test_a_split_folder_keeps_its_unit_and_a_rejoin_merges_it`: retag one file of folder X to another album, then sync. The larger piece keeps X's unit_id. Put the tag back and sync: one unit, the aliases written, the decisions unioned.
- `test_a_pin_never_crosses_release_groups`: merging V (pinned R1, group g1) into K (unresolved, group g2) does not take the pin.
- `test_a_merged_away_album_redirects_to_its_keeper`: after a real `do_album_copies` and a registry pass, `GET /album?key=<copy unit_id>` (through the router) answers `redirect` to the keeper. The keeper's unit_id and its old path key resolve too.
- `test_an_auto_pause_follows_the_album`: `refuse_oscillation` pauses the unit_id; after a move, `_check_unit` at the new key still returns `auto_ids=[]`.
- `MigrationTestCase`:
  - `test_existing_rows_get_unit_ids_and_their_issue_keys_follow`:
    1. Build a v11 DB (patch `db.MIGRATIONS[:11]`) and insert md_albums rows (one split key), unit-kind issues (open, dismissed, a history row for a vanished key), an override and an `auto_paused` row.
    2. Run the real `connect()`.
    3. Assert: unit_ids are unique, keys are rewritten, the history row is untouched, and the override and pause are re-keyed. A second `connect()` changes nothing.
  - `test_an_interrupted_migration_leaves_nothing_half_done`: v12 with a failing last statement makes `connect()` raise; the version stays 11 and `md_albums` has no `unit_id` column. With the real v12 it then succeeds.

**Real-data checks** (`work/p1-b/`, a `/tmp` copy):
- Time v12 on the 1.4 GB copy (expect a few seconds). Run it twice. Report the units numbered (8,928), the issue keys rewritten (about 19,795), the overrides (0) and the md_state rows moved.
- Then run one `sync_registry()` on the migrated copy. Report the time, the index rows stamped (about 64k), the successors and merges found (expect about 0, since nothing moved), and that no row was dropped that wasn't dropped before.
- Count the 163 orphaned dismissed issues; they are not repaired (see Defaults).
- Run the frontend build check.

**Risks:**
- The migration runs on the live DB at the app's next start. It is atomic: interrupted, it rolls back and runs again.
- The registry's successor logic decides which album a row's decisions belong to. A bug could move a pinned release onto another album's files, so the pin rule in step 4 and `test_a_pin_never_crosses_release_groups` are the guard.
- No file is touched by this ticket.
- A folder renamed outside the app (on the share) still loses its identity, as today: its rows are deleted and re-inserted by the scan. That is a known gap.
- URLs change format; old path bookmarks still resolve while the key exists.

---

## P1-F3 One relocate service for every path change (lane c, M, after b)

**Line refs (re-verified):**
- The path tables:
  - `library_index`: path, dirpath, artist_dir, unit_id;
  - `tracks.library_path`; `jobs.target_dir`; `md_albums` key and dirpath;
  - `md_issues.album_key` and `md_issue_refs.album_key`;
  - `library_exclusions` key_a/key_b (album scope by folder, track scope by path);
  - `md_state` `merge_keeper:<folder>` and `plex_readd:<folder>`; `plex_items` path and folder.
- Today's movers and what they update:
  - `apply.rekey_paths` 125-135 covers index path, dirpath and artist_dir, `tracks.library_path` and track exclusions; `rekey_folder` 138 covers album exclusions only;
  - `apply._relocate` 481-539, `do_album_copies` 810-925 (its per-mover rekey at 879), `_file_in_downloads` 319-373 and `revert` 1505-1606 (rekey at 1584) use those;
  - `library.trash_paths` 1384-1505 deletes index rows only (1488-1499); `restore_batch` 1532-1564 re-indexes nothing; `merge_artist_folders` 1612-1691 (after F2: rows updated, tag rewrites not journalled, `_retag_album_artist` 1694-1737 uses mutagen easy);
  - `library_index.add_entry` 52-66 writes 8 columns, with `dirpath` NULL; worker 437-439 calls it;
  - `importer.import_track` 79-167 builds its path from YouTube Music tags with no on-disk spelling (`build_track_relpath` at 103);
  - `jobs.target_dir` is never rekeyed.
- The case-sensitive joins: `missing._album_download` 103-112 and `_browse_from_history` 193-202, `apply._file_in_downloads` 331-333, and `api._album_context` 270-277.

**Premise check: holds.**
- **2,027** completed tracks have a `library_path` that isn't indexed; **238** of them differ from an indexed path only by case. 21 jobs have a `target_dir` with no indexed folder. 953 album-scope exclusions name no indexed folder.
- The case-insensitive check took 49 s with `lower()` on the copy: there is no `path_key` column or index today.
- **Path deviation:** the triage's `library/relocate.py` can't exist beside the module `app/library.py`, so the service is **`app/relocate.py`**.

**§2 rules:** `/library` ignores case, so reuse the on-disk spelling, and a
case-only rename never creates a folder. Nothing is deleted; every change is
journalled and can be undone ("Nothing is deleted, and everything can be undone",
README 369).

**Files:**
- new `backend/app/relocate.py`;
- `backend/app/db.py` (v13; register a deterministic `path_key()` SQL function in `connect()`);
- `backend/app/library.py` (scan writes `path_key`; `trash_paths` → `forget`; `restore_batch` → re-index; `merge_artist_folders` → `relocate`, with moves, tags and trash journalled in one batch);
- `backend/app/library_index.py` (`add_entry` writes the full row); `backend/app/importer.py` (on-disk spelling); `backend/app/worker.py` (the import call site);
- `backend/app/metadata/apply.py` (rekey helpers → `relocate`; every mover); `desired.py` (`on_disk` delegates); `missing.py` (`path_key` joins); `backend/app/api.py` (`_album_context` lookups);
- `backend/app/identity.py` (`path_key` is already there);
- new `backend/tests/test_p1_relocate.py`; `backend/tests/test_library.py` only if a merge-artist result field changes.

**Fix outline:**
1. **`relocate.relocate(pairs, *, batch=None)`**, path pairs `old → new`. All the DB updates are in one function, with a clear transaction boundary. F5 wraps it in `db.transaction()`; until then it runs statement by statement, as today.
   - `library_index`: UPDATE path, dirpath, artist_dir and `path_key`. The row keeps every other column (tags, `provider_track_id`, `unit_id`).
   - `tracks.library_path`, matched exactly and by `path_key`.
   - Track exclusions (UPDATE OR IGNORE plus delete), as today.
   - The folder step, for each `old_folder → new_folder` whose old folder has no indexed audio left:
     - `md_albums` through F2's `rekey_unit` and `merge_into`;
     - album exclusions; `jobs.target_dir` (active and finished alike);
     - `md_state` `merge_keeper:<old casefold>` → new, and `plex_readd:` the same way;
     - `plex_items` rows under the old folder are marked for re-read (`detail_at = 0`); Plex's own paths are not rewritten.
   - Bump `md_state.library_generation` once per call (P2-D3 reads it later).
   - **`forget(paths)`** for Trash: delete the index rows and keep `tracks.library_path` as history. The property below counts a path held in Trash (`library_trash`, `restored_at IS NULL`) as resolved.
   - **`unforget(paths)`** for Restore: re-index the restored files at once (`apply.reindex`).
2. **`path_key`**:
   - v13 is a Python step: `library_index.path_key TEXT`, an index, and a backfill in 2,000-row chunks inside the migration's transaction;
   - the scan's `_COLUMNS` and `_UPSERT` write it, and so does `_read_file`'s row;
   - the `path_key()` SQL function lets a join write `l.path_key = path_key(t.library_path)`, which uses the index for each `t` row.
   - The four case-sensitive joins above switch to it.
   - **No data repair in the migration.** The 2,027 stale references stay as they are and are only reported; repairing them is phase 5's, with the user's go-ahead (§2 Safety).
3. **Every mover calls relocate:**
   - `apply._relocate`, `do_album_copies` (per mover, keeping P0-1's order), `_file_in_downloads`, `revert`'s `moved_back`, `do_merge` through `_relocate`;
   - `library.trash_paths` → `forget`; `restore_batch` → `unforget`; `merge_artist_folders` → `relocate`;
   - `apply.rekey_paths`, `rekey_folder` and `_rekey_exclusions` become thin wrappers or go.
4. **The importer** resolves the destination's artist and album folders to their on-disk spelling before building the path. It uses `relocate.on_disk(folder)`:
   - first the index, by `path_key` prefix;
   - then `os.listdir` of the library root and the artist folder, casefold and NFC, for a folder the index doesn't know yet.

   `desired.on_disk` calls the same function, so the planner and the importer agree. `add_entry` becomes a full `_read_file` upsert that keeps `provider_track_id`, so the new file has `dirpath`, its tags and `path_key` at once.
5. **The artist merge is journalled and undoable.** One `md_changes` batch holds:
   - `move/file` per moved file;
   - `file/tags` per rewritten tag, written with `tagio.write` and its `before` values instead of mutagen easy;
   - `trash/files` for the leftovers.

   `apply.revert(batch)` then undoes the whole merge. The function lazily imports `apply.journal`, as `apply` imports `library` today.

**Acceptance tests:** `tests/test_p1_relocate.py`. Real files and the real scan, registry and executors.
- `dangling(db)` lists every reference in every table that resolves to no indexed file or folder (case-insensitively, by `path_key`) and isn't in Trash:
  - completed `tracks.library_path`; live `md_albums` keys and dirpaths; open unit issues' `album_key`; `md_issue_refs` of open issues;
  - both exclusion scopes; active `jobs.target_dir`; `md_overrides` refs (to a unit_id); `merge_keeper:` folders.
- `PathsResolveTestCase` asserts `dangling == []` after each mover:
  - `test_after_an_import` (`import_track` into a case-variant artist folder); `test_after_a_move`; `test_after_a_merge_of_copies`; `test_after_filing_in_downloads`;
  - `test_after_an_undo`; `test_after_trash_and_restore`; `test_after_an_artist_merge`.
  - The property test is `test_any_sequence_of_movers_keeps_every_path_resolving`: a seeded random sequence of 12 movers over one small fixture (two artists, three albums, one job), with the assertion after every step.
  - Each of these fails before for at least one table: `jobs.target_dir`, a case-variant `tracks.library_path`, or an artist merge's exclusions.
- `test_an_artist_merge_keeps_its_files_indexed_with_their_video_ids`: `provider_track_id` survives, and no rescan is needed.
- `test_an_artist_merge_can_be_undone`: `revert(batch)` restores the folders, the tags (album artist and artist) and the Trash.
- `test_the_importer_uses_the_on_disk_spelling`: `SAM SMITH/` exists; an import for "Sam Smith" lands in `SAM SMITH/…` with no new folder, and the index row is complete (`dirpath`, `path_key`, tags).
- `test_a_case_only_difference_still_joins`: `missing._album_download` finds a job's track whose `library_path` differs by case.
- `test_v13_backfills_path_keys_atomically`: the backfill is complete, a rerun is a no-op, and an injected failure leaves v12.

**Real-data checks** (`work/p1-c/`, a `/tmp` copy):
- Time v13: the `path_key` backfill of 64k rows.
- Report `dangling()` per table on the copy as a baseline (it is not fixed).
- Replay one real artist merge on a container-local copy of two small artist folders (e.g. a split spelling from `find_split_artists`). Show it is journalled, then undo it.

**Risks:**
- relocate rewrites references in many tables. A folder prefix must match exactly the folder or `folder + "/"`: `Album` must never touch `Album 2`.
- The artist merge now writes tags through `tagio`, with the same fields and journalled.
- The importer's spelling lookup must never pick a different album: match on the whole folder `path_key`, not a prefix of the name.

---

## P1-F4 Writers coordinate, and albums with active jobs are "settling" (lane d, M, after c)

**Line refs (re-verified):**
- There is no folder lock anywhere. `runner._lock(key)` (533) is per album key, check versus apply only; `apply.SCAN_HOLD` (977) holds scans during a Plex re-add.
- `importer.import_track` 79-167 (mkdir, copy, rename) and the worker call 426-441, which reads `job["target_dir"]` from the job row taken at 354, before the download.
- `runner._files_pass` 747-799: `_start_album` (894-917) applies the **QUEUE_STAGE** (Download missing → `worker.enqueue` with `target_dir`) at 911-912, and the file stage follows at 784-785 **in the same pass**. That is the triage's 776-781 and 897-907.
- `runner._check_unit` 544-624: `plan_unit` 582 and `missing.refresh` 612.
- `issues.plan_unit` layout 427-458 (the P0-1 guard at 443-446); `plan_album_copies` 930.
- `apply` executors: `do_tags` 203, `do_track_order_files` 246, `do_move` 466, `do_merge` 684, `do_album_copies` 810, `_tidy` 542.
- `library.trash_paths` 1384; `library.scan` 540-650 (upsert 612-613, prune 627-641).
- `worker._recompute_job` 697-736 (the terminal transition at 725-734).
- `models.ACTIVE_JOB_STATUSES` 86-89: queued, paused, resolving, downloading, importing.

**Premise check: holds.** P0-1's residual races, from the phase 0 report items 3
and 5, are real in the code:
- `do_album_copies` writes tags at the source (832-850), then trashes (862-871), then moves (872-891). A cycle's `library.scan` in between reads a retagged mover at its source.
- Nothing stops a check, a merge or a re-tag of an album with a running download.
- The queue stage enqueues Download missing with today's folder, and the move in the same pass leaves that `target_dir` stale. Lane c's relocate now rekeys `jobs.target_dir`, but a track already being imported still races the move.

**§2 rules:** the metadata manager does **not** move, merge or re-tag an album
while a job for it is queued or downloading. A download of an album already in
the library goes into that album's current folder.

**Files:**
- new `backend/app/locks.py`;
- `backend/app/importer.py`, `worker.py`, `library.py` (scan and `trash_paths`);
- `backend/app/metadata/runner.py`, `issues.py`, `apply.py`, `missing.py`;
- new `backend/tests/test_p1_settling.py`.

**Fix outline:**
1. **`locks.py`.** A registry of `asyncio.Lock`, one per unit folder (`identity.path_key(unit_dir(folder))`).
   - `async with locks.hold(*folders)` acquires in sorted order, so it can't deadlock.
   - It is **re-entrant per task**: a ContextVar holds the folders the current task holds, and a nested `hold` of one of them is a no-op. So `do_album_copies` → `trash_paths` and `_relocate` → `_tidy` don't deadlock.
   - `locks.busy(folder)` reports a lock held by someone else.
2. **Who holds what:**
   - `runner._files_pass` holds the album's current folder, its `desired.folder` and, for `album_copies`, every copy's folder, around `_start_album` and the file stage. It replaces nothing: the album-key locks stay.
   - `importer.import_track` holds the destination's folder from mkdir to rename, and `add_entry`.
   - `library.trash_paths` and `merge_artist_folders` hold the affected album folders.
   - `apply.revert` holds the folders its moves touch.
   - `library.scan` doesn't wait. At write time it **skips** the rows of busy folders and doesn't prune their paths; they are read at the next scan. That closes P0-1's "scan between tag write and move" race.
3. **The importer follows the unit.** After taking the lock, the worker re-reads `jobs.target_dir`, which lane c's relocate keeps current. So a move that ran while the track waited sends the import to the album's new folder.
4. **Settling.** `locks.settling()` is the set of folders, as `path_key`, that active jobs (`ACTIVE_JOB_STATUSES`) will import into:
   - their `target_dir`;
   - the folders of tracks those jobs have already imported.

   It is read from the DB and cached for 10 s; at apply time it is read fresh.
   - `_check_unit` passes `settling=True` to `plan_unit`. It then plans no `tags`, `track_order` (files), `move`, `move_conflict` or `extra_tracks`. Plex-only kinds are still planned.
   - `missing.refresh` doesn't file or refresh a settling unit's missing tracks.
   - `plan_album_copies` leaves out any set with a settling member.
   - At apply time, every FILE_STAGE executor and the QUEUE_STAGE raise `KeepOpen("waits for the download into <folder> to finish")` for a settling unit, whoever asked. A plan may predate the job. P0-1's breaker and layout guard run inside these executors, so they are covered too.
5. **When a job ends**, it is due again. In `worker._recompute_job`, when a job turns terminal, set `md_albums next_check=0, priority=10` for its settling folders and wake the runner. P1-B5 extends this hook later.
6. **No Download missing in the pass that moves the album.** In `_files_pass`, when an album's group has a QUEUE_STAGE issue **and** a move-kind issue (`move`, `move_conflict`, `album_copies`), the queue stage is held back.
   - It is kept on the album's plex-due state. `_apply_plex` re-checks the album under its new key (which re-files missing tracks), then `request_apply`s the held issue for the next pass.
   - The issue stays `queued`, and the job is enqueued with the album's new folder.

**Acceptance tests:** `tests/test_p1_settling.py`. Real files, importer, scan, registry, checks and executors.
- `test_an_import_during_a_planned_move_lands_in_the_current_folder` (the triage's acceptance):
  1. Unit X is resolved, with a layout move to Y planned; its move issue is open from an earlier check.
  2. A Download missing job into X is queued.
  3. `check_unit(X)` plans no move, and applying the stale move issue leaves it open with "waits for the download".
  4. The real `import_track` lands in X.
  5. The job ends: X is due, the check plans the move, and the apply moves the imported file with the rest.

  It fails before: the move runs and the import lands in a re-created X.
- `test_an_import_waits_for_a_running_move_and_follows_it`: an import into X starts while X's move holds its lock (patched to pause after the first file). The import waits, then lands in Y, because `jobs.target_dir` was rekeyed. **Fails before.**
- `test_a_scan_during_a_merge_does_not_index_the_movers_at_the_source`: `library.scan` runs from inside `do_album_copies` after the tag writes, like `test_p0_oscillation`'s `a_cycle_meanwhile`. No split key appears, and the movers' source rows keep their old tags. **Fails before.**
- `test_merges_and_the_layout_guard_wait_while_a_copy_is_settling`: `plan_album_copies` leaves the set out, and `do_album_copies` raises KeepOpen.
- `test_download_missing_is_not_queued_in_the_pass_that_moves_the_album`: `_files_pass` gets a missing-tracks issue and a move together. The move runs first, and `worker.enqueue` is called once, after the re-check, with `target_dir` set to the new folder. **Fails before.**
- `test_trash_waits_for_a_folder_held_by_an_apply`.

**Real-data check** (`work/p1-d/`): on the copy, report the size of the settling
set (active jobs and their folders), and how many units' checks would skip
file-side planning today.

**Risks:**
- A lock bug can stall the apply loop or the importer. The tests use real awaits, and `hold()` is sorted and re-entrant.
- The scan skipping busy folders must never prune them.
- A paused job keeps its album settling (see Defaults), so no fix reaches it until the job resumes or is removed. Its issues say why.

---

## P1-F5 Transactions and indexes (lane d, S, after F4)

**Line refs (re-verified):**
- `db.py` 501-519: `execute` and `executemany` commit every statement under the global lock; `fetchone` and `fetchall` take the lock.
- The callers to wrap:
  - `issues.save` 166-230;
  - `apply.rekey_unit` and F2's `units.merge_into`;
  - lane c's `relocate.relocate`, `forget` and `unforget`;
  - `units.sync_registry`'s writes;
  - `library.trash_paths`' DB part (1488-1499).
- The queries to index. `EXPLAIN QUERY PLAN` on the copy shows **`SCAN md_changes`** (285,911 rows) for all four:
  - `ours_locked` (`apply.py` 86-91: target, ref, field);
  - `current_path` (1482: target='move', ref);
  - P0-1's breaker `oscillation` (403: target='move', field='file', applied_at);
  - P1-G4's `match_flipflop` (`issues.py` 591: target='plex', field='match', ref, applied_at).
- `UPDATE tracks … WHERE library_path = ?` is `SCAN tracks`.

**Premise check: holds.** On the copy, md_changes holds 53,372 move/file and 345
plex/match rows. One `ours_locked` call takes about 0.35 s.

**Files:**
- `backend/app/db.py`: `transaction()`, and v14;
- `backend/app/metadata/issues.py`, `apply.py`, `units.py`;
- `backend/app/relocate.py`; `backend/app/library.py`;
- new `backend/tests/test_p1_transactions.py`.

**Fix outline:**
1. **`db.transaction()`**, an async context manager.
   - It takes the global lock, runs `BEGIN IMMEDIATE`, and on exit `COMMIT`s, or `ROLLBACK`s on an exception.
   - A ContextVar marks the owning task. While it is set, that task's `db.execute`, `executemany`, `fetchone` and `fetchall` run on the same connection **without** taking the lock and without committing, so the existing helpers (`save`, `rekey_*`) work inside it unchanged.
   - Other tasks wait on the lock until the commit.
   - A nested `transaction()` in the owning task is a no-op (re-entrant).
   - **Rule:** no non-DB await (file I/O, HTTP) inside a transaction, because it would hold the DB lock. Callers wrap only their DB part.
2. **Use it** in `issues.save` (one commit per call instead of about 4 per item), `rekey_unit` and `merge_into`, `relocate`, `forget`, `unforget`, `sync_registry`'s writes (after `all_units()`'s read) and `trash_paths`' index/trash rows.
3. **v14** (SQL):
   - `CREATE INDEX IF NOT EXISTS idx_changes_target_ref ON md_changes(target, ref, field)` serves `ours_locked`, `current_path`, `changed_by_us` and `match_flipflop` (target, ref and field equalities).
   - `idx_changes_target_field_at ON md_changes(target, field, applied_at)` serves the breaker's window scan.
   - `idx_tracks_library_path ON tracks(library_path)`.

**Acceptance tests:** `tests/test_p1_transactions.py`, on a real aiosqlite DB.
- `test_a_failure_rolls_back_every_statement`.
- `test_db_calls_inside_a_transaction_share_it`: `issues.save` inside one does not deadlock, and commits once.
- `test_another_task_waits_for_the_commit`.
- `test_issues_save_is_all_or_nothing`: an injected failure on the third item leaves no new row and no changed refs. It **fails before**.
- `test_a_failed_relocate_changes_no_table`: an injected failure after the `library_index` update leaves `tracks`, `jobs` and `md_albums` unchanged. **Fails before.**
- `test_sync_registry_is_atomic`: likewise, mid-merge.
- `IndexTestCase.test_the_journal_lookups_use_an_index`: `EXPLAIN QUERY PLAN` of the exact SQL strings of `ours_locked`, `current_path`, `oscillation`, `match_flipflop` and the `tracks.library_path` update shows `USING INDEX`. The test imports the strings from the modules, factored into module-level constants if needed, so they can't drift. **Fails before.**

**Real-data check** (`work/p1-d/`): time v14 on the copy, then time `ours_locked`,
the breaker query and `match_flipflop` before and after (about 0.3 s each before).

**Risks:**
- A transaction around a slow step blocks every writer. The "DB-only inside" rule is checked in review by grepping each `async with db.transaction()` block for non-DB awaits.
- Index creation on the live DB runs at start; time it.

---

## Lanes

| lane | branch | depends_on | tickets (in order) | files |
|---|---|---|---|---|
| a | `lane/p1-a` | – | P1-F1 | `backend/app/identity.py` (new), `library.py`, `ownership.py`, `api.py`, `discover.py`, `metadata/{missing,resolver,units,issues,apply,plexsync}.py`; `frontend/src/components/artist/ReleaseRow.jsx`; `backend/tests/test_p1_identity.py`, `identity_golden.py` (new), `test_catalog.py` (line 675) |
| b | `lane/p1-b` | a | P1-F2 | `backend/app/db.py`, `library.py`, `metadata/{units,apply,issues,runner,routes,views,art,desired}.py`; `frontend/src/api.js`, `components/Library.jsx`, `components/library/{ArtistView,DetailPane}.jsx`, `components/library/issues.js`; `backend/tests/test_p1_unit_id.py` (new); fixture updates in `test_p0_oscillation.py` and `test_p0_plex_foreign.py` if needed |
| c | `lane/p1-c` | b | P1-F3 | `backend/app/relocate.py` (new), `db.py`, `library.py`, `library_index.py`, `importer.py`, `worker.py`, `api.py`, `identity.py`, `metadata/{apply,desired,missing}.py`; `backend/tests/test_p1_relocate.py` (new); `test_library.py` only if a merge-artist result field changes |
| d | `lane/p1-d` | c | P1-F4, then P1-F5 | `backend/app/locks.py` (new), `importer.py`, `worker.py`, `library.py`, `relocate.py`, `db.py`, `metadata/{runner,issues,apply,missing,units}.py`; `backend/tests/test_p1_settling.py`, `test_p1_transactions.py` (new) |

Why: the lead's phase note. F1 and F2 are L-sized, so F1, F2 and F3 each get a
lane, chained a → b → c, and F4 then F5 share lane d after c. The tickets share
`apply.py`, `issues.py`, `units.py`, `library.py` and `db.py` throughout, so
parallel lanes would conflict. Test modules are disjoint: one new module per
ticket. Integration order: a, b, c, d, with each lane's tip containing the one
before it.

## Defaults chosen (the lead can override)

- **F1, same-video matches stay `have`.** The `agrees()` rule applies to same-title matches: a file downloaded from the same video is the identical audio.
- **F1, the resolver's title score and YouTube search keep today's album keys** (`qualifiers="strip"`), so resolution doesn't move for naming reasons. Only the callers the ticket lists get the new `[Single]`/edition rule.
- **F1, rockstar with names only** stays "another album": a known gap, recorded in the golden table, as the lead asked.
- **F2, `missing_tracks` stays keyed `missing_tracks:<release_mbid>`.** It is already stable across moves. Keying it by release group without P1-B1's per-group counting would make two releases' issues overwrite each other. P1-B1 changes key and counting together.
- **F2, union rules** (step 4): `ignored` from either side; a dismissal from either side survives; a pinned release crosses only within one release group; the survivor's overrides win. All of these keep files.
- **F2, the 163 dismissed issues already orphaned** by past key changes are not re-attached. That is a data repair for phase 5 (P1-R8-like), with the user's go-ahead.
- **F2, `merge_keeper:` stays keyed by folder.** It guards a folder, which by design doesn't move; lane c's relocate renames it if it ever does.
- **F2, external renames** (made on the share) still lose a unit's identity, as today: a known gap.
- **F2 also makes `_migrate` atomic** (BEGIN/COMMIT plus the version row). That is half of P2-D8; the backup half stays in P2-D8.
- **F3, the service is `app/relocate.py`**, because `app/library.py` is a module, not a package.
- **F3, no stale-reference repair in the migration.** The 2,027 unindexed `tracks.library_path` values (238 of them case-only) are reported, not rewritten (§2: a live data repair needs the user's go-ahead).
- **F3, Trash keeps `tracks.library_path`** as history, and "in Trash" counts as resolved.
- **F4, a paused job keeps its album settling.** §2 says "queued or downloading"; a paused job resumes into that folder, and not touching the album keeps files as they are.
- **F4, an explicit Apply on a settling album is also held** (KeepOpen with the reason), as §2 says "does not move, merge or re-tag … while a job is queued or downloading".

## Deploy notes (preliminary; the integrator fills in the timings)

- **Migrations at the next start:**
  - v12 (F2: unit ids, issue keys, aliases; about 20k issue rows);
  - v13 (F3: `path_key` backfill, 64k rows);
  - v14 (F5: three indexes on md_changes and tracks).

  Each is atomic and safe to interrupt. Their durations on the 1.4 GB copy go here.
- **No `RULES_VERSION` bump.** The F1 rule changes reach albums as they come due.
- **What the user may notice:**
  - album URLs become `#/library/<artist>/<unit id>`; old ones still open;
  - some Download-missing and album tracks that used to be skipped, with a mismatched length or version, now download (F1 dry run 1 gives the count);
  - a download into an album pauses that album's fixes until the job ends (F4).
- **The P0-4 note still applies:** the Re-check banner's rockstar-style singles remain a known gap after F1, so hold off pressing Re-check for those.
