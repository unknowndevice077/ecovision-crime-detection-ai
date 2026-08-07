// next.config.ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // lucide-react is imported with large named-import lists in several files
  // (17 icons in page.tsx, ~19 in DevteamView.tsx) -- this makes Next.js
  // rewrite those into per-icon imports so only the icons actually used end
  // up in the client bundle, instead of pulling in more of the package.
  experimental: {
    optimizePackageImports: ["lucide-react"],
  },
};

export default nextConfig;