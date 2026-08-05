#!/usr/bin/env bash

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DIST_DIR="$REPO_ROOT/src/.vuepress/dist"
readonly PAGES_REMOTE_URL="${PAGES_REMOTE_URL:-https://github.com/zhouxzh/Ascend310.git}"
readonly PAGES_DEPLOY_BRANCH="${PAGES_DEPLOY_BRANCH:-deploy}"

run_pnpm() {
    if command -v node >/dev/null 2>&1 && command -v pnpm >/dev/null 2>&1; then
        pnpm "$@"
        return
    fi

    if command -v cmd.exe >/dev/null 2>&1; then
        cmd.exe /d /c pnpm "$@"
        return
    fi

    printf 'error: pnpm is unavailable in this shell\n' >&2
    exit 1
}

run_deploy_git() {
    local worktree_dir=$1
    shift

    if command -v git.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1; then
        git.exe -C "$(wslpath -w "$worktree_dir")" "$@"
    else
        git -C "$worktree_dir" "$@"
    fi
}

cd "$REPO_ROOT"
run_pnpm run docs:build

mkdir -p "$REPO_ROOT/tmp"
PAGES_WORKTREE="$(mktemp -d "$REPO_ROOT/tmp/pages-deploy.XXXXXX")"
readonly PAGES_WORKTREE
trap 'rm -rf -- "$PAGES_WORKTREE"' EXIT

cp -a "$DIST_DIR/." "$PAGES_WORKTREE/"
rm -rf -- "$PAGES_WORKTREE/.git"
touch "$PAGES_WORKTREE/.nojekyll"

run_deploy_git "$PAGES_WORKTREE" init -b main
run_deploy_git "$PAGES_WORKTREE" add -A
run_deploy_git "$PAGES_WORKTREE" commit -m 'deploy'
run_deploy_git "$PAGES_WORKTREE" push --force "$PAGES_REMOTE_URL" "main:$PAGES_DEPLOY_BRANCH"

printf 'Deploy complete: https://zhouxzh.github.io/Ascend310/\n'
