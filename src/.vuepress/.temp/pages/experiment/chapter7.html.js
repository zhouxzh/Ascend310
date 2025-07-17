import comp from "D:/Github/Ascend310/src/.vuepress/.temp/pages/experiment/chapter7.html.vue"
const data = JSON.parse("{\"path\":\"/experiment/chapter7.html\",\"title\":\"实验7：PS端EMIO操作与PL端外设控制\",\"lang\":\"zh-CN\",\"frontmatter\":{\"gitInclude\":[]},\"headers\":[],\"readingTime\":{\"minutes\":2.58,\"words\":773},\"filePathRelative\":\"experiment/chapter7.md\"}")
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
