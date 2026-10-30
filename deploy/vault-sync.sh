#!/usr/bin/env bash
# Two-way sync of the agents' knowledge vault with a private GitHub repo, so it
# can be opened and edited in Obsidian. Runs hourly from cron (installed by the
# deploy workflow when LRMAGENT_VAULT_REPO is set).
#
# Synced folders (paths inside the agent workspace volume, same paths in the repo):
#   knowledge/                 the shared vault
#   agents/<id>/knowledge/     agents that keep their own vault
# Only folders that exist on the server are replaced from the agents' side;
# everything else in the repo (.obsidian/, notes at the top, other folders) is
# left exactly as Obsidian wrote it.
#
# Two markers keep the history straight:
#   refs/lrm/agents  the commit whose synced folders equal what the agents have
#   refs/lrm/pushed  the last commit this script pushed
# Agents' changes = agents-ref -> live volume; Obsidian's changes = pushed-ref ->
# origin/main. A note changed on both sides keeps the Obsidian version and gets
# the agent's version beside it as "<note> (agent copy <date>).md".
set -Eeuo pipefail

REPO="${1:?usage: vault-sync.sh owner/repo}"
URL="${VAULT_REMOTE_URL:-git@github.com:$REPO.git}"
VOLUME="${VAULT_VOLUME:-cowagent_workspace}"
BASE="$HOME/vault-sync"
WORK="$BASE/repo"
STATUS="$BASE/status"
KEY="$HOME/.ssh/lrmagent_vault"
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$BASE/known_hosts"

log() { echo "$(date -u +%FT%TZ) $*"; }
# Status lines read by healthcheck.sh: OK / RETRY <n in a row> / CONFLICT / FAIL.
finish() {
  local kind="$1" message="$2" retries=0
  if [[ "$kind" == RETRY ]]; then retries=$(( $(cat "$BASE/retries" 2>/dev/null || echo 0) + 1 )); fi
  echo "$retries" > "$BASE/retries"
  if [[ "$kind" == RETRY ]]; then message="$message (run $retries in a row)"; fi
  echo "$kind $(date -u +%FT%TZ) $message" > "$STATUS"
  log "$kind $message"
  exit 0
}
trap 'finish FAIL "unexpected error at line $LINENO (see ~/vault-sync/sync.log)"' ERR

# Tree fingerprint of the synced folders, to tell whether anything changed.
fingerprint() { (cd "$1" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum) | sha256sum | cut -d' ' -f1; }

export_live() {
  sudo -n docker run --rm -v "$VOLUME":/v:ro alpine:3.20 sh -c '
    cd /v; set --
    for d in knowledge agents/*/knowledge; do [ -d "$d" ] && [ ! -L "$d" ] && set -- "$@" "$d"; done
    [ $# -gt 0 ] && tar -cf - "$@" || tar -cf - -T /dev/null' | tar -x -C "$1"
}

# Synced folders present in a tree (one per line).
roots() { (cd "$1" && for d in knowledge agents/*/knowledge; do [[ -d "$d" ]] && echo "$d"; done; true); }

