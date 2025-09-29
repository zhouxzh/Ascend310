#!/usr/bin/env sh 
set -e 
pnpm run docs:build 
cd dist 
git init 
git add -A 
git commit -m 'deploy' 
git push -f https://github.com/zhouxzh/FPGA-course.git master:gh-pages 
cd -

# 使用export-pdf命令时，或许需要手动设置chrome的路径变量，e.g. export PUPPETEER_EXECUTABLE_PATH="/Users/idsefa/.cache/puppeteer/chrome/mac_arm-140.0.7339.207/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"