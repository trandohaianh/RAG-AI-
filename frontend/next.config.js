/** @type {import('next').NextConfig} */

// Content-Security-Policy (có nonce theo từng request) được đặt trong proxy.ts.
const nextConfig = {
  output: "standalone",
};

module.exports = nextConfig;
