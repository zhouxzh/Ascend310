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
        "chapter9.md"
      ]
    },
    {
      text: "实践案例",
      icon: "experiment",
      prefix: "/experiment/",
      collapsible: true,
      children: [
        "README.md",
        { text: "案例 1 · 人脸考勤（face-attendance）", link: "case1.md" },
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
      prefix: "/appendix/",
      collapsible: true,
      children: [
        "README.md",
        "appendix1.md",
        "appendix2.md",
        "appendix3.md",
        "appendix4.md",
        "appendix5.md"
      ]
    },
    {
      text: "教材配套演示",
      icon: "presentation",
      prefix: "/presentation/",
      collapsible: true,
      children: [
        "README.md",
        {
          text: "全书导览（Marp 放映）",
          link: "/presentation/00-repository-map.html",
        },
        {
          text: "附录 1 · 开发板与基础环境",
          link: "/presentation/01-hardware-basics.html",
        },
        {
          text: "附录 2 · 昇腾 310B Linux 操作与命令教程",
          link: "/presentation/02-linux-commands.html",
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
          text: "案例 1 · 边缘人脸考勤",
          link: "/presentation/05-face-recognition.html",
        },
        {
          text: "案例 2 · 目标跟踪检测",
          link: "/presentation/06-object-tracking.html",
        },
        {
          text: "案例 3 · Ascend 310B DDSP 智能电子琴",
          link: "/presentation/07-smart-piano.html",
        },
        {
          text: "案例 9 · 在昇腾 310B 上复现中文文本聊天",
          link: "/presentation/08-chatbot.html",
        },
      ]
    },
  ],
});
