import { sidebar } from "vuepress-theme-hope";

export default sidebar({
  "/": [
    "",
    {
      text: "理论教程",
      icon: "book",
      link: "/book/",
      prefix: "/book/",
      collapsible: true,
      children: [
        "chapter1.md",
        "chapter2.md",
        "chapter3.md",
        "chapter4.md",
        "chapter5.md", 
        "chapter6.md",
        "chapter7.md",
        "chapter8.md",
        "chapter9.md"
      ]
    },
    {
      text: "实践案例",
      icon: "experiment",
      link: "/experiment/",
      prefix: "/experiment/",
      collapsible: true,
      children: [
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
      text: "附录",
      icon: "book",
      link: "/appendix/",
      prefix: "/appendix/",
      collapsible: true,
      children: [
        "appendix1.md",
        "appendix2.md",
        "appendix3.md",
        "appendix4.md",
        "appendix5.md",
        "appendix6.md"
      ]
    },
    {
      text: "教材配套演示",
      icon: "presentation",
      link: "/presentation/",
      prefix: "/presentation/",
      collapsible: true,
      children: [
        {
          text: "全书导览（Marp 放映）",
          link: "/presentation/00-repository-map.html",
        },
        {
          text: "附录 1 · 开发板与基础环境",
          link: "/presentation/01-hardware-basics.html",
        },
        {
          text: "附录 2 · 基于昇腾 310B 的 Ubuntu 教程",
          link: "/presentation/02-ubuntu-basics.html",
        },
        {
          text: "附录 3 · Python 编程基础",
          link: "/presentation/03-python-basics.html",
        },
        {
          text: "附录 4 · Vibe Coding 基础",
          link: "/presentation/04-vibe-coding.html",
        },
        {
          text: "附录 5 · ROS2 基础教程",
          link: "/presentation/09-ros-basics.html",
        },
        {
          text: "案例 1：智能考勤机",
          link: "/presentation/05-face-recognition.html",
        },
        {
          text: "案例 2：目标跟踪检测",
          link: "/presentation/06-object-tracking.html",
        },
        {
          text: "案例 3：智能电子琴",
          link: "/presentation/07-smart-piano.html",
        },
        {
          text: "案例 9：智能聊天机器人",
          link: "/presentation/08-chatbot.html",
        },
      ]
    },
  ],
});
