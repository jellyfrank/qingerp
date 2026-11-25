import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  base: "/qing_b2b_base/static/app/",
  build: { sourcemap: false },
});
