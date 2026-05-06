import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        mars: {
          bg: "#0b0d12",
          panel: "#13161d",
          panel2: "#181c25",
          border: "#23262d",
          subtle: "#2a2e38",
          accent: "#6366f1",
          accent2: "#8b5cf6",
        },
        tier: {
          1: "#fbbf24",
          2: "#f97316",
          3: "#22c55e",
          4: "#ef4444",
          5: "#a855f7",
        },
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          '"PingFang SC"',
          '"Microsoft YaHei"',
          '"Source Han Sans CN"',
          '"Helvetica Neue"',
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          '"JetBrains Mono"',
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
export default config;
