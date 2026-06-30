const staticExport = process.env.NEXT_STATIC_EXPORT === "1";

/** @type {import('next').NextConfig} */
const nextConfig = {
  ...(staticExport ? { output: "export" } : {}),
  experimental: {
    webpackBuildWorker: false,
    workerThreads: false
  }
};

export default nextConfig;
