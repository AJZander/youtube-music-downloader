# Kick-off: implement the 2026-11 review with a subagent workflow

Use a workflow (multi-agent orchestration) to implement every ticket in `docs/review/2026-11-triage.md`. I'm opting in to a larger scale than the default: **up to ~16 agents per phase workflow**, one workflow per phase. Load the `workflow-authoring` skill before you write the first script.

## Your role

You are the lead. You plan each phase, write and launch its workflow, read the results, spot-check the diffs yourself, keep the ledger and talk to me. Subagents write the ticket code. You don't, except for the two Plex spikes (below), which you run yourself with me watching.

## Read first

1. The whole of `docs/review/2026-11-triage.md`:
   - **§2 is the contract.** Where code and §2 disagree, §2 wins. Where the doc and I disagree, I win.
   - §5 has the tickets, §6 the order, §7 the checks, §8 the sandbox.
   - §9 lists defaults. Use them.
2. Your memory files: `review-2026-11-decisions`, `same-album-duplicates`, `plex-music-section-facts`, `sandbox-verification` and `library-share-case-insensitive`. Subagents can't see your memory, so put any rule they need into their prompts.
3. If `docs/review/progress.md` exists, you're resuming. Pick up from the ledger and don't redo finished tickets.

## Hard rules (copy this block verbatim into every agent prompt)

```
SAFETY: these are not negotiable
- The live app (mdl-app, mdl-web, mdl-pot) is running a download queue and a metadata cycle with auto-apply.
  Never start, stop, restart, build or `docker compose` it, and never `docker exec` anything that writes into it.
  Reading `docker logs mdl-app` is fine.
- Never write to the live DB (data/mdl.db) or to the music library (MUSIC_LIBRARY_PATH). Use sqlite `.backup` copies only.
- Any container that runs app code against real data uses `--network none`, and mounts data/ and the library `:ro`.
  Feature flags are NOT enforced at apply time yet (P2-D9), so a dry run with write access could really move or trash files.
- Plex: read-only GETs only, hand-written, never through app code paths.
  No merge, refresh, unlock, re-add, match, edit, delete or prefs change.
  Set logging.getLogger("httpx").setLevel(logging.WARNING) in every script. Never print the Plex token.
- Never `cat` .env: it holds the Plex token. Read single keys with grep, e.g. `grep '^MUSIC_LIBRARY_PATH=' .env`.
- Never purge Trash. Never use the user's email as MUSICBRAINZ_CONTACT.
- Line numbers in the triage doc are from 2026-11-02. Re-check each one before you edit.
  Counts in the doc are sizes, not test oracles: the live library keeps changing.
- When a rule is unclear, choose the action that keeps files: a missed duplicate is harmless, a false one gets deleted.
- If a ticket's premise turns out wrong, or it needs a product decision that §2/§9 doesn't answer, stop that ticket,
  mark it needs-decision with the evidence, and move on. Don't guess.
- No new Python or npm dependencies without the lead's approval. Match the surrounding code's style, naming and comment density.
```

## Sandbox commands (from §8)

**Backend tests.** Mount your *worktree's* `backend/`, not the main checkout's:
```
docker run --rm --network none -e PYTHONDONTWRITEBYTECODE=1 -v "<worktree>/backend:/src:ro" -w /src \
  --entrypoint /opt/engine-venv/bin/python music-downloader-mdl-app:latest -m unittest discover tests
```

