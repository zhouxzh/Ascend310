import comp from "D:/Github/Ascend310/src/.vuepress/.temp/pages/book/chapter7.html.vue"
const data = JSON.parse("{\"path\":\"/book/chapter7.html\",\"title\":\"第七章\",\"lang\":\"zh-cn\",\"frontmatter\":{\"title\":\"第七章\",\"author\":[\"周贤中\"],\"date\":\"2025-03-30T00:00:00.000Z\",\"subject\":\"Markdown\",\"keywords\":[\"Ascend\"],\"lang\":\"zh-cn\",\"gitInclude\":[]},\"headers\":[],\"readingTime\":{\"minutes\":0.07,\"words\":20},\"filePathRelative\":\"book/chapter7.md\",\"localizedDate\":\"2025年3月30日\"}")
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
