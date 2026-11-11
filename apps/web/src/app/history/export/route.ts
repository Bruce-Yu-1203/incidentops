import { apiBaseUrl } from "@/lib/api";
import { apiParams, fetchHistory, type HistoryRow, parseHistoryQuery, toCsv } from "@/lib/history";
import { sessionFetch } from "@/lib/session";

const PAGE_SIZE = 200;
const MAX_PAGES = 25;

/** All rows matching the current filters, as CSV (at most 5,000). */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const query = parseHistoryQuery(Object.fromEntries(url.searchParams));
  const authed = await sessionFetch();
  const now = new Date();
  const rows: HistoryRow[] = [];
  for (let page = 1; page <= MAX_PAGES; page++) {
    const result = await fetchHistory(apiBaseUrl(), apiParams({ ...query, page }, now, PAGE_SIZE), authed);
    if (!result.ok) return new Response(`Could not export: ${result.error}\n`, { status: 502 });
    rows.push(...result.data.rows);
    if (rows.length >= result.data.total || result.data.rows.length === 0) break;
  }
  return new Response(toCsv(rows), {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": `attachment; filename="incidents-${now.toISOString().slice(0, 10)}.csv"`,
    },
  });
}
