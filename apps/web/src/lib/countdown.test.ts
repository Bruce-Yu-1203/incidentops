import { describe, expect, it } from "vitest";

import { remaining, shortHash } from "./countdown";

const now = new Date("2026-11-09T12:00:00Z");

describe("remaining", () => {
  it("formats minutes and seconds under an hour", () => {
    expect(remaining("2026-11-09T12:08:42Z", now)).toEqual({
      expired: false,
      seconds: 522,
      label: "08:42",
    });
  });

  it("adds hours when needed", () => {
    expect(remaining("2026-11-09T13:02:03Z", now).label).toBe("1:02:03");
  });

  it("reports expiry and bad input", () => {
    expect(remaining("2026-11-09T12:00:00Z", now)).toMatchObject({
      expired: true,
      label: "expired",
    });
    expect(remaining("not a date", now)).toMatchObject({ expired: true, label: "unknown" });
  });
});

describe("shortHash", () => {
  it("keeps the algorithm and both ends", () => {
    expect(shortHash(`sha256:7c1e2d${"0".repeat(54)}a90b`)).toBe("sha256:7c1e2d…a90b");
    expect(shortHash("sha256:abc")).toBe("sha256:abc");
  });
});
