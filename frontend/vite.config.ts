import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({mode})=>({
  define:{__PRODUCT__:JSON.stringify(mode==='product')},
  // 与后端技术页隔离，开发代理和正式部署均使用 /app/。
  base: "/app/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api/context": { target: "http://127.0.0.1:8787", changeOrigin: false },
    },
  },
}));
