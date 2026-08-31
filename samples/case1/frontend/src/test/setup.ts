import "@testing-library/jest-dom/vitest";

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

class ResizeObserverStub {
  observe() { return undefined; }
  unobserve() { return undefined; }
  disconnect() { return undefined; }
}

Object.defineProperty(window, "ResizeObserver", { writable: true, value: ResizeObserverStub });

Object.defineProperty(HTMLMediaElement.prototype, "play", {
  configurable: true,
  value: () => Promise.resolve(),
});

if (!URL.createObjectURL) {
  URL.createObjectURL = () => "blob:case1-test";
}

if (!URL.revokeObjectURL) {
  URL.revokeObjectURL = () => undefined;
}

if (!navigator.mediaDevices) {
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: async () => ({ getTracks: () => [] }) },
  });
}
