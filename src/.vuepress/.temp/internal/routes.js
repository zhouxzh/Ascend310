export const redirects = JSON.parse("{}")

export const routes = Object.fromEntries([
  ["/", { loader: () => import(/* webpackChunkName: "index.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/index.html.js"), meta: {"t":"简介","i":"school"} }],
  ["/portfolio.html", { loader: () => import(/* webpackChunkName: "portfolio.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/portfolio.html.js"), meta: {"t":"作者简介","i":"house"} }],
  ["/book/", { loader: () => import(/* webpackChunkName: "book_index.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/book/index.html.js"), meta: {"t":"昇腾310B"} }],
  ["/book/chapter0.html", { loader: () => import(/* webpackChunkName: "book_chapter0.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/book/chapter0.html.js"), meta: {"t":"第0讲：前言"} }],
  ["/book/chapter1.html", { loader: () => import(/* webpackChunkName: "book_chapter1.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/book/chapter1.html.js"), meta: {"t":"第一章：初步使用开发板"} }],
  ["/book/chapter2.html", { loader: () => import(/* webpackChunkName: "book_chapter2.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/book/chapter2.html.js"), meta: {"t":"第二章"} }],
  ["/book/chapter3.html", { loader: () => import(/* webpackChunkName: "book_chapter3.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/book/chapter3.html.js"), meta: {"t":"第三章"} }],
  ["/book/chapter4.html", { loader: () => import(/* webpackChunkName: "book_chapter4.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/book/chapter4.html.js"), meta: {"t":"第四章"} }],
  ["/book/chapter5.html", { loader: () => import(/* webpackChunkName: "book_chapter5.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/book/chapter5.html.js"), meta: {"t":"第五章"} }],
  ["/book/chapter6.html", { loader: () => import(/* webpackChunkName: "book_chapter6.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/book/chapter6.html.js"), meta: {"t":"第六章"} }],
  ["/book/chapter7.html", { loader: () => import(/* webpackChunkName: "book_chapter7.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/book/chapter7.html.js"), meta: {"t":"第七章"} }],
  ["/book/chapter8.html", { loader: () => import(/* webpackChunkName: "book_chapter8.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/book/chapter8.html.js"), meta: {"t":"第八章"} }],
  ["/404.html", { loader: () => import(/* webpackChunkName: "404.html" */"/Users/hehaocheng/WebstormProjects/Ascend310/src/.vuepress/.temp/pages/404.html.js"), meta: {"t":""} }],
]);
