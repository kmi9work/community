import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type FieldMeta, type Ref } from "../api";
import { fmtDate, fmtDateTime, fmtTime } from "../context";

// --- показ значения ---------------------------------------------------------------------

export function FieldValue({ field, value }: { field: FieldMeta; value: any }) {
  if (value === null || value === undefined || value === "") return <span className="muted">—</span>;
  switch (field.type) {
    case "bool":
      return value ? <span className="yes">да</span> : <span className="muted">нет</span>;
    case "enum":
      return <span className={`badge badge-${value}`}>{field.choices?.[value] ?? value}</span>;
    case "date":
      return <>{fmtDate(value)}</>;
    case "time":
      return <>{fmtTime(value)}</>;
    case "datetime":
      return <>{fmtDateTime(value)}</>;
    case "fk":
      return field.ref ? <Link to={`/r/${field.ref}/${value.id}`} onClick={(e) => e.stopPropagation()}>{value.label}</Link> : <>{value.label}</>;
    case "m2m":
      return <>{(value as Ref[]).map((v) => v?.label).join(", ") || <span className="muted">—</span>}</>;
    case "image":
      return <img className="thumb" src={value} alt="" loading="lazy" />;
    case "text":
      return <span className="clip" title={value}>{value}</span>;
    case "computed":
      return <Computed value={value} />;
    default:
      return <>{String(value)}</>;
  }
}

function Computed({ value }: { value: any }) {
  if (typeof value === "boolean") return value ? <span className="yes">да</span> : <span className="muted">нет</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">—</span>;
    if (typeof value[0] === "object" && value[0] && "question" in value[0])
      return (
        <div className="answers">
          {value.map((a: any, i: number) => (
            <div key={i}>
              <span className="muted">{a.question}:</span> {a.answer}
            </div>
          ))}
        </div>
      );
    return <>{value.join(", ")}</>;
  }
  if (value && typeof value === "object" && "label" in value) return <>{value.label}</>;
  return <>{String(value)}</>;
}

// --- поля ввода ---------------------------------------------------------------------------

type InputProps = { field: FieldMeta; value: any; onChange: (v: any) => void; autoFocus?: boolean; onCommit?: () => void };

export function FieldInput({ field, value, onChange, autoFocus, onCommit }: InputProps) {
  const key = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && field.type !== "text" && onCommit) onCommit();
  };
  switch (field.type) {
    case "bool":
      return (
        <label className="check">
          <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} autoFocus={autoFocus} />
          <span>{value ? "да" : "нет"}</span>
        </label>
      );
    case "enum":
      return (
        <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} autoFocus={autoFocus}>
          <option value="">—</option>
          {Object.entries(field.choices || {}).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      );
    case "text":
      return <textarea value={value ?? ""} rows={5} onChange={(e) => onChange(e.target.value)} autoFocus={autoFocus} />;
    case "int":
    case "float":
      return <input type="number" step={field.type === "float" ? "any" : "1"} value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)} onKeyDown={key} autoFocus={autoFocus} />;
    case "date":
      return <input type="date" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} onKeyDown={key} autoFocus={autoFocus} />;
    case "time":
      return <input type="time" value={value ? String(value).slice(0, 5) : ""} onChange={(e) => onChange(e.target.value || null)} onKeyDown={key} autoFocus={autoFocus} />;
    case "fk":
      return <RefSelect resource={field.ref!} filter={field.ref_filter} value={value} onChange={onChange} autoFocus={autoFocus} />;
    case "m2m":
      return <MultiRefSelect resource={field.ref!} filter={field.ref_filter} value={value || []} onChange={onChange} />;
    default:
      return <input type={field.write_only ? "password" : "text"} value={value ?? ""} onChange={(e) => onChange(e.target.value)} onKeyDown={key} autoFocus={autoFocus} autoComplete={field.write_only ? "new-password" : "off"} />;
  }
}

function filterQuery(filter: Record<string, string> | null | undefined) {
  const q: Record<string, string> = {};
  Object.entries(filter || {}).forEach(([k, v]) => (q[`f.${k}`] = v));
  return q;
}

function useOptions(resource: string, filter: Record<string, string> | null | undefined, text: string, open: boolean) {
  const [items, setItems] = useState<{ id: number; label: string }[]>([]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const t = setTimeout(() => {
      api(`/api/r/${resource}/options`, { query: { q: text, ...filterQuery(filter) } })
        .then((r) => alive && setItems(r))
        .catch(() => alive && setItems([]));
    }, 200);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [resource, JSON.stringify(filter), text, open]);
  return items;
}

function useOutside(ref: React.RefObject<HTMLElement>, fn: () => void) {
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) fn();
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [ref, fn]);
}

export function RefSelect({ resource, filter, value, onChange, autoFocus, placeholder }: { resource: string; filter?: Record<string, string> | null; value: Ref; onChange: (v: Ref) => void; autoFocus?: boolean; placeholder?: string }) {
  const [open, setOpen] = useState(!!autoFocus);
  const [text, setText] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useOutside(box, () => setOpen(false));
  const items = useOptions(resource, filter, text, open);
  return (
    <div className="ref" ref={box}>
      <button type="button" className="ref-value" onClick={() => setOpen(!open)}>
        {value ? value.label : <span className="muted">{placeholder || "выбрать…"}</span>}
      </button>
      {value && (
        <button type="button" className="ref-clear" title="Очистить" onClick={() => onChange(null)}>×</button>
      )}
      {open && (
        <div className="ref-pop">
          <input autoFocus placeholder="Поиск" value={text} onChange={(e) => setText(e.target.value)} />
          <div className="ref-list">
            {items.map((it) => (
              <button type="button" key={it.id} className={value?.id === it.id ? "active" : ""} onClick={() => { onChange(it); setOpen(false); }}>
                {it.label}
              </button>
            ))}
            {!items.length && <div className="muted pad">Ничего не найдено</div>}
          </div>
        </div>
      )}
    </div>
  );
}

export function MultiRefSelect({ resource, filter, value, onChange }: { resource: string; filter?: Record<string, string> | null; value: Ref[]; onChange: (v: Ref[]) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useOutside(box, () => setOpen(false));
  const items = useOptions(resource, filter, text, open);
  const ids = new Set(value.map((v) => v?.id));
  return (
    <div className="ref multi" ref={box}>
      <div className="chips" onClick={() => setOpen(true)}>
        {value.map((v) => (
          <span className="chip" key={v!.id}>
            {v!.label}
            <button type="button" onClick={(e) => { e.stopPropagation(); onChange(value.filter((x) => x!.id !== v!.id)); }}>×</button>
          </span>
        ))}
        <span className="muted add">+ добавить</span>
      </div>
      {open && (
        <div className="ref-pop">
          <input autoFocus placeholder="Поиск" value={text} onChange={(e) => setText(e.target.value)} />
          <div className="ref-list">
            {items.map((it) => (
              <button type="button" key={it.id} className={ids.has(it.id) ? "active" : ""} onClick={() => onChange(ids.has(it.id) ? value.filter((x) => x!.id !== it.id) : [...value, it])}>
                {ids.has(it.id) ? "✓ " : ""}{it.label}
              </button>
            ))}
            {!items.length && <div className="muted pad">Ничего не найдено</div>}
          </div>
        </div>
      )}
    </div>
  );
}

// значение поля для отправки на сервер
export function toPayload(field: FieldMeta, value: any) {
  if (field.type === "fk") return value ? value.id : null;
  if (field.type === "m2m") return (value || []).map((v: Ref) => v!.id);
  return value;
}
