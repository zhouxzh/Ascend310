#!/bin/bash

# 确保脚本抛出遇到的错误
set -e

pnpm run docs:build 
cd src/.vuepress/dist 
git init -b main
git add -A 
git commit -m 'deploy' 

# 将构建产物强制推送到远程 deploy 分支
git push -f https://github.com/zhouxzh/Ascend310.git main:deploy

echo "Deploy complete."

cd -
