import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  define:{__PRODUCT__:'false'},
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    include: ["src/test/**/*.test.{ts,tsx}"],
    poolOptions: { threads: { singleThread: true } },
    reporters: ["default", "junit"],
    outputFile: {
      junit: "../outputs/ui_purple_validation/frontend_tests.xml",
    },
  },
});
