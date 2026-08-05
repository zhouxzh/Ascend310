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
    local deploy_dir=$1
    shift

    if command -v git.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1; then
        git.exe -C "$(wslpath -w "$deploy_dir")" "$@"
    else
        git -C "$deploy_dir" "$@"
    fi
}

cd "$REPO_ROOT"
run_pnpm run docs:build

mkdir -p "$REPO_ROOT/tmp"
deploy_dir="$(mktemp -d "$REPO_ROOT/tmp/pages-deploy.XXXXXX")"
readonly deploy_dir
trap 'rm -rf -- "$deploy_dir"' EXIT

cp -a "$DIST_DIR/." "$deploy_dir/"
rm -rf -- "$deploy_dir/.git"
touch "$deploy_dir/.nojekyll"

run_deploy_git "$deploy_dir" init -b main
run_deploy_git "$deploy_dir" add -A
run_deploy_git "$deploy_dir" commit -m 'deploy'
run_deploy_git "$deploy_dir" push --force "$PAGES_REMOTE_URL" "main:$PAGES_DEPLOY_BRANCH"

printf 'Deploy complete: https://zhouxzh.github.io/Ascend310/\n'
