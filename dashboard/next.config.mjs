// Dashboard reads signals.db ONLY (ADR-0007: never fetches). better-sqlite3
// is a native module — keep it external to the server bundle.
const nextConfig = {
  serverExternalPackages: ["better-sqlite3"],
};
export default nextConfig;
