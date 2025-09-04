import { sidebar } from "vuepress-theme-hope";

export default sidebar({
  "/": [
    "",
    {
      text: "理论教程",
      icon: "book",
      prefix: "/book/",
      collapsible: true,
      children: [
        "README.md",
        "chapter1.md",
        "chapter2.md",
        "chapter3.md",
        "chapter4.md",
        "chapter5.md", 
        "chapter6.md",
        "chapter7.md",
        "chapter8.md",
        "chapter9.md",
        "chapter10.md"
      ]
    },
    {
      text: "实践案例",
      icon: "experiment",
      prefix: "/experiment/",
      collapsible: true,
      children: [
        "README.md",
        "case1.md",
        "case2.md",
        "case3.md",
        "case4.md",
        "case5.md",
        "case6.md",
        "case7.md",
        "case8.md",
        "case9.md"
      ]
    },
    {
      text: "幻灯片",
      icon: "person-chalkboard",
      link: "https://ecosystem.vuejs.press/zh/plugins/markdown/revealjs/demo.html",
    },
  ],
});
