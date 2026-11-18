import { useEffect, useState } from "react";
import { api, ApiError, type FieldMeta, type Row } from "../api";
import { useMeta } from "../context";
import { FieldInput, FieldValue, toPayload } from "./fields";
import { errorText, Modal, useToast } from "./ui";

// Форма карточки: редактируемые поля + Сохранить; остальные — только показ.
export function FieldForm({ resource, row, onSaved, hide = [] }: { resource: string; row: Row; onSaved: (r: Row) => void; hide?: string[] }) {
  const meta = useMeta(resource);
  const toast = useToast();
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [error, setError] = useState<{ field: string; message: string } | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => setDraft({}), [row.id]);
  const editableRow = row._editable !== false;
  const fields = meta.fields.filter((f) => !hide.includes(f.name) && f.type !== "image");
  const dirty = Object.keys(draft).length > 0;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const body: Record<string, any> = {};
      Object.entries(draft).forEach(([k, v]) => (body[k] = toPayload(fields.find((f) => f.name === k)!, v)));
      const updated = await api<Row>(`/api/r/${resource}/${row.id}`, { method: "PATCH", body });
      setDraft({});
      onSaved(updated);
      toast("Сохранено");
    } catch (e) {
      if (e instanceof ApiError && e.field) setError({ field: e.field, message: e.message });
      toast(errorText(e), "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="form">
      {fields.map((f) => {
        const canEdit = editableRow && f.editable && !f.immutable;
        if (f.write_only && !canEdit) return null;
        const value = f.name in draft ? draft[f.name] : row[f.name];
        return (
          <label key={f.name} className={`field ${error?.field === f.name ? "invalid" : ""} ${f.type === "text" ? "wide" : ""}`}>
            <span>{f.label}{f.required && canEdit ? " *" : ""}</span>
            {canEdit ? <FieldInput field={f} value={value} onChange={(v) => setDraft((d) => ({ ...d, [f.name]: v }))} /> : <div className="value"><FieldValue field={f} value={value} /></div>}
            {f.help && canEdit && <small className="muted">{f.help}</small>}
            {error?.field === f.name && <small className="error">{error.message}</small>}
          </label>
        );
      })}
      {editableRow && (
        <div className="form-actions">
          <button className="primary" disabled={!dirty || saving} onClick={save}>Сохранить</button>
          {dirty && <button className="ghost" onClick={() => setDraft({})}>Отменить изменения</button>}
        </div>
      )}
    </div>
  );
}

// Создание записи. fixed — значения, заданные контекстом (например, мероприятие во вкладке).
export function CreateDialog({ resource, fixed = {}, onClose, onCreated, title }: { resource: string; fixed?: Record<string, any>; onClose: () => void; onCreated: (r: Row) => void; title?: string }) {
  const meta = useMeta(resource);
  const toast = useToast();
  const fields = meta.fields.filter((f) => f.create && f.type !== "computed" && f.type !== "image" && !(f.name in fixed));
  const [values, setValues] = useState<Record<string, any>>(() => {
    const init: Record<string, any> = {};
    fields.forEach((f) => {
      if (f.type === "bool" && ["is_active", "subscribed"].includes(f.name)) init[f.name] = true;
    });
    return init;
  });
  const [error, setError] = useState<{ field: string; message: string } | null>(null);
  const submit = async () => {
    try {
      const body: Record<string, any> = { ...fixed };
      fields.forEach((f: FieldMeta) => {
        if (values[f.name] !== undefined) body[f.name] = toPayload(f, values[f.name]);
      });
      const row = await api<Row>(`/api/r/${resource}`, { body });
      onCreated(row);
    } catch (e) {
      if (e instanceof ApiError && e.field) setError({ field: e.field, message: e.message });
      toast(errorText(e), "error");
    }
  };
  return (
    <Modal title={title || `${meta.title}: новая запись`} onClose={onClose} footer={<><button className="ghost" onClick={onClose}>Отмена</button><button className="primary" onClick={submit}>Создать</button></>}>
      <div className="form single">
        {fields.map((f) => (
          <label key={f.name} className={`field ${error?.field === f.name ? "invalid" : ""}`}>
            <span>{f.label}{f.required ? " *" : ""}</span>
            <FieldInput field={f} value={values[f.name]} onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
            {f.help && <small className="muted">{f.help}</small>}
            {error?.field === f.name && <small className="error">{error.message}</small>}
          </label>
        ))}
      </div>
    </Modal>
  );
}
