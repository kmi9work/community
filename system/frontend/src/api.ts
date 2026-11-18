// Обращение к API админки: cookie-сессия, заголовок против CSRF, ошибки с текстом.

export class ApiError extends Error {
  status: number;
  field: string;
  constructor(status: number, message: string, field = "") {
    super(message);
    this.status = status;
    this.field = field;
  }
}

type Query = Record<string, string | number | undefined | null>;

export function qs(params: Query = {}): string {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  });
  const s = u.toString();
  return s ? `?${s}` : "";
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; query?: Query; form?: FormData } = {}): Promise<T> {
  const method = opts.method || (opts.body !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = { "X-Requested-With": "fetch" };
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(path + qs(opts.query), { method, headers, body, credentials: "same-origin" });
  if (res.status === 401 && !path.startsWith("/api/auth/login")) onUnauthorized();
  if (!res.ok) {
    let message = `Ошибка ${res.status}`;
    let field = "";
    try {
      const data = await res.json();
      const d = data.detail;
      if (typeof d === "string") message = d;
      else if (d && typeof d === "object" && d.message) {
        message = d.message;
        field = d.field || "";
      } else if (Array.isArray(d) && d[0]?.msg) message = d.map((x: any) => x.msg).join("; ");
    } catch {
      /* ответ не JSON */
    }
    throw new ApiError(res.status, message, field);
  }
  const type = res.headers.get("content-type") || "";
  if (type.includes("application/json")) return res.json();
  return (await res.text()) as unknown as T;
}

export async function download(path: string, query: Query) {
  const res = await fetch(path + qs(query), { credentials: "same-origin" });
  if (!res.ok) {
    let message = `Ошибка ${res.status}`;
    try {
      message = (await res.json()).detail || message;
    } catch {
      /* не JSON */
    }
    throw new ApiError(res.status, message);
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const name = /filename="([^"]+)"/.exec(cd)?.[1] || "export.xlsx";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

// --- метаданные разделов -------------------------------------------------------------

export type FieldMeta = {
  name: string;
  label: string;
  type: string;
  choices: Record<string, string> | null;
  ref: string | null;
  ref_filter: Record<string, string> | null;
  editable: boolean;
  required: boolean;
  immutable: boolean;
  create: boolean;
  list: boolean;
  sortable: boolean;
  write_only: boolean;
  help: string;
};

export type FilterMeta = { name: string; label: string; type: string; choices: Record<string, string> | null; ref: string | null };

export type ResourceMeta = {
  name: string;
  title: string;
  section: string;
  fields: FieldMeta[];
  filters: FilterMeta[];
  default_sort: string;
  can_create: boolean;
  can_delete: boolean;
  searchable: boolean;
};

export type Ref = { id: number; label: string } | null;
export type Row = Record<string, any> & { id: number; _label: string; _editable?: boolean };
export type ListResult = { items: Row[]; total: number; page: number; size: number };
export type SavedView = { id: number; resource: string; name: string; params: ViewParams; is_system: boolean };
export type ViewParams = { filters?: Record<string, string>; sort?: string; columns?: string[]; q?: string };
