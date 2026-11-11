import { isCsrfOriginAllowed } from "next/dist/server/app-render/csrf-protection";
import { describe, expect, it } from "vitest";
import config from "../../next.config";

describe("documented development origin", () => {
  it("allows the IPv4 demo address without opening development resources to other hosts", () => {
    expect(isCsrfOriginAllowed("127.0.0.1", config.allowedDevOrigins)).toBe(true);
    expect(isCsrfOriginAllowed("untrusted.example", config.allowedDevOrigins)).toBe(false);
    expect(isCsrfOriginAllowed("evil.127.0.0.1", config.allowedDevOrigins)).toBe(false);
  });
});
