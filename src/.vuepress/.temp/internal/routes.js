export const redirects = JSON.parse("{}")

export const routes = Object.fromEntries([
  ["/portfolio.html", { loader: () => import(/* webpackChunkName: "portfolio.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/portfolio.html.js"), meta: {"t":"作者简介","i":"house"} }],
  ["/", { loader: () => import(/* webpackChunkName: "index.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/index.html.js"), meta: {"t":"简介","i":"school"} }],
  ["/book/chapter0.html", { loader: () => import(/* webpackChunkName: "book_chapter0.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/book/chapter0.html.js"), meta: {"t":"第0讲：前言"} }],
  ["/book/chapter1.html", { loader: () => import(/* webpackChunkName: "book_chapter1.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/book/chapter1.html.js"), meta: {"t":"第1讲：初步使用开发板"} }],
  ["/book/chapter2.html", { loader: () => import(/* webpackChunkName: "book_chapter2.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/book/chapter2.html.js"), meta: {"t":"第2讲：GPIO测试"} }],
  ["/book/chapter3.html", { loader: () => import(/* webpackChunkName: "book_chapter3.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/book/chapter3.html.js"), meta: {"t":"第3讲：NPU简介"} }],
  ["/book/chapter4.html", { loader: () => import(/* webpackChunkName: "book_chapter4.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/book/chapter4.html.js"), meta: {"t":"第4讲：体验NPU-AI应用"} }],
  ["/book/chapter5.html", { loader: () => import(/* webpackChunkName: "book_chapter5.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/book/chapter5.html.js"), meta: {"t":"第5讲：定制并编译Linux镜像"} }],
  ["/book/chapter6.html", { loader: () => import(/* webpackChunkName: "book_chapter6.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/book/chapter6.html.js"), meta: {"t":"第6讲：高级应用"} }],
  ["/book/chapter7.html", { loader: () => import(/* webpackChunkName: "book_chapter7.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/book/chapter7.html.js"), meta: {"t":"第七章"} }],
  ["/book/chapter8.html", { loader: () => import(/* webpackChunkName: "book_chapter8.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/book/chapter8.html.js"), meta: {"t":"第八章"} }],
  ["/book/", { loader: () => import(/* webpackChunkName: "book_index.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/book/index.html.js"), meta: {"t":"昇腾310B"} }],
  ["/404.html", { loader: () => import(/* webpackChunkName: "404.html" */"C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/src/.vuepress/.temp/pages/404.html.js"), meta: {"t":""} }],
]);
