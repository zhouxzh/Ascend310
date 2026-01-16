#!/bin/bash

pnpm run docs:build 
cd dist 
git init 
git add -A 
git commit -m 'deploy' 
# git branch -m master
git push -f https://github.com/zhouxzh/FPGA-course.git master:gh-pages 

echo "Deploy complete."