import { sidebar } from "vuepress-theme-hope";

export default sidebar({
  "/": [
    {
      text: "教材目录",
      icon: "book",
      prefix: "",
      collapsible: false,
      children: "structure",
    },
  ],
  "/book/": [
    {
      text: "理论教程",
      icon: "book",
      prefix: "",
      collapsible: true,
      children: "structure",
    },
  ],
  "/experiment/": [
    {
      text: "实践案例",
      icon: "experiment",
      prefix: "",
      collapsible: true,
      children: "structure",
    },
  ],
  "/appendix/": [
    {
      text: "附录",
      icon: "book",
      prefix: "",
      collapsible: true,
      children: "structure",
    },
  ],
  "/presentation/": [
    {
      text: "教材配套演示",
      icon: "presentation",
      prefix: "",
      collapsible: false,
      children: "structure",
    },
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
    {
      text: "附录 6 · ROS2 基础教程",
      link: "/presentation/09-ros-basics.html",
    },
  ],
});
