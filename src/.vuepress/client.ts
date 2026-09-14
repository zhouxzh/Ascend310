import { defineClientConfig, withBase } from "vuepress/client";
import { Layout as ThemeLayout } from "vuepress-theme-hope/client";
import { defineComponent, h } from "vue";

const presentationHtmlPath = /^\/Ascend310\/presentation\/\d{2}-[^/]+\.html(?:$|[?#])/u;

const textbookLinks = [
  { text: "理论教程", link: "/book/" },
  { text: "实践案例", link: "/experiment/" },
  { text: "附录", link: "/appendix/" },
  { text: "附录 2 · Ubuntu 教程", link: "/appendix/appendix2.html" },
  { text: "附录 6 · ROS2 基础", link: "/appendix/appendix6.html" },
  { text: "教学演示", link: "/presentation/" },
  { text: "附录 2 · Ubuntu 演示", link: "/presentation/02-ubuntu-basics.html" },
  { text: "附录 6 · ROS2 演示", link: "/presentation/09-ros-basics.html" },
];

const TextbookSidebar = defineComponent({
  name: "TextbookSidebar",
  setup() {
    return () =>
      h("nav", { class: "vp-sidebar-textbook" }, [
        h("p", { class: "vp-sidebar-title" }, "教材导航"),
        h(
          "ul",
          { class: "vp-sidebar-links" },
          textbookLinks.map((item) =>
            h(
              "li",
              { key: item.link },
              h(
                "a",
                {
                  class: "vp-sidebar-link",
                  href: withBase(item.link),
                },
                item.text,
              ),
            ),
          ),
        ),
      ]);
  },
});

const CustomLayout = defineComponent({
  name: "CustomLayout",
  setup(props, { slots }) {
    return () =>
      h(ThemeLayout, props, {
        ...slots,
        sidebarTop: () => h(TextbookSidebar),
      });
  },
});

export default defineClientConfig({
  layouts: {
    Layout: CustomLayout,
  },

  enhance() {
    if (typeof window === "undefined") return;

    window.addEventListener(
      "click",
      (event) => {
        if (
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        ) {
          return;
        }

        const target = event.target;
        if (!(target instanceof Element)) return;

        const anchor = target.closest<HTMLAnchorElement>("a[href]");
        if (!anchor || !presentationHtmlPath.test(new URL(anchor.href).pathname)) {
          return;
        }

        // Marp decks are static HTML files in public/, not VuePress routes.
        // Force a document navigation instead of letting Vue Router render 404.
        event.preventDefault();
        event.stopImmediatePropagation();
        window.location.assign(anchor.href);
      },
      true,
    );
  },
});