import_tree() {
  (cd "$1" && tar -cf - $(roots "$1")) | sudo -n docker run -i --rm -v "$VOLUME":/v alpine:3.20 sh -c '
    set -e; tmp=$(mktemp -d); tar -x -C "$tmp"; cd /v; owner=$(stat -c %u:%g /v)
    for d in $(cd "$tmp" && ls -d knowledge agents/*/knowledge 2>/dev/null); do
      [ -d "$tmp/$d" ] || continue
      if [ -L "$d" ]; then continue; fi           # agent uses the shared vault
      agent_dir=${d%/knowledge}
      if [ ! -d "$d" ]; then
        # A folder first created in Obsidian: only for agents that exist.
        [ "$d" = knowledge ] || [ -d "$agent_dir" ] || continue
        mkdir -p "$d"
      fi
      find "$d" -mindepth 1 -delete
      cp -a "$tmp/$d/." "$d/"
      chown -R "$owner" "$d"
    done'
}

mkdir -p "$BASE"
exec 9>"$BASE/lock"
flock -n 9 || { log "another sync is running; skipping"; exit 0; }
[[ -f "$KEY" || -n "${VAULT_REMOTE_URL:-}" ]] || finish FAIL "no deploy key at $KEY"

if [[ ! -d "$WORK/.git" ]]; then
  git clone -q "$URL" "$WORK" 2>/dev/null \
    || finish FAIL "cannot reach $REPO (create the private repo and add the deploy key with write access)"
fi
cd "$WORK"
git config user.name "LRMagent vault sync"
git config user.email "vault-sync@lrmagent.invalid"
# A run killed halfway can leave a rebase or a dirty tree behind.
git rebase --abort >/dev/null 2>&1 || true
git reset -q --hard >/dev/null 2>&1 || true
git clean -qfdx
git fetch -q origin || finish FAIL "fetch from $REPO failed"
remote="$(git rev-parse -q --verify origin/main || true)"
agents_ref="$(git rev-parse -q --verify refs/lrm/agents || true)"
pushed_ref="$(git rev-parse -q --verify refs/lrm/pushed || true)"
empty_tree="$(git hash-object -t tree /dev/null)"

live="$(mktemp -d)"; merged="$(mktemp -d)"; again="$(mktemp -d)"
trap 'rm -rf "$live" "$merged" "$again"' EXIT
export_live "$live"
live_print="$(fingerprint "$live")"

# The agents' side: replace only the synced folders the server has.
if [[ -n "$agents_ref" ]]; then
  git checkout -qf --detach "$agents_ref"
else
  git checkout -qf --orphan "sync-$$"
  git rm -rqf --cached --ignore-unmatch . >/dev/null
  git clean -qfdx
fi
while IFS= read -r root; do
  git rm -rqf --cached --ignore-unmatch -- "$root" >/dev/null
  rm -rf "$root"
  mkdir -p "$(dirname "$root")"
  cp -a "$live/$root" "$root"
done < <(roots "$live")
live_roots="$(roots "$live")"
[[ -z "$live_roots" ]] || git add -A -- $live_roots
git diff --cached --quiet || git commit -qm "Agents' changes, $(date -u '+%Y-%m-%d %H:%M') UTC"
agents_commit="$(git rev-parse -q --verify HEAD || true)"

# Same note changed on both sides: keep Obsidian's, save the agent's beside it.
copies=0
if [[ -n "$remote" && -n "$agents_commit" ]]; then
  agents_base="${agents_ref:-$empty_tree}"
  obsidian_base="${pushed_ref:-${agents_ref:-$empty_tree}}"
  while IFS= read -r -d '' f; do
    [[ -n "$f" ]] || continue
    if git cat-file -e "HEAD:$f" 2>/dev/null && git cat-file -e "origin/main:$f" 2>/dev/null \
       && [[ "$(git rev-parse "HEAD:$f")" != "$(git rev-parse "origin/main:$f")" ]]; then
      if [[ "$f" == *.* ]]; then copy="${f%.*} (agent copy $(date -u +%F)).${f##*.}"; else copy="$f (agent copy $(date -u +%F))"; fi
      git show "HEAD:$f" > "$copy"
      if git cat-file -e "$agents_base:$f" 2>/dev/null; then git show "$agents_base:$f" > "$f"; else rm -f "$f"; fi
      git add -A -- "$f" "$copy"
      copies=$((copies + 1))
    fi
  done < <(comm -z -12 <(git diff -z --name-only "$agents_base" HEAD | sort -z) \
                       <(git diff -z --name-only "$obsidian_base" origin/main | sort -z))
  if (( copies )); then
    if [[ -n "$agents_ref" && "$agents_commit" != "$agents_ref" ]]; then git commit -q --amend --no-edit; else git commit -qm "Agent copies of notes also edited in Obsidian"; fi
  fi
  agents_commit="$(git rev-parse HEAD)"
fi

# Combine with what was edited in Obsidian (the repo).
if [[ -n "$remote" && -n "$agents_commit" ]]; then
  if ! git rebase -q origin/main >/dev/null 2>&1; then
    git rebase --abort || true
    finish CONFLICT "the same lines of a note were changed in Obsidian and by an agent; fix it in $REPO, the next run retries"
  fi
elif [[ -n "$remote" ]]; then
  git checkout -qf --detach origin/main
fi
git rev-parse -q --verify HEAD >/dev/null || finish OK "nothing to sync yet"
merged_commit="$(git rev-parse HEAD)"
if [[ "$merged_commit" != "$remote" ]]; then
  git push -q origin HEAD:main || finish FAIL "push to $REPO failed"
fi
git update-ref refs/lrm/pushed "$merged_commit"

# Bring Obsidian edits back to the agents, unless they wrote meanwhile.
merged_roots="$(roots .)"
[[ -z "$merged_roots" ]] || git archive HEAD $merged_roots | tar -x -C "$merged"
if [[ "$(fingerprint "$merged")" != "$live_print" ]]; then
  export_live "$again"
  if [[ "$(fingerprint "$again")" != "$live_print" ]]; then
    # The volume still equals the agents' commit, so that stays the base.
    [[ -n "$agents_commit" ]] && git update-ref refs/lrm/agents "$agents_commit"
    finish RETRY "agents were writing during the sync; their edits and yours are pushed, the next run brings yours back"
  fi
  import_tree "$merged"
fi
git update-ref refs/lrm/agents "$merged_commit"
finish OK "synced $(git rev-parse --short HEAD)$( (( copies )) && echo "; kept $copies agent copies of notes also edited in Obsidian")"
