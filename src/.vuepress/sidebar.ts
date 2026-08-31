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
        "01-hardware-basics.md",
        "02-linux-commands.md",
        "03-python-basics.md",
        "04-vibe-coding.md",
        "05-face-recognition.md",
        "06-object-tracking.md",
        "07-smart-piano.md",
        "08-chatbot.md"
      ]
    },
  ],
});
