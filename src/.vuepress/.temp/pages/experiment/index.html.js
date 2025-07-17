import comp from "D:/Github/Ascend310/src/.vuepress/.temp/pages/experiment/index.html.vue"
const data = JSON.parse("{\"path\":\"/experiment/\",\"title\":\"FPGA系统设计实验教程\",\"lang\":\"zh-CN\",\"frontmatter\":{\"gitInclude\":[]},\"headers\":[],\"readingTime\":{\"minutes\":0.65,\"words\":195},\"filePathRelative\":\"experiment/README.md\"}")
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
