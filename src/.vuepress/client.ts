import { defineClientConfig } from "vuepress/client";

const presentationHtmlPath = /^\/Ascend310\/presentation\/\d{2}-[^/]+\.html(?:$|[?#])/u;

export default defineClientConfig({
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
