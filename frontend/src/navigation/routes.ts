import { useEffect, useState } from "react";

/* Hash routing, as Beacon did it: `#case/<id>/<tab>/<item>` deep-links anything. */

export type Page = "home" | "signin" | "overview" | "flow" | "cases" | "case" | "properties" | "field" | "findings" | "approvals" | "audit" | "settings" | "consent" | "verify" | "notfound";
export interface Route { page: Page; id?: string; tab?: string; item?: string }

export const NAV: Array<{ page: Page; label: string; key: string }> = [
  { page: "overview", label: "Overview", key: "o" },
  { page: "flow", label: "Flow", key: "w" },
  { page: "cases", label: "Cases", key: "c" },
  { page: "properties", label: "Properties", key: "p" },
  { page: "field", label: "Field", key: "l" },
  { page: "findings", label: "Findings", key: "f" },
  { page: "approvals", label: "Approvals", key: "a" },
  { page: "audit", label: "Audit", key: "u" },
  { page: "settings", label: "Settings", key: "s" },
];

export const CASE_TABS = [
  { key: "overview", label: "Overview" },
  { key: "flow", label: "Flow" },
  { key: "evidence", label: "Evidence" },
  { key: "land", label: "Land records" },
  { key: "ownership", label: "Ownership" },
  { key: "financials", label: "Financials" },
  { key: "findings", label: "Findings" },
  { key: "workflow", label: "Workflow" },
  { key: "history", label: "History" },
];

const PAGES = new Set<Page>(["home", "signin", "overview", "flow", "cases", "properties", "field", "findings", "approvals", "audit", "settings", "verify"]);

export function parseHash(hash = window.location.hash): Route {
  const [head, ...rest] = hash.replace(/^#/, "").split("?")[0].split("/").map(decodeURIComponent);
  if (!head) return { page: "home" };
  if (head === "case" && rest[0]) return { page: "case", id: rest[0], tab: rest[1] || "overview", item: rest[2] };
  if (head === "consent" && rest[0]) return { page: "consent", id: rest[0] };
  if (PAGES.has(head as Page)) return { page: head as Page, tab: rest[0] };
  return { page: "notfound" };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function go(hash: string): void {
  window.location.hash = hash;
}
