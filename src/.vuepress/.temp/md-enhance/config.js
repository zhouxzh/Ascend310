import Mermaid from "D:/Github/Ascend310/node_modules/.pnpm/vuepress-plugin-md-enhance@_dffcc5c1c333be15edaff97e511e44bd/node_modules/vuepress-plugin-md-enhance/lib/client/components/Mermaid.js";

export default {
  enhance: ({ app }) => {
    app.component("Mermaid", Mermaid);
  },
};
