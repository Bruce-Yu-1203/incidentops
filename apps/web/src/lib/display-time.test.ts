import { describe, expect, it } from "vitest";
import { formatDisplayTime } from "./display-time";

describe("explicit display time", () => {
  it("formats a specified timezone independently of the host timezone", () => {
    const previous = process.env.TZ;
    try {
      process.env.TZ = "Asia/Tokyo";
      const tokyoHost = formatDisplayTime("2026-11-11T20:00:00Z", "UTC", "date");
      process.env.TZ = "America/Toronto";
      expect(formatDisplayTime("2026-11-11T20:00:00Z", "UTC", "date")).toBe(tokyoHost);
      expect(formatDisplayTime("2026-11-11T20:00:00Z", "America/Toronto")).toBe("15:00:00");
      expect(formatDisplayTime("2026-11-11T20:00:00Z", "UTC", "axis")).toBe("20:00");
    } finally {
      if (previous === undefined) delete process.env.TZ;
      else process.env.TZ = previous;
    }
  });
});
