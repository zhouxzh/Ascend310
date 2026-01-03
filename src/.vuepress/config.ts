import { viteBundler } from '@vuepress/bundler-vite'
import { defineUserConfig } from "vuepress";
import { hopeTheme } from "vuepress-theme-hope";
import { markdownImagePlugin } from '@vuepress/plugin-markdown-image'

import theme from "./theme.js";

export default defineUserConfig({
  base: "/Ascend310/",

  lang: "zh-CN",
  title: "主页",
  description: "vuepress-theme-hope 的文档演示",

  bundler: viteBundler({
    viteOptions: {},
    vuePluginOptions: {},
  }),

  theme: hopeTheme({
    // 添加这行即可启用数学公式（默认使用 MathJax）
    plugins: {
      mdEnhance: {
        mathjax: true,
      },
    },
    
    markdown: {
      mermaid: true,
    },
  }),
  // 和 PWA 一起启用
  // shouldPrefetch: false,
  plugins: [
    //markdownImagePlugin({
      // size: true,
      // obsidianSize: true,
    // }),
  ],
});

