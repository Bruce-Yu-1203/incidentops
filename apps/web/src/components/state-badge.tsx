import { type Machine, type Tone, toneOf } from "@/lib/states";

/** Stroke icons per tone, so state is never conveyed by colour alone. */
export function ToneIcon({ tone, size = 12 }: { tone: Tone; size?: number }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.9,
    strokeLinecap: "round" as const,
    "aria-hidden": true,
  };
  switch (tone) {
    case "success":
      return (
        <svg {...common}>
          <path d="M3 8.5l3 3 7-7" />
        </svg>
      );
    case "danger":
      return (
        <svg {...common}>
          <path d="M4 4l8 8M12 4l-8 8" />
        </svg>
      );
    case "unknown":
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6.5" />
          <path d="M6.2 6.2a1.9 1.9 0 1 1 2.6 1.8c-.5.2-.8.6-.8 1.1v.4M8 11.6v.1" />
        </svg>
      );
    case "attention":
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6.5" />
          <path d="M8 4.5v4M8 11v.2" />
        </svg>
      );
    case "progress":
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6" />
          <path d="M8 4.5V8l2.5 1.5" />
        </svg>
      );
    default:
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="2.5" fill="currentColor" />
        </svg>
      );
  }
}

export function StateBadge({
  machine,
  state,
  label,
}: {
  machine: Machine;
  state: string;
  label?: string;
}) {
  const tone = toneOf(machine, state);
  return (
    <span className={`badge tone-${tone}`} data-tone={tone}>
      <ToneIcon tone={tone} />
      {label ?? state}
    </span>
  );
}
