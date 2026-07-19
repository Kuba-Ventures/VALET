# Git history cleanup — runbook

**Status: EXECUTED 2026-07-19.** Kept as a record of what was done and as a
reference if this ever recurs. See "Actual results" below for measured outcomes
and two corrections to the original procedure.

## TL;DR

PRs were slow to push because `.git` history carried **~430 MB of old build
binaries**, not because the codebase is large — source is ~54k lines and healthy.
One commit — `41bd7c0` (PR #77) — accidentally committed the entire
`src-tauri/target/` build directory plus a `.dmg` and `.pkg`. Git keeps deleted
files in history forever, so they weighed down every clone, fetch, and CI checkout.

**Fix:** rewrite history to strip those files.

## Why not just refactor / write less code?

It would do **nothing** for push speed. The weight was old binaries in history, not
source lines. Consolidating `server.py` etc. is a separate, optional code-quality
task — worth doing on its own merits, but unrelated. Don't conflate them.

## Actual results (2026-07-19)

| | Before | After |
|---|---|---|
| Fresh clone `.git` | 445 MB | **9.7 MB** |
| HEAD tree hash | `3260d51…` | **`3260d51…`** (byte-identical) |
| Files at HEAD | 344 | 344 (identical set) |
| `main` commits | 351 | 351 |
| Artifact paths in history | thousands | **0** |
| Largest remaining blob | build binaries | `src-tauri/icons/icon.icns`, 1.4 MB |

Old `main` was `a26c1ff`; rewritten `main` is `365e62c`.

## Corrections to the original procedure

Two things the first draft of this runbook got wrong. Both would have bitten on a
literal rerun:

1. **Do NOT `git push --force --mirror`.** A mirror clone carries ~290
   `refs/pull/*` refs. GitHub refuses writes to `refs/pull/*`, and `--mirror` also
   *deletes* any remote ref not present locally. Push the branches and tags
   explicitly instead (step 6 below).

2. **`git for-each-ref 'refs/pull/*'` matches nothing.** The wildcard doesn't span
   path components — use the bare prefix `refs/pull` if you ever do need to
   enumerate or drop them.

## Preconditions

- [ ] **0 open PRs.** If any are open, merge or close them first — their branches
      diverge from the rewritten main and have to be recreated.
- [ ] Tell every collaborator: "stop pushing, a history rewrite is coming, you'll
      re-clone." Pick a low-activity window.
- [ ] You have **admin** on `Kuba-Ventures/VALET` (needed to temporarily relax
      branch protection).

## Procedure

### 1. Work from a FRESH mirror clone (never your working repo)

`git-filter-repo` refuses to run on a normal clone and removes the `origin` remote
as a safety measure. Use a throwaway mirror:

```bash
cd /tmp
git clone --mirror https://github.com/Kuba-Ventures/VALET.git valet-mirror.git
du -sh valet-mirror.git   # sanity: ~445 MB
```

### 2. Keep a backup (rollback path)

```bash
cp -R /tmp/valet-mirror.git /tmp/valet-mirror-BACKUP.git
```

### 3. Get git-filter-repo

```bash
brew install git-filter-repo   # or: pip install git-filter-repo
git filter-repo --version
```

### 4. Strip the build artifacts from ALL history

```bash
cd /tmp/valet-mirror.git
git filter-repo \
  --path src-tauri/target/ \
  --path src-tauri/binaries/ \
  --path build/ \
  --path-glob '*.dmg' \
  --path-glob '*.pkg' \
  --invert-paths --force
```

### 5. Verify before pushing anything

The decisive check is the **tree hash at HEAD**: if it matches the pre-rewrite
value, the working content is byte-identical and only history changed.

```bash
git reflog expire --expire=now --all && git gc --prune=now
du -sh .                                    # expect ~10 MB
git rev-parse main^{tree}                   # must equal the pre-rewrite tree hash
git rev-list --count main                   # must equal the pre-rewrite commit count
# No large blobs should remain (icons ~1.4 MB are fine):
git rev-list --objects --all \
  | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' \
  | awk '/^blob/ {print $3, $4}' | sort -rn | head
```

Compare old vs new file sets directly:

```bash
diff <(git -C /tmp/valet-mirror-BACKUP.git ls-tree -r --name-only main) \
     <(git -C /tmp/valet-mirror.git       ls-tree -r --name-only main)
```

### 6. Push the rewritten history

Save the current protection config first so you can restore it exactly:

```bash
gh api repos/Kuba-Ventures/VALET/branches/main/protection > /tmp/protection-restore.json
```

Temporarily set `allow_force_pushes: true` via the API (or GitHub → Settings →
Branches), then push branches and tags **explicitly** — not `--mirror`:

```bash
cd /tmp/valet-mirror.git
git push --force https://github.com/Kuba-Ventures/VALET.git \
  refs/heads/main:refs/heads/main \
  refs/heads/fix/inbox-summarize-from-rows:refs/heads/fix/inbox-summarize-from-rows \
  refs/heads/fix/sports-national-teams:refs/heads/fix/sports-national-teams \
  refs/tags/overnight-complete-2026-05-18:refs/tags/overnight-complete-2026-05-18 \
  refs/tags/pre-overnight-2026-05-17:refs/tags/pre-overnight-2026-05-17
```

Restore branch protection **immediately** afterward and read it back to confirm
`allow_force_pushes` is `false` again and both required checks
(`factory-tests`, `factory-review`) survived.

### 7. Migrate each local clone

Old clones still contain the old history and will fight the new one. You do *not*
have to delete the directory — an in-place reset preserves gitignored files you
almost certainly want to keep (`.env`, `data/*.db`, `cert.pem` / `key.pem`,
`node_modules/`).

First, find branches holding work that never made it to main. Note that this repo
**squash-merges**, so a three-dot `git diff` shows every branch as "unique" and is
useless here. `git cherry` is the correct tool — it detects equivalent patches
upstream:

```bash
OLD=a26c1ff   # pre-rewrite main
for b in $(git for-each-ref --format='%(refname:short)' refs/heads | grep -v '^main$'); do
  n=$(git cherry $OLD $b 2>/dev/null | grep -c '^+')
  [ "$n" != "0" ] && echo "$n  $b"
done | sort -rn
```

In the 2026-07-19 run this reduced ~70 local branches to **15** with genuinely
unmerged commits. Export those as patches before deleting anything:

```bash
git format-patch "$(git merge-base $OLD $b)..$b" -o /path/to/rescue/$(echo $b | tr '/' '_')
git stash show -p --binary 'stash@{0}' > /path/to/rescue/stash-0.patch
```

Then reset and reclaim the disk:

```bash
git fetch origin --prune --force
git checkout main && git reset --hard origin/main
git for-each-ref --format='%(refname:short)' refs/heads | grep -v '^main$' | xargs -n1 git branch -D
git stash clear
git reflog expire --expire=now --all && git gc --prune=now
du -sh .git   # expect ~10 MB
```

Reapply rescued work onto the new history with
`git am /path/to/rescue/<branch>/*.patch`.

## Notes

- **GitHub keeps the old objects server-side** until its own GC runs; new clones
  are already small. If the old binaries need provable purging (e.g. they
  contained anything sensitive), open a GitHub Support request after pushing.
- **CI needs no change.** `factory.yml` uses `fetch-depth: 0` because the reviewer
  does a three-dot `git diff origin/main...HEAD`; shallowing it would break that
  diff. At ~10 MB, `fetch-depth: 0` is both cheap and correct — the rewrite fixes
  CI checkout speed with zero workflow edits.
- **Prevention (already in place):** `.gitignore` excludes `src-tauri/target/`,
  `build/`, `*.dmg`, etc., so this can't recur. Keep it that way.

## Rollback

From `/tmp/valet-mirror-BACKUP.git`, re-enable force-push and push the same
explicit refspecs as step 6. Once collaborators have re-cloned the new history,
rollback means another coordinated force-push — so verify at step 5, where it's
free.
