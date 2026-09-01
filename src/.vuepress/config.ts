import { viteBundler } from '@vuepress/bundler-vite'
import { defineUserConfig } from "vuepress";
// import { hopeTheme } from "vuepress-theme-hope";
import markdownItAttrs from 'markdown-it-attrs';

import theme from "./theme.js";

export default defineUserConfig({
  base: "/Ascend310/",

  // Numbered presentation Markdown files are Marp sources. Their generated
  // HTML is copied into the public directory and must not be rebuilt as
  // ordinary VuePress pages at the same output path.
  pagePatterns: [
    "**/*.md",
    "!presentation/[0-9][0-9]-*.md",
    "!.vuepress",
    "!node_modules",
  ],

  head: [
    ["link", { rel: "icon", href: "/Ascend310/favicon.ico" }],
  ],

  lang: "zh-CN",
  title: "昇腾310B实战",
  description: "基于昇腾310B的边缘计算与人工智能推理部署开源教材",

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

