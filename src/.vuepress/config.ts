import { viteBundler } from '@vuepress/bundler-vite'
import { defineUserConfig } from "vuepress";
// import { hopeTheme } from "vuepress-theme-hope";
import markdownItAttrs from 'markdown-it-attrs';

import theme from "./theme.js";

export default defineUserConfig({
  base: "/",

  // Numbered presentation Markdown files are Marp sources. Their generated
  // HTML is copied into the public directory and must not be rebuilt as
  // ordinary VuePress pages at the same output path.
  pagePatterns: [
    "**/*.md",
    "!presentation/[0-9][0-9]-*.md",
    "!presentation/legacy-*.md",
    "!.vuepress",
    "!node_modules",
  ],

  head: [
    ["link", { rel: "icon", href: "/favicon.ico" }],
  ],

  lang: "zh-CN",
  title: "昇腾310B实战",
  description: "基于昇腾310B的边缘计算与人工智能推理部署开源教材",

  bundler: viteBundler({
    viteOptions: {
      server: {
        watch: {
          // Atomic writers (and another agent editing case9) create locked
          // .tmpdir/.tmp files under src/experiment. Watching them makes Vite
          // die with EBUSY; generated dist files are also not source.
          ignored: [
            "**/.*.tmpdir/**",
            "**/*.tmp",
            "**/.vuepress/dist/**",
          ],
        },
      },
    },
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

