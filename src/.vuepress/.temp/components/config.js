import { hasGlobalComponent } from "/Users/hehaocheng/WebstormProjects/Ascend310/node_modules/.pnpm/@vuepress+helper@2.0.0-rc.74_vuepress@2.0.0-rc.19_@vuepress+bundler-vite@2.0.0-rc.19_@t_d2890f67ec4779ca8f9364961404c63a/node_modules/@vuepress/helper/lib/client/index.js";
import Badge from "/Users/hehaocheng/WebstormProjects/Ascend310/node_modules/.pnpm/vuepress-plugin-components@2.0.0-rc.71_sass-embedded@1.83.4_vuepress@2.0.0-rc.19_@vuepr_bfd52fe433d86779990647891442379d/node_modules/vuepress-plugin-components/lib/client/components/Badge.js";

import "/Users/hehaocheng/WebstormProjects/Ascend310/node_modules/.pnpm/@vuepress+helper@2.0.0-rc.74_vuepress@2.0.0-rc.19_@vuepress+bundler-vite@2.0.0-rc.19_@t_d2890f67ec4779ca8f9364961404c63a/node_modules/@vuepress/helper/lib/client/styles/sr-only.css";

export default {
  enhance: ({ app }) => {
    if(!hasGlobalComponent("Badge")) app.component("Badge", Badge);
    
  },
  setup: () => {

  },
  rootComponents: [

  ],
};
