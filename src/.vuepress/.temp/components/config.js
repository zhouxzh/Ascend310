import { hasGlobalComponent } from "C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/node_modules/.pnpm/@vuepress+helper@2.0.0-rc.7_d2890f67ec4779ca8f9364961404c63a/node_modules/@vuepress/helper/lib/client/index.js";
import Badge from "C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/node_modules/.pnpm/vuepress-plugin-components@_bfd52fe433d86779990647891442379d/node_modules/vuepress-plugin-components/lib/client/components/Badge.js";

import "C:/Users/hhc00/iCloudDrive/Desktop/Ascend310/node_modules/.pnpm/@vuepress+helper@2.0.0-rc.7_d2890f67ec4779ca8f9364961404c63a/node_modules/@vuepress/helper/lib/client/styles/sr-only.css";

export default {
  enhance: ({ app }) => {
    if(!hasGlobalComponent("Badge")) app.component("Badge", Badge);
    
  },
  setup: () => {

  },
  rootComponents: [

  ],
};
