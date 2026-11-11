import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The documented demo URL uses the IPv4 loopback host, not localhost.
  allowedDevOrigins: ["127.0.0.1"],
  cacheComponents: true,
  partialPrefetching: true,
};

export default nextConfig;