**Real-data dry runs.**
- At the start of each phase, the lead makes **one** shared copy of the DB at `.cache/claude-sandbox/mdl-<phase>.db` (it's gitignored), using `sqlite3 'file:data/mdl.db?mode=ro' ".backup ..."`. The DB is 1.4 GB, so agents don't make their own copies.
- Agents mount the shared copy `:ro`, copy it into the container's `/tmp` if they need to write, and point `settings.data_dir` at that copy.

**Frontend build check.** Run `docker build -t mdl-web-sandbox ./frontend` from the worktree. Never use the compose image name.

**UI checks.** Use the harness in §8. Put screenshots in `.cache/claude-sandbox/`.

## Git

- Create `fix/review-2026-11-p0` off `main`. Commit the triage doc and this prompt there first.
- **Stack one branch per phase**, each starting from the previous phase's tip: `fix/review-2026-11-p0`, `-p1`, `-p2`, `-p3b`, `-p3`, `-p4`, `-p6`, `-p7`, `-p8`, `-p5`. That way I can deploy phases in order.
- Lane work happens in agent worktrees (`isolation: "worktree"`) on lane branches. The integrator merges the lanes into the phase branch.
- **One commit per ticket.** The subject is `P0-1: break the merge/move oscillation`. The body says what changed and gives the test evidence. End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never commit to `main`. Never push, force-push or rewrite published history.

## Ledger: `docs/review/progress.md`

Keep one row per ticket:

`ticket | status (todo / in-progress / done / blocked / needs-decision / deferred) | branch | commit | test evidence | notes`

Update it after every workflow and commit it on the phase branch. It's how you resume if context runs out.

## Shape of each phase workflow

1. **Plan** (1 agent, read-only):
   - Re-verify every ticket in the phase against the current code: line refs, premise, files touched.
   - Group the tickets into **lanes** with disjoint file sets, at most 4. Tickets that share a file go in one lane, in §6 order.
   - Write `docs/review/progress/phase-<n>-plan.md`.
   - Return the lanes, plus any ticket whose premise failed.
2. **Implement** (1 agent per lane, worktree, lanes in parallel, tickets within a lane in order). For each ticket:
   - Write the acceptance test(s) from the ticket first, and record that they fail.
   - Implement the fix.
   - Run the full backend suite in the sandbox, and the frontend build if frontend files changed.
   - Schema changes go through `db._migrate`. Run the migration on a copy of the shared phase DB and report how long it took.
   - Commit, one commit per ticket.
3. **Review** (1 agent per lane, read-only, adversarial). Check each diff against:
   - **§2 and the ticket's acceptance criteria.** Does each test really prove the criterion, or does it only test a mock?
   - **Every move, merge, retag or trash path.** Is it safe if interrupted? Can it be undone through `md_changes` or Trash? Does it respect settling (P1-F4) and the feature flags?
   - **The DB.** It's one aiosqlite connection behind a global lock, committing per statement. Check for races and for half-written state.
   - **Secrets.** The token is never logged.
   - Return findings with file:line and a concrete failure scenario. Return no style nits.
4. **Fix** (only for confirmed findings, in the lane's worktree). Then re-run the suite.
5. **Integrate** (1 agent):
   - Merge the lanes into the phase branch, resolving conflicts, and run the full suite.
   - Run the §7 queries that apply to this phase, as a dry run against the shared DB copy, where the check can be simulated.
   - Write `docs/review/progress/phase-<n>-report.md`, covering tickets done, tests added, dry-run numbers, open findings and anything needing me.

After each workflow:
- Read the report.
- Look at the integrated diff yourself.
- Update the ledger.
- Give me a summary of at most 5 lines.
- Then go on to the next phase, unless a stop point below applies.

## Phase order

| Run | Tickets | Notes |
|---|---|---|
| p0 | P0-1 … P0-6, P1-G4 | Stops the live damage. G4 ships here, per §6. Do P0-1 first, in its own lane. |
| p1 | P1-F1 … P1-F5 | Mostly a chain: F1 → F2 → F3 → F4 → F5, so expect 1–2 lanes. It's the biggest refactor. Behaviour stays identical except where a ticket says otherwise, and the existing 394 tests stay green. |
| p2 | P1-A1 … P1-A3, P2-A4 | Add the issue state history that §7's "came back" check needs. |
| p3b | P1-G1, G5, G2, G3, P2-G6, G7 | G1 lands first. Doggystyle (§4.E) is the golden case in tests. G2's Plex reconcile step waits for P1-C5, so build it behind a flag until then. |
| p3 | P1-B1 … P1-B6 | Needs G1. |
| p4 | P1-C1 … C4, C6, C7, P2-C8, then C5 | C1–C3 don't need the spike. Build C5 after the spike, from what it showed. |
| p6 | P2-D1 … P3-D12 | P2-D7 pins the `mdl-pot` image. That's a compose change, so stage it and tell me rather than deploying it. |
| p7 | P2-E1 … P3-E8 | UI. Check it in the harness and save screenshots. |
| p8 | P3-H1 … H3, P2-H4 | Fill the H4 test gaps first, then the dead code (H2), then the module split (H1) last, because it conflicts with everything. |
| p5 | P1-R1 … P1-R8 | **Tooling and dry runs only.** See stop point 3. |

You may merge p2 and p3b into one workflow if the plan shows their files don't overlap.

## Stop points: wait for me

1. **Phase 0 is integrated and green.** Tell me it's ready to deploy, with the merge and `docker compose up -d --build` steps and the §7 oscillation query to watch. Deploying is my call. Carry on with p1 while I decide.
2. **The two Plex-write spikes:**
   - P1-C5 merge, on Sia *Eye to Eye (The Remixes)*;
   - P1-C7 genre lock, on one album.

   Write the exact requests, show them to me and wait for my yes. Then run them yourself, record what happened in `docs/review/plex-merge-spike.md` and build the ticket on that. If I say no, build it behind a disabled flag.
3. **Phase 5 never runs live.**
   - Build each R step as a CLI command that is dry-run by default.
   - Run each dry run against the shared DB copy, with `--network none` and the library `:ro`.
   - Put the counts in the p5 report and stop. I'll decide on each live run.
4. **Needs-decision tickets.** Collect them, keep going with the others, and ask me all of them at the end of the phase. Use AskUserQuestion with a recommended option.
5. **A phase whose integration fails verification twice after fixes.** Stop and report what's failing, with the output.

## Definition of done (per ticket)

- The acceptance tests from the ticket exist. They failed before the fix and pass after it.
- The full backend suite is green in the sandbox, and the frontend builds if it was touched.
- The adversarial review found nothing it could confirm, or what it found has been fixed.
- Line refs were re-checked, and the doc's premise still held.
- The ledger row is updated with the commit hash and the test evidence.

At the very end, give me:
- every phase branch, with what each one changes;
- the deploy order;
- what's left in the ledger, as needs-decision or deferred;
- the p5 dry-run counts I need to approve.
