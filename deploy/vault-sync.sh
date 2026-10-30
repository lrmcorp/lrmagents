#!/usr/bin/env bash
# Two-way sync of the agents' knowledge vault with a private GitHub repo, so it
# can be opened and edited in Obsidian. Runs hourly from cron (installed by the
# deploy workflow when LRMAGENT_VAULT_REPO is set).
#
# Synced folders (paths inside the agent workspace volume, same paths in the repo):
#   knowledge/                 the shared vault
#   agents/<id>/knowledge/     agents that keep their own vault
#
# Git does the merging: the agents' changes since the last sync are committed on
# top of the last synced commit and rebased onto the repo. If the same note was
# changed on both sides, nothing is overwritten; the run stops and records a
# CONFLICT that the health check reports.
set -euo pipefail

REPO="${1:?usage: vault-sync.sh owner/repo}"
URL="${VAULT_REMOTE_URL:-git@github.com:$REPO.git}"
VOLUME="${VAULT_VOLUME:-cowagent_workspace}"
BASE="$HOME/vault-sync"
WORK="$BASE/repo"
STATUS="$BASE/status"
KEY="$HOME/.ssh/lrmagent_vault"
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$BASE/known_hosts"

log() { echo "$(date -u +%FT%TZ) $*"; }
finish() { echo "$1 $(date -u +%FT%TZ) $2" > "$STATUS"; log "$1 $2"; exit 0; }

# Tree fingerprint, to tell whether anything changed.
fingerprint() { (cd "$1" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum) | sha256sum | cut -d' ' -f1; }

export_live() {
  sudo -n docker run --rm -v "$VOLUME":/v:ro alpine:3.20 sh -c '
    cd /v; set --
    for d in knowledge agents/*/knowledge; do [ -d "$d" ] && [ ! -L "$d" ] && set -- "$@" "$d"; done
    [ $# -gt 0 ] && tar -cf - "$@" || tar -cf - -T /dev/null' | tar -x -C "$1"
}

import_tree() {
  (cd "$1" && tar -cf - .) | sudo -n docker run -i --rm -v "$VOLUME":/v alpine:3.20 sh -c '
    set -e; tmp=$(mktemp -d); tar -x -C "$tmp"; cd /v; owner=$(stat -c %u:%g /v)
    for d in knowledge agents/*/knowledge; do
      [ -d "$d" ] && [ ! -L "$d" ] || continue
      [ -d "$tmp/$d" ] || continue   # never wipe a whole vault because the repo lacks it
      find "$d" -mindepth 1 -delete
      cp -a "$tmp/$d/." "$d/"
      chown -R "$owner" "$d"
    done'
}

mkdir -p "$BASE"
[[ -f "$KEY" || -n "${VAULT_REMOTE_URL:-}" ]] || finish FAIL "no deploy key at $KEY"

if [[ ! -d "$WORK/.git" ]]; then
  git clone -q "$URL" "$WORK" 2>/dev/null \
    || finish FAIL "cannot reach git@github.com:$REPO (create the private repo and add the deploy key with write access)"
fi
cd "$WORK"
git config user.name "LRMagent vault sync"
git config user.email "vault-sync@lrmagent.invalid"
git fetch -q origin || finish FAIL "fetch failed"
remote="$(git rev-parse -q --verify origin/main || true)"
last="$(cat "$BASE/last" 2>/dev/null || true)"
[[ -n "$last" ]] && ! git cat-file -e "$last^{commit}" 2>/dev/null && last=""

live="$(mktemp -d)"; merged="$(mktemp -d)"; again="$(mktemp -d)"
trap 'rm -rf "$live" "$merged" "$again"' EXIT
export_live "$live"
live_print="$(fingerprint "$live")"

# The agents' side: the live vault as a commit on top of the last synced state.
if [[ -n "$last" ]]; then git checkout -q --detach "$last"; else git checkout -q --orphan "sync-$$"; fi
git rm -rq --cached --ignore-unmatch . >/dev/null
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$live"/. .
git add -A
git diff --cached --quiet || git commit -qm "Agents' changes, $(date -u '+%Y-%m-%d %H:%M') UTC"

# A note changed on both sides: the Obsidian version wins, the agent's version is
# kept beside it as "<note> (agent copy <date>).md", so nothing is lost and the
# sync never stalls on it.
copies=0
if [[ -n "$remote" ]] && git rev-parse -q --verify HEAD >/dev/null; then
  base="${last:-$(git hash-object -t tree /dev/null)}"
  while IFS= read -r -d '' f; do
    [[ -n "$f" ]] || continue
    if git cat-file -e "HEAD:$f" 2>/dev/null && git cat-file -e "origin/main:$f" 2>/dev/null \
       && [[ "$(git rev-parse "HEAD:$f")" != "$(git rev-parse "origin/main:$f")" ]]; then
      copy="${f%.*} (agent copy $(date -u +%F))${f##*.}"
      [[ "$f" == *.* ]] && copy="${f%.*} (agent copy $(date -u +%F)).${f##*.}"
      git show "HEAD:$f" > "$copy"
      if git cat-file -e "$base:$f" 2>/dev/null; then git show "$base:$f" > "$f"; else rm -f "$f"; fi
      git add -A -- "$f" "$copy"
      copies=$((copies + 1))
    fi
  done < <(comm -z -12 <(git diff -z --name-only "$base" HEAD | sort -z) <(git diff -z --name-only "$base" origin/main | sort -z))
  if (( copies )); then git commit -q --amend --no-edit 2>/dev/null || git commit -qm "Agent copies of notes also edited in Obsidian"; fi
fi

# Combine with what was edited in Obsidian (the repo).
if [[ -n "$remote" ]] && git rev-parse -q --verify HEAD >/dev/null; then
  if ! git rebase -q origin/main >/dev/null 2>&1; then
    git rebase --abort || true
    finish CONFLICT "the same note was changed in Obsidian and by an agent; fix it in $REPO, the next run retries"
  fi
elif [[ -n "$remote" ]]; then
  git checkout -q --detach origin/main
fi
if git rev-parse -q --verify HEAD >/dev/null; then
  [[ "$(git rev-parse HEAD)" == "$remote" ]] || git push -q origin HEAD:main || finish FAIL "push failed"
fi

# Bring Obsidian edits back to the agents, unless they wrote meanwhile.
if git rev-parse -q --verify HEAD >/dev/null; then
  git archive HEAD | tar -x -C "$merged"
  if [[ "$(fingerprint "$merged")" != "$live_print" ]]; then
    export_live "$again"
    [[ "$(fingerprint "$again")" == "$live_print" ]] || finish RETRY "agents were writing during the sync; retrying next run"
    import_tree "$merged"
  fi
  git rev-parse HEAD > "$BASE/last"
fi
finish OK "synced $(git rev-parse --short HEAD 2>/dev/null || echo empty)$( (( copies )) && echo "; kept $copies agent copies of notes also edited in Obsidian")"
