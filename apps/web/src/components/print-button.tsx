"use client";

export function PrintButton({ className = "secondary no-print" }: { className?: string }) {
  return (
    <button type="button" className={className} onClick={() => window.print()}>
      Print or save as PDF
    </button>
  );
}
