import comp from "D:/Github/Ascend310/src/.vuepress/.temp/pages/portfolio.html.vue"
const data = JSON.parse("{\"path\":\"/portfolio.html\",\"title\":\"作者简介\",\"lang\":\"zh-CN\",\"frontmatter\":{\"home\":true,\"portfolio\":true,\"title\":\"作者简介\",\"icon\":\"house\",\"welcome\":\"👋 你好，我是\",\"name\":\"周贤中\",\"avatar\":\"/assets/image/Tom.png\",\"description\":\"一个业余的码农\",\"social\":true,\"email\":\"zhouxzh@gdut.edu.cn\",\"timeline\":false,\"article\":false,\"star\":false,\"articleInfo\":false,\"pageInfo\":false,\"titles\":[\"博士\"],\"footer\":false,\"gitInclude\":[]},\"headers\":[{\"level\":2,\"title\":\"介绍\",\"slug\":\"介绍\",\"link\":\"#介绍\",\"children\":[]}],\"readingTime\":{\"minutes\":0.4,\"words\":121},\"filePathRelative\":\"portfolio.md\"}")
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
