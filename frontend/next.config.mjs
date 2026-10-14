import { readFileSync } from "node:fs";
import YAML from "yaml";

const frontendPolicy = YAML.parse(readFileSync(new URL("../configs/frontend.yaml", import.meta.url), "utf8"))?.frontend;
const clientPolicy = frontendPolicy?.client;
const proxyTimeoutMs = frontendPolicy?.server?.proxy_timeout_ms;
const uploadMaxBytes = frontendPolicy?.server?.upload_max_bytes;
if (!Number.isSafeInteger(proxyTimeoutMs) || proxyTimeoutMs <= 0) {
  throw new Error("configs/frontend.yaml must define a finite positive proxy timeout");
}
const policyFields = ["request_timeout_ms", "execution_confirmation_timeout_ms", "connection_test_timeout_ms", "control_refresh_ms", "activity_clock_ms", "readiness_refresh_ms", "max_contract_bytes", "upload_max_bytes"];
if (!clientPolicy || policyFields.some((key) => !Number.isSafeInteger(clientPolicy[key]) || clientPolicy[key] <= 0)) {
  throw new Error("configs/frontend.yaml must define a finite positive client policy");
}
if (!Number.isSafeInteger(uploadMaxBytes) || uploadMaxBytes <= 0 || clientPolicy.upload_max_bytes !== uploadMaxBytes) {
  throw new Error("configs/frontend.yaml must define matching positive client/server upload limits");
}

/** @type {import('next').NextConfig} */
const localRuntime = YAML.parse(readFileSync(new URL("../configs/local_runtime.yaml", import.meta.url), "utf8"));
const backendUrl =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  `http://${localRuntime.host}:${localRuntime.backend_port}`;

const nextConfig = {
  env: Object.fromEntries(policyFields.map((key) => [`NEXT_PUBLIC_MARS_${key.toUpperCase()}`, String(clientPolicy[key])])),
  output: "standalone",
  reactStrictMode: true,
  // TensorBoard serves a directory URL; preserve its trailing slash for assets.
  skipTrailingSlashRedirect: true,
  typedRoutes: false,
  experimental: { proxyTimeout: proxyTimeoutMs, middlewareClientMaxBodySize: uploadMaxBytes },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
