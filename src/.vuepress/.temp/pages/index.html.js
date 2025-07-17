import comp from "D:/Github/Ascend310/src/.vuepress/.temp/pages/index.html.vue"
const data = JSON.parse("{\"path\":\"/\",\"title\":\"简介\",\"lang\":\"zh-CN\",\"frontmatter\":{\"home\":true,\"icon\":\"school\",\"title\":\"简介\",\"heroImage\":\"/logo.png\",\"bgImageStyle\":{\"background-attachment\":\"fixed\"},\"heroText\":\"昇腾310B——理论与实践\",\"tagline\":\"前沿技术课程\",\"actions\":[{\"text\":\"作者简介\",\"icon\":\"users-line\",\"link\":\"./portfolio.md\",\"type\":\"primary\"},{\"text\":\"理论教程\",\"icon\":\"list-check\",\"link\":\"./book/README.md\"}],\"highlights\":[{\"header\":\"课程特色\",\"highlights\":[{\"title\":\"理论与实践结合\",\"icon\":\"balance-scale\",\"details\":\"32课时\"},{\"title\":\"最新开源开发工具\",\"icon\":\"toolbox\",\"details\":null},{\"title\":\"项目驱动教学\",\"icon\":\"project-diagram\",\"details\":\"从简单应用到复杂系统设计\"}]}],\"footer\":\"周贤中 版权所有 © 2025 | 电子邮箱：zhouxzh@gdut.edu.cn\",\"gitInclude\":[]},\"headers\":[],\"readingTime\":{\"minutes\":0.42,\"words\":126},\"filePathRelative\":\"README.md\"}")
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
