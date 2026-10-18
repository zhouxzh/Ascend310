import { sidebar } from "vuepress-theme-hope";

// BEGIN GENERATED PRESENTATION SIDEBAR
const presentationChildren = [
  {
    text: "全书导览",
    collapsible: true,
    children: [
        { text: "课程导览", link: "/presentation/00-course-overview.html" },
    ],
  },
  {
    text: "理论章节",
    collapsible: true,
    children: [
        { text: "昇腾310B边缘计算基础", link: "/presentation/10-chapter1.html" },
        { text: "CANN软件栈核心组件解析", link: "/presentation/11-chapter2.html" },
        { text: "昇腾PyTorch扩展迁移基础", link: "/presentation/12-chapter3.html" },
        { text: "PyACL应用开发基础", link: "/presentation/13-chapter4.html" },
        { text: "MindSpore开发基础", link: "/presentation/14-chapter5.html" },
        { text: "DVPP视频处理基础", link: "/presentation/15-chapter6.html" },
        { text: "算子开发实战", link: "/presentation/16-chapter7.html" },
        { text: "性能分析与优化", link: "/presentation/17-chapter8.html" },
        { text: "模型量化案例教学", link: "/presentation/18-chapter9.html" },
        { text: "AI项目交付闭环", link: "/presentation/19-chapter10.html" },
    ],
  },
  {
    text: "实践案例",
    collapsible: true,
    children: [
        { text: "智能考勤机", link: "/presentation/20-case1.html" },
        { text: "目标跟踪检测", link: "/presentation/21-case2.html" },
        { text: "智能电子琴", link: "/presentation/22-case3.html" },
        { text: "掌纹识别", link: "/presentation/23-case4.html" },
        { text: "智能数据采集分析仪", link: "/presentation/24-case5.html" },
        { text: "智能小车（制作中）", link: "/presentation/25-case6.html" },
        { text: "智能相册", link: "/presentation/26-case7.html" },
        { text: "实时手势识别", link: "/presentation/27-case8.html" },
        { text: "智能聊天机器人（制作中）", link: "/presentation/28-case9.html" },
    ],
  },
  {
    text: "附录",
    collapsible: true,
    children: [
        { text: "开发板与基础环境（制作中）", link: "/presentation/30-appendix1.html" },
        { text: "基于昇腾 310B 的 Ubuntu 教程（制作中）", link: "/presentation/31-appendix2.html" },
        { text: "Python 编程基础（制作中）", link: "/presentation/32-appendix3.html" },
        { text: "Vibe Coding 基础", link: "/presentation/33-appendix4.html" },
        { text: "ROS2 基础教程（制作中）", link: "/presentation/34-appendix5.html" },
        { text: "工具箱、FAQ 与参数模板（制作中）", link: "/presentation/35-appendix6.html" },
        { text: "CANN 升级与版本配套（制作中）", link: "/presentation/36-appendix7.html" },
    ],
  },
];
// END GENERATED PRESENTATION SIDEBAR

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
        "chapter9.md",
        "chapter10.md"
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
        "appendix6.md",
        "appendix7.md"
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
          text: "课件总目录",
          link: "/presentation/",
        },
        ...presentationChildren,
      ]
    },
  ],
});
