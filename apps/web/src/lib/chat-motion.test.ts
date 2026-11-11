import { afterEach, describe, expect, it, vi } from "vitest";
import { glowRange, visibleFilterBounds } from "./chat-motion";

afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });

describe("chat animation preparation", () => {
  it("decodes once and shares the result across repeated warmups", async () => {
    const decode = vi.fn().mockResolvedValue(undefined);
    class FakeImage { src = ""; decode = decode; }
    vi.stubGlobal("Image", FakeImage);
    const motion = await import("./chat-motion");
    expect(motion.displacementMapReady()).toBe(false);
    const first = motion.prepareDisplacementMap();
    expect(motion.prepareDisplacementMap()).toBe(first);
    expect(await first).toBe(true);
    expect(motion.displacementMapReady()).toBe(true);
    expect(decode).toHaveBeenCalledTimes(1);
  });
  it("keeps the lightweight fallback available after a failed decode and permits retry", async () => {
    const decode = vi.fn().mockRejectedValueOnce(new Error("unavailable")).mockResolvedValue(undefined);
    class FakeImage { src = ""; decode = decode; }
    vi.stubGlobal("Image", FakeImage);
    const motion = await import("./chat-motion");
    expect(await motion.prepareDisplacementMap()).toBe(false);
    expect(motion.displacementMapReady()).toBe(false);
    expect(await motion.prepareDisplacementMap()).toBe(true);
  });
  it("bounds long scrolled pages to the visible viewport", () => {
    expect(visibleFilterBounds({ left: 0, top: -2000, right: 1400, bottom: 5000 }, 1400, 900))
      .toEqual({ x: 0, y: 2000, width: 1400, height: 900 });
    expect(visibleFilterBounds({ left: 0, top: 1000, right: 100, bottom: 1200 }, 1400, 900)).toBeNull();
  });
  it("computes highlight distances once without DOM reads during frames", () => {
    expect(glowRange({ left: 0, top: 0, right: 3, bottom: 4 }, 0, 0)).toEqual({ near: 0, far: 5 });
  });
});
