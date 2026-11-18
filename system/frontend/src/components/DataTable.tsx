import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type FieldMeta, type FilterMeta, type ListResult, type Ref, type Row, type SavedView, type ViewParams } from "../api";
import { useMeta } from "../context";
import { FieldInput, FieldValue, RefSelect, toPayload } from "./fields";
import { errorText, Modal, useToast } from "./ui";

type Props = {
  resource: string;
  fixed?: Record<string, string>; // скрытые фильтры (вкладки карточек)
  hide?: string[]; // скрытые колонки
  actions?: ReactNode;
  onCreate?: () => void;
  reloadKey?: number;
  syncUrl?: boolean;
  onLoaded?: (r: ListResult, query: Record<string, string>) => void;
  pageSize?: number;
};

const LS = (r: string) => `cols:${r}`;

function readColumns(resource: string, fallback: string[]) {
  try {
    const raw = localStorage.getItem(LS(resource));
    if (raw) return JSON.parse(raw) as string[];
  } catch {
    /* хранилище недоступно */
  }
  return fallback;
}

export function DataTable({ resource, fixed = {}, hide = [], actions, onCreate, reloadKey = 0, syncUrl, onLoaded, pageSize = 50 }: Props) {
  const meta = useMeta(resource);
  const toast = useToast();
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const fixedKeys = Object.keys(fixed);
  const visibleFields = meta.fields.filter((f) => !f.write_only && !hide.includes(f.name) && !fixedKeys.includes(f.name));
  const defaultCols = visibleFields.filter((f) => f.list).map((f) => f.name);

  const init = useMemo(() => {
    if (!syncUrl) return { q: "", sort: meta.default_sort, page: 1, filters: {} as Record<string, string> };
    const filters: Record<string, string> = {};
    search.forEach((v, k) => k.startsWith("f.") && (filters[k.slice(2)] = v));
    return { q: search.get("q") || "", sort: search.get("sort") || meta.default_sort, page: Number(search.get("page") || 1), filters };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resource]);

  const [q, setQ] = useState(init.q);
  const [qDebounced, setQDebounced] = useState(init.q);
  const [sort, setSort] = useState(init.sort);
  const [page, setPage] = useState(init.page);
  const [filters, setFilters] = useState<Record<string, string>>(init.filters);
  const [filterLabels, setFilterLabels] = useState<Record<string, Ref>>({});
  const [columns, setColumns] = useState<string[]>(() => readColumns(resource, defaultCols));
  const [data, setData] = useState<ListResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [views, setViews] = useState<SavedView[]>([]);
  const [showCols, setShowCols] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const filtersRef = useRef<HTMLDivElement>(null);
  const [saveView, setSaveView] = useState(false);
  const [viewName, setViewName] = useState("");
  const [editing, setEditing] = useState<{ id: number; field: string; value: any } | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setQDebounced(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    if (fixedKeys.length) return;
    api<SavedView[]>("/api/views", { query: { resource } }).then(setViews).catch(() => setViews([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resource]);

  useEffect(() => {
    if (!showFilters) return;
    const onDown = (e: MouseEvent) => {
      if (filtersRef.current && !filtersRef.current.contains(e.target as Node)) setShowFilters(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setShowFilters(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [showFilters]);

  const query = useMemo(() => {
    const out: Record<string, string> = { sort, page: String(page), size: String(pageSize) };
    if (qDebounced) out.q = qDebounced;
    Object.entries({ ...filters, ...fixed }).forEach(([k, v]) => v !== "" && v !== undefined && (out[`f.${k}`] = v));
    return out;
  }, [sort, page, qDebounced, JSON.stringify(filters), JSON.stringify(fixed), pageSize]);

  const load = useCallback(() => {
    setLoading(true);
    api<ListResult>(`/api/r/${resource}`, { query })
      .then((r) => {
        setData(r);
        onLoaded?.(r, query);
      })
      .catch((e) => toast(errorText(e), "error"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resource, JSON.stringify(query)]);

  useEffect(load, [load, reloadKey]);

  useEffect(() => {
    if (!syncUrl) return;
    const next = new URLSearchParams();
    Object.entries(query).forEach(([k, v]) => k !== "size" && !(k === "page" && v === "1") && !(k === "sort" && v === meta.default_sort) && next.set(k, v));
    setSearch(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(query)]);

  const setFilter = (name: string, value: string) => {
    setPage(1);
    setFilters((f) => {
      const n = { ...f };
      if (value === "" || value === undefined) delete n[name];
      else n[name] = value;
      return n;
    });
  };

  const applyView = (v: SavedView | null) => {
    const p: ViewParams = v?.params || {};
    setFilters(p.filters || {});
    setSort(p.sort || meta.default_sort);
    setQ(p.q || "");
    setPage(1);
    if (p.columns?.length) setColumns(p.columns);
  };

  const storeColumns = (cols: string[]) => {
    setColumns(cols);
    try {
      localStorage.setItem(LS(resource), JSON.stringify(cols));
    } catch {
      /* хранилище недоступно */
    }
  };

  const doSaveView = async () => {
    try {
      const v = await api<SavedView>("/api/views", { body: { resource, name: viewName, params: { filters, sort, columns, q } } });
      setViews((vs) => [...vs, v]);
      setSaveView(false);
      setViewName("");
      toast("Представление сохранено");
    } catch (e) {
      toast(errorText(e), "error");
    }
  };

  const commit = async (row: Row, field: FieldMeta, value: any) => {
    setEditing(null);
    try {
      const updated = await api<Row>(`/api/r/${resource}/${row.id}`, { method: "PATCH", body: { [field.name]: toPayload(field, value) } });
      setData((d) => (d ? { ...d, items: d.items.map((r) => (r.id === row.id ? updated : r)) } : d));
    } catch (e) {
      toast(errorText(e), "error");
    }
  };

  const remove = async (row: Row) => {
    if (!confirm(`Удалить «${row._label}»?`)) return;
    try {
      await api(`/api/r/${resource}/${row.id}`, { method: "DELETE" });
      load();
    } catch (e) {
      toast(errorText(e), "error");
    }
  };

  const cols = visibleFields.filter((f) => columns.includes(f.name));
  const filterDefs = meta.filters.filter((f) => !fixedKeys.includes(f.name) && f.type !== "str");
  const filterValue = (f: FilterMeta) =>
    f.type === "date_range" ? Boolean(filters[`${f.name}.from`] || filters[`${f.name}.to`]) : Boolean(filters[f.name]);
  const activeFilters = filterDefs.filter(filterValue).length;
  const resetFilters = () => {
    setPage(1);
    setFilters({});
    setFilterLabels({});
  };
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;

  return (
    <div className="table-wrap">
      <div className="toolbar">
        {meta.searchable && <input className="search" type="search" placeholder="Поиск" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />}
        {filterDefs.length > 0 && (
          <div className="filters-wrap" ref={filtersRef}>
            <button className={`ghost filters-btn ${activeFilters ? "on" : ""}`} aria-expanded={showFilters} onClick={() => setShowFilters(!showFilters)}>
              Фильтры
              {activeFilters > 0 && <span className="filter-count">{activeFilters}</span>}
            </button>
            {showFilters && (
              <div className="filters-pop">
                <div className="filters-pop-head">
                  <strong>Фильтры</strong>
                  {activeFilters > 0 && <button className="ghost small" onClick={resetFilters}>Сбросить</button>}
                </div>
                {filterDefs.map((f) => (
                  <FilterControl key={f.name} def={f} value={filters[f.name] || ""} label={filterLabels[f.name]} onChange={(v, lbl) => { setFilter(f.name, v); if (lbl !== undefined) setFilterLabels((l) => ({ ...l, [f.name]: lbl })); }} from={filters[`${f.name}.from`] || ""} to={filters[`${f.name}.to`] || ""} onRange={(k, v) => setFilter(`${f.name}.${k}`, v)} />
                ))}
                <button className="primary small" onClick={() => setShowFilters(false)}>Готово</button>
              </div>
            )}
          </div>
        )}
        <div className="spacer" />
        {!fixedKeys.length && (
          <select className="views" value="" onChange={(e) => { const v = views.find((x) => String(x.id) === e.target.value); if (e.target.value === "reset") applyView(null); else if (v) applyView(v); }}>
            <option value="">Представления…</option>
            <option value="reset">Сбросить фильтры</option>
            {views.map((v) => (
              <option key={v.id} value={v.id}>{v.name}</option>
            ))}
          </select>
        )}
        {!fixedKeys.length && <button className="ghost" onClick={() => setSaveView(true)}>Сохранить вид</button>}
        <div className="cols-wrap">
          <button className="ghost" onClick={() => setShowCols(!showCols)}>Колонки</button>
          {showCols && (
            <div className="cols-pop" onMouseLeave={() => setShowCols(false)}>
              {visibleFields.map((f) => (
                <label key={f.name} className="check">
                  <input type="checkbox" checked={columns.includes(f.name)} onChange={(e) => storeColumns(e.target.checked ? [...columns, f.name] : columns.filter((c) => c !== f.name))} />
                  <span>{f.label}</span>
                </label>
              ))}
              <button className="ghost small" onClick={() => storeColumns(defaultCols)}>По умолчанию</button>
            </div>
          )}
        </div>
        {actions}
        {meta.can_create && onCreate && <button className="primary" onClick={onCreate}>Добавить</button>}
      </div>

      <div className="table-scroll">
        <table className={loading ? "loading" : ""}>
          <thead>
            <tr>
              {cols.map((f) => (
                <th key={f.name} className={f.sortable ? "sortable" : ""} onClick={() => f.sortable && setSort(sort === f.name ? `-${f.name}` : f.name)}>
                  {f.label}
                  {sort === f.name && " ↑"}
                  {sort === `-${f.name}` && " ↓"}
                </th>
              ))}
              {meta.can_delete && <th />}
            </tr>
          </thead>
          <tbody>
            {data?.items.map((row) => (
              <tr key={row.id} onClick={() => navigate(`/r/${resource}/${row.id}`)}>
                {cols.map((f, index) => {
                  if (index === 0)
                    return (
                      <td key={f.name} className={`t-${f.type} first`}>
                        <Link to={`/r/${resource}/${row.id}`} onClick={(e) => e.stopPropagation()}>
                          {f.type === "fk" || f.type === "enum" || f.type === "computed" || f.type === "datetime" || f.type === "date" ? <FieldValue field={{ ...f, ref: null }} value={row[f.name]} /> : row[f.name] || row._label}
                        </Link>
                      </td>
                    );
                  const canEdit = f.editable && !f.immutable && row._editable !== false && f.type !== "image";
                  const isEditing = editing?.id === row.id && editing.field === f.name;
                  return (
                    <td key={f.name} className={`t-${f.type} ${canEdit ? "editable" : ""}`} onClick={(e) => { if (canEdit) { e.stopPropagation(); if (!isEditing) setEditing({ id: row.id, field: f.name, value: row[f.name] }); } }}>
                      {isEditing ? (
                        <InlineEditor field={f} initial={editing.value} onCommit={(v) => commit(row, f, v)} onCancel={() => setEditing(null)} />
                      ) : (
                        <FieldValue field={f} value={row[f.name]} />
                      )}
                    </td>
                  );
                })}
                {meta.can_delete && (
                  <td className="row-actions" onClick={(e) => e.stopPropagation()}>
                    <button className="icon danger" title="Удалить" onClick={() => remove(row)}>×</button>
                  </td>
                )}
              </tr>
            ))}
            {data && !data.items.length && (
              <tr className="empty">
                <td colSpan={cols.length + 1}>Ничего нет</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <span className="muted">Всего: {data?.total ?? "…"}</span>
        {pages > 1 && (
          <>
            <button className="ghost small" disabled={page <= 1} onClick={() => setPage(page - 1)}>←</button>
            <span>{page} / {pages}</span>
            <button className="ghost small" disabled={page >= pages} onClick={() => setPage(page + 1)}>→</button>
          </>
        )}
      </div>
      {saveView && (
        <Modal title="Сохранить представление" onClose={() => setSaveView(false)} footer={<button className="primary" onClick={doSaveView}>Сохранить</button>}>
          <label className="field">
            <span>Название</span>
            <input autoFocus value={viewName} onChange={(e) => setViewName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && doSaveView()} />
          </label>
          <p className="muted">Сохранятся поиск, фильтры, сортировка и колонки.</p>
        </Modal>
      )}
    </div>
  );
}

function InlineEditor({ field, initial, onCommit, onCancel }: { field: FieldMeta; initial: any; onCommit: (v: any) => void; onCancel: () => void }) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLDivElement>(null);
  const instant = ["bool", "enum", "fk"].includes(field.type);
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        if (instant) onCancel();
        else onCommit(value);
      }
    };
    const k = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => {
      document.removeEventListener("mousedown", h);
      document.removeEventListener("keydown", k);
    };
  }, [value]);
  return (
    <div className="inline-editor" ref={ref}>
      <FieldInput field={field} value={value} autoFocus onCommit={() => onCommit(value)} onChange={(v) => (instant ? onCommit(v) : setValue(v))} />
      {field.type === "m2m" || field.type === "text" ? <button className="primary small" onClick={() => onCommit(value)}>OK</button> : null}
    </div>
  );
}

function FilterControl({ def, value, label, onChange, from, to, onRange }: { def: FilterMeta; value: string; label?: Ref; onChange: (v: string, label?: Ref) => void; from: string; to: string; onRange: (k: "from" | "to", v: string) => void }) {
  if (def.type === "enum")
    return (
      <select className="filter" value={value} onChange={(e) => onChange(e.target.value)} title={def.label}>
        <option value="">{def.label}: все</option>
        {Object.entries(def.choices || {}).map(([k, v]) => (
          <option key={k} value={k}>{v}</option>
        ))}
      </select>
    );
  if (def.type === "bool")
    return (
      <select className="filter" value={value} onChange={(e) => onChange(e.target.value)} title={def.label}>
        <option value="">{def.label}: все</option>
        <option value="true">{def.label}: да</option>
        <option value="false">{def.label}: нет</option>
      </select>
    );
  if (def.type === "fk")
    return (
      <div className="filter">
        <RefSelect resource={def.ref!} value={value ? label || { id: Number(value), label: `#${value}` } : null} placeholder={def.label} onChange={(v) => onChange(v ? String(v.id) : "", v)} />
      </div>
    );
  if (def.type === "date_range")
    return (
      <span className="filter range" title={def.label}>
        <span className="muted">{def.label}</span>
        <input type="date" value={from} onChange={(e) => onRange("from", e.target.value)} />
        <span className="muted">—</span>
        <input type="date" value={to} onChange={(e) => onRange("to", e.target.value)} />
      </span>
    );
  return null;
}
