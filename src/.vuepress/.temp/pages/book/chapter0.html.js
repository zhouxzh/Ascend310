import comp from "D:/Github/Ascend310/src/.vuepress/.temp/pages/book/chapter0.html.vue"
const data = JSON.parse("{\"path\":\"/book/chapter0.html\",\"title\":\"第0讲：前言\",\"lang\":\"zh-cn\",\"frontmatter\":{\"title\":\"第0讲：前言\",\"author\":[\"周贤中\"],\"date\":\"2025-07-17T00:00:00.000Z\",\"subject\":\"Markdown\",\"keywords\":[\"Ascend\"],\"lang\":\"zh-cn\",\"gitInclude\":[]},\"headers\":[{\"level\":2,\"title\":\"昇腾310B介绍\",\"slug\":\"昇腾310b介绍\",\"link\":\"#昇腾310b介绍\",\"children\":[]}],\"readingTime\":{\"minutes\":0.09,\"words\":27},\"filePathRelative\":\"book/chapter0.md\",\"localizedDate\":\"2025年7月17日\"}")
export { comp, data }

if (import.meta.webpackHot) {
  import.meta.webpackHot.accept()
  if (__VUE_HMR_RUNTIME__.updatePageData) {
    __VUE_HMR_RUNTIME__.updatePageData(data)
  }
}

if (import.meta.hot) {
  import.meta.hot.accept(({ data }) => {
    __VUE_HMR_RUNTIME__.updatePageData(data)
  })
}
