#!/bin/bash

# 确保脚本抛出遇到的错误
set -e

pnpm run docs:build 
cd src/.vuepress/dist 
git init 
git add -A 
git commit -m 'deploy' 

# 将当前分支(HEAD)强制推送到远程的 gh-pages 分支
git push -f https://github.com/zhouxzh/Ascend310.git master:deploy

echo "Deploy complete."

cd -