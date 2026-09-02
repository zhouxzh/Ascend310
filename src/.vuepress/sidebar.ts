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
      text: "教学演示",
      icon: "presentation",
      prefix: "/presentation/",
      collapsible: true,
      children: [
        "README.md",
        {
          text: "00 · 仓库内容地图（Marp 放映）",
          link: "/presentation/00-repository-map.html",
        },
        {
          text: "第1周：昇腾310B硬件基础",
          link: "/presentation/01-hardware-basics.html",
        },
        {
          text: "第2周：Linux命令基础",
          link: "/presentation/02-linux-commands.html",
        },
        {
          text: "第3周：Python编程基础",
          link: "/presentation/03-python-basics.html",
        },
        {
          text: "第4周：Vibe Coding基础",
          link: "/presentation/04-vibe-coding.html",
        },
        {
          text: "第5周：人脸识别",
          link: "/presentation/05-face-recognition.html",
        },
        {
          text: "第6周：目标跟踪",
          link: "/presentation/06-object-tracking.html",
        },
        {
          text: "第7周：智能电子琴",
          link: "/presentation/07-smart-piano.html",
        },
        {
          text: "第8周：聊天机器人",
          link: "/presentation/08-chatbot.html",
        },
      ]
    },
  ],
});
