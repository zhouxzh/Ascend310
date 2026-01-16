import { viteBundler } from '@vuepress/bundler-vite'
import { defineUserConfig } from "vuepress";
// import { hopeTheme } from "vuepress-theme-hope";
import markdownItAttrs from 'markdown-it-attrs';

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

  theme,

  // 和 PWA 一起启用
  // shouldPrefetch: false,
  plugins: [
    //markdownImagePlugin({
      // size: true,
      // obsidianSize: true,
    // }),
  ],
  extendsMarkdown: (md) => {
    md.use(markdownItAttrs)
  },
});

