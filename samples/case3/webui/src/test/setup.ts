import '@testing-library/jest-dom/vitest'

class ResizeObserverStub implements ResizeObserver {
  constructor(private callback: ResizeObserverCallback) {}

  observe(target: Element) {
    this.callback(
      [{ target, contentRect: { width: 800, height: 320 } } as ResizeObserverEntry],
      this,
    )
  }
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub
