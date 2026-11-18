import { createContext, useContext } from "react";
import type { ResourceMeta } from "./api";

export type Me = { id: number; login: string; name: string; participant: { id: number; label: string } | null };

export const MetaContext = createContext<Record<string, ResourceMeta>>({});
export const MeContext = createContext<{ me: Me | null; setMe: (m: Me | null) => void }>({ me: null, setMe: () => {} });

export function useMeta(resource: string): ResourceMeta {
  const meta = useContext(MetaContext)[resource];
  if (!meta) throw new Error(`Нет раздела ${resource}`);
  return meta;
}

export function useAllMeta() {
  return useContext(MetaContext);
}

export function useMe() {
  return useContext(MeContext);
}

// --- форматирование ----------------------------------------------------------------

const dateFmt = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
const dtFmt = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });

export function fmtDate(v: string | null | undefined) {
  if (!v) return "";
  const [y, m, d] = v.split("-").map(Number);
  return dateFmt.format(new Date(y, m - 1, d));
}

export function fmtDateTime(v: string | null | undefined) {
  if (!v) return "";
  return dtFmt.format(new Date(v));
}

export function fmtTime(v: string | null | undefined) {
  return v ? v.slice(0, 5) : "";
}

export const CHANNEL_LABEL: Record<string, string> = { telegram: "Телеграм", max: "MAX" };
