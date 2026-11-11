export const DISPLACEMENT_MAP_URL = "/chat-displacement-v1.png";
export const SHOCKWAVE_DURATION_MS = 1150;
export const LITE_DURATION_MS = 750;

let decoded = false;
let preparing: Promise<boolean> | null = null;

/** Decode the static lookup texture before the first interaction. Never generate it on click. */
export function prepareDisplacementMap(): Promise<boolean> {
  if (preparing) return preparing;
  preparing = (async () => {
    try {
      const image = new Image();
      image.src = DISPLACEMENT_MAP_URL;
      await image.decode();
      decoded = true;
      return true;
    } catch {
      preparing = null;
      return false;
    }
  })();
  return preparing;
}

export function displacementMapReady(): boolean { return decoded; }

/** Clip the expensive HTML filter to pixels that can actually be seen. */
export function visibleFilterBounds(
  rect: { left: number; top: number; right: number; bottom: number },
  width: number, height: number,
): { x: number; y: number; width: number; height: number } | null {
  const left = Math.max(0, rect.left), top = Math.max(0, rect.top);
  const right = Math.min(width, rect.right), bottom = Math.min(height, rect.bottom);
  return right <= left || bottom <= top ? null : {
    x: left - rect.left, y: top - rect.top, width: right - left, height: bottom - top,
  };
}

/** Geometry stays fixed for the duration; animation frames only change opacity. */
export function glowRange(rect: { left: number; top: number; right: number; bottom: number }, ox: number, oy: number) {
  const nx = Math.max(rect.left, Math.min(ox, rect.right));
  const ny = Math.max(rect.top, Math.min(oy, rect.bottom));
  return {
    near: Math.hypot(ox - nx, oy - ny),
    far: Math.hypot(Math.max(ox - rect.left, rect.right - ox), Math.max(oy - rect.top, rect.bottom - oy)),
  };
}
