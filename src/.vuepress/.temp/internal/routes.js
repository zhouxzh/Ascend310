export const redirects = JSON.parse("{}")

export const routes = Object.fromEntries([
  ["/portfolio.html", { loader: () => import(/* webpackChunkName: "portfolio.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/portfolio.html.js"), meta: {"t":"作者简介","i":"house"} }],
  ["/", { loader: () => import(/* webpackChunkName: "index.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/index.html.js"), meta: {"t":"简介","i":"school"} }],
  ["/book/chapter0.html", { loader: () => import(/* webpackChunkName: "book_chapter0.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/book/chapter0.html.js"), meta: {"t":"第0讲：前言"} }],
  ["/book/chapter1.html", { loader: () => import(/* webpackChunkName: "book_chapter1.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/book/chapter1.html.js"), meta: {"t":"第一章"} }],
  ["/book/chapter2.html", { loader: () => import(/* webpackChunkName: "book_chapter2.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/book/chapter2.html.js"), meta: {"t":"第二章"} }],
  ["/book/chapter3.html", { loader: () => import(/* webpackChunkName: "book_chapter3.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/book/chapter3.html.js"), meta: {"t":"第三章"} }],
  ["/book/chapter4.html", { loader: () => import(/* webpackChunkName: "book_chapter4.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/book/chapter4.html.js"), meta: {"t":"第四章"} }],
  ["/book/chapter5.html", { loader: () => import(/* webpackChunkName: "book_chapter5.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/book/chapter5.html.js"), meta: {"t":"第五章"} }],
  ["/book/chapter6.html", { loader: () => import(/* webpackChunkName: "book_chapter6.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/book/chapter6.html.js"), meta: {"t":"第六章"} }],
  ["/book/chapter7.html", { loader: () => import(/* webpackChunkName: "book_chapter7.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/book/chapter7.html.js"), meta: {"t":"第七章"} }],
  ["/book/chapter8.html", { loader: () => import(/* webpackChunkName: "book_chapter8.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/book/chapter8.html.js"), meta: {"t":"第八章"} }],
  ["/book/", { loader: () => import(/* webpackChunkName: "book_index.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/book/index.html.js"), meta: {"t":"昇腾310B"} }],
  ["/404.html", { loader: () => import(/* webpackChunkName: "404.html" */"D:/Github/Ascend310/src/.vuepress/.temp/pages/404.html.js"), meta: {"t":""} }],
]);

if (import.meta.webpackHot) {
  import.meta.webpackHot.accept()
  if (__VUE_HMR_RUNTIME__.updateRoutes) {
    __VUE_HMR_RUNTIME__.updateRoutes(routes)
  }
  if (__VUE_HMR_RUNTIME__.updateRedirects) {
    __VUE_HMR_RUNTIME__.updateRedirects(redirects)
  }
}

if (import.meta.hot) {
  import.meta.hot.accept(({ routes, redirects }) => {
    __VUE_HMR_RUNTIME__.updateRoutes(routes)
    __VUE_HMR_RUNTIME__.updateRedirects(redirects)
  })
}
