import type { TimelineCategory, TimelineItem } from "@/lib/timeline";

function CategoryIcon({ category }: { category: TimelineCategory }) {
  const common = {
    width: 13,
    height: 13,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  if (category === "alert") {
    return (
      <svg {...common}>
        <path d="M8 2.5l6 11H2z" />
        <path d="M8 7v3M8 12v.1" />
      </svg>
    );
  }
  if (category === "tool") {
    return (
      <svg {...common}>
        <circle cx="7" cy="7" r="4.5" />
        <path d="M10.5 10.5l3 3" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M8 2l5 2v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4z" />
    </svg>
  );
}

/** Vertical event timeline with a category icon, tone and relative offset per entry. */
export function Timeline({ items }: { items: TimelineItem[] }) {
  if (items.length === 0) return <p className="subtitle">No events yet.</p>;
  return (
    <ol className="vtimeline">
      {items.map((item) => (
        <li key={item.key} className={`tone-${item.tone}`} data-category={item.category}>
          <span className="vt-icon">
            <CategoryIcon category={item.category} />
          </span>
          <span className="vt-body">
            <span className="vt-title">{item.title}</span>
            {item.detail ? <span className="subtitle">{item.detail}</span> : null}
            <span className="subtitle vt-time">
              <time dateTime={item.at}>{new Date(item.at).toLocaleTimeString()}</time>
              {item.offset ? ` · ${item.offset}` : ""}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}
