import { navbar } from "vuepress-theme-hope";

export default navbar([
  "/",
  {
    text: "理论教程",
    link: "/book/",
    icon: "book",
    activeMatch: "^/book/"
  },
  {
    text: "实践案例",
    link: "/experiment/",
    icon: "experiment",
    activeMatch: "^/experiment/"
  },
  {
    text: "附录",
    link: "/appendix/",
    icon: "book",
    activeMatch: "^/appendix/"
  },
  {
    text: "教学演示",
    link: "/presentation/",
    icon: "presentation",
    activeMatch: "^/presentation/"
  },
  "portfolio.md",
]);
