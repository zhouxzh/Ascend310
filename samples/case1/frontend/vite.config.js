import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
    plugins: [react()],
    base: "./",
    build: {
        outDir: "dist",
        emptyOutDir: true,
        sourcemap: false,
    },
    server: {
        host: "0.0.0.0",
        port: 5173,
        proxy: {
            "/api": {
                target: "http://127.0.0.1:5000",
                changeOrigin: true,
            },
            "/video_feed": {
                target: "http://127.0.0.1:5000",
                changeOrigin: true,
            },
            "/uploads": {
                target: "http://127.0.0.1:5000",
                changeOrigin: true,
            },
        },
    },
    test: {
        environment: "jsdom",
        globals: true,
        setupFiles: "./src/test/setup.ts",
        include: ["src/**/*.test.{ts,tsx}"],
    },
});
