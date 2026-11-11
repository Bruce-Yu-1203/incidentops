"use client";

import { useState } from "react";

export function CopyButton({
  value,
  label,
  text = "Copy",
  className = "secondary small",
}: {
  value: string;
  label: string;
  text?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={className}
      aria-label={label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? "Copied" : text}
    </button>
  );
}
