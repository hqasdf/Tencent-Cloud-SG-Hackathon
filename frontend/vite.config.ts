import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { validatePublicBuildEnvironment } from "./src/utils/apiConfig";

export default defineConfig(({ command, mode }) => {
  if (command === "build") validatePublicBuildEnvironment(loadEnv(mode, process.cwd(), "VITE_"));
  return { plugins: [react()], server: { port: 5173, host: "127.0.0.1" } };
});
