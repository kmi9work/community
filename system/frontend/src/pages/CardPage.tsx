import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, download, type Row } from "../api";
import { DataTable } from "../components/DataTable";
import { CreateDialog, FieldForm } from "../components/FieldForm";
import { errorText, Modal, useToast } from "../components/ui";
import { useMeta } from "../context";

type Tab = { key: string; title: string; render: () => ReactNode };

export function CardPage() {
  const { resource = "", id = "" } = useParams();
  const meta = useMeta(resource);
  const toast = useToast();
  const [row, setRow] = useState<Row | null>(null);
  const [tab, setTab] = useState("main");
  const load = useCallback(() => {
    api<Row>(`/api/r/${resource}/${id}`).then(setRow).catch((e) => toast(errorText(e), "error"));
  }, [resource, id]);
  useEffect(() => {
    setRow(null);
    setTab("main");
    load();
  }, [load]);
  if (!row) return <div className="page"><p className="muted">Загрузка…</p></div>;
  const ctx = { row, setRow, reload: load };
  const tabs = (CARDS[resource] || ((c: Ctx) => genericTabs(c, resource)))(ctx);
  const current = tabs.find((t) => t.key === tab) || tabs[0];
  return (
    <div className="page">
      <div className="crumbs">
        <Link to={`/r/${resource}`}>{meta.title}</Link>
      </div>
      <h1>{row._label}</h1>
      {tabs.length > 1 && (
        <div className="tabs" role="tablist">
          {tabs.map((t) => (
            <button key={t.key} role="tab" aria-selected={t.key === current.key} className={t.key === current.key ? "active" : ""} onClick={() => setTab(t.key)}>
              {t.title}
            </button>
          ))}
        </div>
      )}
      <div className="tab-body">{current.render()}</div>
    </div>
  );
}

type Ctx = { row: Row; setRow: (r: Row) => void; reload: () => void };

function genericTabs({ row, setRow }: Ctx, resource: string): Tab[] {
  return [{ key: "main", title: "Основное", render: () => <FieldForm resource={resource} row={row} onSaved={setRow} /> }];
}

function Sub({ resource, fixed, hide, create, actions, reloadKey, onLoaded }: { resource: string; fixed: Record<string, string>; hide?: string[]; create?: Record<string, any>; actions?: ReactNode; reloadKey?: number; onLoaded?: (r: any, q: Record<string, string>) => void }) {
  const [adding, setAdding] = useState(false);
  const [key, setKey] = useState(0);
  return (
    <>
      <DataTable resource={resource} fixed={fixed} hide={hide ?? Object.keys(fixed)} actions={actions} onCreate={create ? () => setAdding(true) : undefined} reloadKey={key + (reloadKey || 0)} onLoaded={onLoaded} />
      {adding && create && <CreateDialog resource={resource} fixed={create} onClose={() => setAdding(false)} onCreated={() => { setAdding(false); setKey((k) => k + 1); }} />}
    </>
  );
}

function ExportButton({ query }: { query: Record<string, string> }) {
  const toast = useToast();
  return (
    <button className="ghost" onClick={async () => {
      const q: Record<string, string> = {};
      Object.entries(query).forEach(([k, v]) => (k.startsWith("f.") || k === "q") && (q[k] = v));
      try {
        await download("/api/export/runs", q);
      } catch (e) {
        toast(errorText(e), "error");
      }
    }}>Выгрузить в Excel</button>
  );
}

function RunsWithExport({ fixed }: { fixed: Record<string, string> }) {
  const [query, setQuery] = useState<Record<string, string>>({});
  return <Sub resource="runs" fixed={fixed} onLoaded={(_r, q) => setQuery(q)} actions={<ExportButton query={query} />} />;
}

// --- мероприятие ----------------------------------------------------------------------------

function EventMain({ row, setRow, reload }: Ctx) {
  const toast = useToast();
  const navigate = useNavigate();
  const [dialog, setDialog] = useState<"" | "move" | "cancel">("");
  const [form, setForm] = useState({ date: row.date, time: row.time ? row.time.slice(0, 5) : "", address: row.address || "", reason: "" });
  const closed = row.status === "cancelled" || row.status === "completed";
  const act = async (fn: () => Promise<any>, ok: string) => {
    try {
      const r = await fn();
      toast(r?.recipients !== undefined ? `${ok}. Получателей: ${r.recipients}` : ok);
      setDialog("");
      reload();
    } catch (e) {
      toast(errorText(e), "error");
    }
  };
  const upload = async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    try {
      await api(`/api/events/${row.id}/poster`, { form: fd });
      toast("Афиша загружена");
      reload();
    } catch (e) {
      toast(errorText(e), "error");
    }
  };
  return (
    <>
      <div className="actions-bar">
        <button disabled={closed} onClick={async () => { try { const r = await api(`/api/events/${row.id}/announce`, { method: "POST" }); navigate(`/r/broadcasts/${r.broadcast_id}`); } catch (e) { toast(errorText(e), "error"); } }}>Разослать анонс</button>
        <button disabled={closed} onClick={() => setDialog("move")}>Перенести</button>
        <button disabled={closed} className="danger" onClick={() => setDialog("cancel")}>Отменить</button>
        <button disabled={closed} onClick={() => confirm("Перевести мероприятие в «проведено»?") && act(() => api(`/api/events/${row.id}/complete`, { method: "POST" }), "Мероприятие проведено")}>Провести</button>
        <button onClick={() => window.open(`/api/events/${row.id}/master-list`, "_blank", "noopener")}>Список для мастера</button>
      </div>
      <div className="poster">
        {row.poster ? <img src={row.poster} alt="Афиша" /> : <div className="poster-empty muted">Афиши нет</div>}
        <div>
          <label className="button">
            Загрузить афишу
            <input type="file" accept="image/jpeg,image/png" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          </label>
          {row.poster && <button className="ghost" onClick={() => act(() => api(`/api/events/${row.id}/poster`, { method: "DELETE" }), "Афиша удалена")}>Удалить</button>}
          <p className="muted small">JPEG или PNG, до 5 МБ, размер любой</p>
        </div>
      </div>
      <FieldForm resource="events" row={row} onSaved={setRow} />
      {dialog === "move" && (
        <Modal title="Перенос мероприятия" onClose={() => setDialog("")} footer={<button className="primary" onClick={() => act(() => api(`/api/events/${row.id}/move`, { body: { ...form, time: form.time || null } }), "Перенесено, сообщение записавшимся поставлено в очередь")}>Перенести и сообщить</button>}>
          <div className="form single">
            <label className="field"><span>Новая дата *</span><input type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></label>
            <label className="field"><span>Новое время</span><input type="time" value={form.time} onChange={(e) => setForm({ ...form, time: e.target.value })} /></label>
            <label className="field"><span>Адрес</span><input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /></label>
            <label className="field"><span>Причина *</span><textarea value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></label>
            <p className="muted">Прежние дата и время сохранятся, записавшимся уйдёт сообщение о переносе, напоминания пересчитаются от новой даты.</p>
          </div>
        </Modal>
      )}
      {dialog === "cancel" && (
        <Modal title="Отмена мероприятия" onClose={() => setDialog("")} footer={<button className="danger" onClick={() => act(() => api(`/api/events/${row.id}/cancel`, { body: { reason: form.reason } }), "Отменено, сообщение записавшимся поставлено в очередь")}>Отменить мероприятие</button>}>
          <label className="field"><span>Причина *</span><textarea autoFocus value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></label>
          <p className="muted">Записавшимся уйдёт сообщение об отмене, напоминания не отправятся. Записи останутся в CRM.</p>
        </Modal>
      )}
    </>
  );
}

function EventParticipants({ row }: Ctx) {
  const toast = useToast();
  const [key, setKey] = useState(0);
  const bulk = async (attendance: string | null) => {
    try {
      const r = await api(`/api/events/${row.id}/attendance`, { body: { attendance } });
      toast(`Отмечено: ${r.count}`);
      setKey((k) => k + 1);
    } catch (e) {
      toast(errorText(e), "error");
    }
  };
  return (
    <Sub resource="participations" fixed={{ event: String(row.id) }} create={{ event: row.id }} reloadKey={key}
      actions={<><button className="ghost" onClick={() => bulk("came")}>Отметить всех пришедшими</button><button className="ghost" onClick={() => bulk(null)}>Снять отметки</button></>} />
  );
}

const eventTabs = (ctx: Ctx): Tab[] => [
  { key: "main", title: "Основное", render: () => <EventMain {...ctx} /> },
  { key: "participants", title: "Участники", render: () => <EventParticipants {...ctx} /> },
  { key: "organizers", title: "Организаторы", render: () => <Sub resource="organizers" fixed={{ event: String(ctx.row.id) }} create={{ event: ctx.row.id }} /> },
  { key: "reminders", title: "Напоминания", render: () => <Sub resource="reminders" fixed={{ event: String(ctx.row.id) }} create={{ event: ctx.row.id }} /> },
  { key: "broadcasts", title: "Рассылки", render: () => <Sub resource="broadcasts" fixed={{ event: String(ctx.row.id) }} /> },
  { key: "answers", title: "Ответы", render: () => <RunsWithExport fixed={{ event: String(ctx.row.id) }} /> },
];

// --- участник ------------------------------------------------------------------------------------

function ParticipantMain({ row, setRow, reload }: Ctx) {
  const toast = useToast();
  return (
    <>
      <div className="actions-bar">
        <button disabled={!row.consent} onClick={async () => {
          if (!confirm("Отозвать согласие на обработку персональных данных?")) return;
          try { await api(`/api/participants/${row.id}/revoke-consent`, { method: "POST" }); toast("Согласие отозвано"); reload(); } catch (e) { toast(errorText(e), "error"); }
        }}>Отозвать согласие</button>
      </div>
      <FieldForm resource="participants" row={row} onSaved={setRow} />
    </>
  );
}

const participantTabs = (ctx: Ctx): Tab[] => {
  const f = { participant: String(ctx.row.id) };
  return [
    { key: "main", title: "Данные", render: () => <ParticipantMain {...ctx} /> },
    { key: "consents", title: "Согласия", render: () => <Sub resource="consents" fixed={f} /> },
    { key: "subs", title: "Подписки", render: () => <Sub resource="subscriptions" fixed={f} create={{ participant: ctx.row.id }} /> },
    { key: "interests", title: "Интересы", render: () => <Sub resource="interests" fixed={f} /> },
    { key: "events", title: "Записи", render: () => <Sub resource="participations" fixed={f} /> },
    { key: "runs", title: "Опросы", render: () => <Sub resource="runs" fixed={f} /> },
    { key: "refusals", title: "Отказы", render: () => <><p className="muted">Отказ от всех опросов: <b>{ctx.row.surveys_declined_all ? "да" : "нет"}</b>. Удаление строки снимает отказ.</p><Sub resource="refusals" fixed={f} /></> },
    { key: "appeals", title: "Обращения", render: () => <Sub resource="appeals" fixed={f} /> },
  ];
};

// --- опросы ----------------------------------------------------------------------------------------

function QuestionnaireMain({ row, setRow }: Ctx) {
  const toast = useToast();
  const navigate = useNavigate();
  return (
    <>
      {row.purpose === "survey" && (
        <div className="actions-bar">
          <button disabled={!row.is_active} onClick={async () => { try { const r = await api(`/api/questionnaires/${row.id}/send`, { method: "POST" }); navigate(`/r/broadcasts/${r.broadcast_id}`); } catch (e) { toast(errorText(e), "error"); } }}>Разослать опрос</button>
        </div>
      )}
      <FieldForm resource="questionnaires" row={row} onSaved={setRow} />
    </>
  );
}

const questionnaireTabs = (ctx: Ctx): Tab[] => {
  const f = { questionnaire: String(ctx.row.id) };
  return [
    { key: "main", title: "Основное", render: () => <QuestionnaireMain {...ctx} /> },
    { key: "questions", title: "Вопросы", render: () => <Sub resource="questions" fixed={f} create={{ questionnaire: ctx.row.id }} /> },
    { key: "runs", title: "Прохождения", render: () => <RunsWithExport fixed={f} /> },
    { key: "refusals", title: "Отказы", render: () => <Sub resource="refusals" fixed={f} /> },
  ];
};

const questionTabs = (ctx: Ctx): Tab[] => [
  { key: "main", title: "Вопрос", render: () => <FieldForm resource="questions" row={ctx.row} onSaved={ctx.setRow} /> },
  ...(ctx.row.answer_type !== "open" ? [{ key: "options", title: "Варианты ответа", render: () => <Sub resource="options" fixed={{ question: String(ctx.row.id) }} create={{ question: ctx.row.id }} /> }] : []),
];

const runTabs = (ctx: Ctx): Tab[] => [
  { key: "main", title: "Прохождение", render: () => <FieldForm resource="runs" row={ctx.row} onSaved={ctx.setRow} /> },
  { key: "answers", title: "Ответы", render: () => <Sub resource="answers" fixed={{ run: String(ctx.row.id) }} /> },
];

// --- рассылка ----------------------------------------------------------------------------------------

const KIND_FIELDS: Record<string, string[]> = {
  announcement: ["event"],
  survey: ["questionnaire"],
  reminder: ["event"],
  event_change: ["event"],
  text: [],
};
const MODE_FIELDS: Record<string, string> = { event_types: "event_types", levels: "levels", selected: "participants" };

function safeHtml(html: string) {
  const esc = html.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc
    .replace(/&amp;(amp|lt|gt|quot);/g, "&$1;")
    .replace(/&lt;(\/?)(b|i|u|s|code|pre)&gt;/g, "<$1$2>")
    .replace(/&lt;a href="(https?:\/\/[^"<>\s]+)"&gt;(.*?)&lt;\/a&gt;/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$2</a>');
}

function BroadcastMain({ row, setRow, reload }: Ctx) {
  const toast = useToast();
  const navigate = useNavigate();
  const [preview, setPreview] = useState<any>(null);
  const [previewError, setPreviewError] = useState("");
  const [cancel, setCancel] = useState(false);
  const [reason, setReason] = useState("");
  const draft = row.status === "draft";
  const active = row.status === "queued" || row.status === "sending";
  const all = ["event", "questionnaire", "event_types", "levels", "participants"];
  const keep = new Set([...(KIND_FIELDS[row.kind] || []), MODE_FIELDS[row.recipient_mode]].filter(Boolean));
  const hide = all.filter((f) => !keep.has(f));

  useEffect(() => {
    if (!draft) return;
    setPreview(null);
    setPreviewError("");
    api(`/api/broadcasts/${row.id}/preview`).then(setPreview).catch((e) => setPreviewError(errorText(e)));
  }, [row]);

  useEffect(() => {
    if (!active) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [active, reload]);

  const act = async (fn: () => Promise<any>, ok: string) => {
    try {
      const r = await fn();
      toast(ok);
      setCancel(false);
      if (r?.broadcast_id && r.broadcast_id !== row.id) navigate(`/r/broadcasts/${r.broadcast_id}`);
      else reload();
    } catch (e) {
      toast(errorText(e), "error");
    }
  };
  const total = row.recipients_count ? row.delivered_count + row.errors_count : 0;
  return (
    <>
      <div className="actions-bar">
        {draft && <button onClick={() => act(() => api(`/api/broadcasts/${row.id}/test`, { method: "POST" }), "Тестовое сообщение поставлено в очередь")}>Отправить себе</button>}
        {draft && <button className="primary" disabled={!preview} onClick={() => confirm(`Отправить рассылку ${preview?.recipients_count ?? 0} получателям?`) && act(() => api(`/api/broadcasts/${row.id}/launch`, { method: "POST" }), "Рассылка запущена")}>Отправить</button>}
        {(draft || active) && <button className="danger" onClick={() => setCancel(true)}>Отменить рассылку</button>}
        {row.errors_count > 0 && row.status !== "cancelled" && <button onClick={() => act(() => api(`/api/broadcasts/${row.id}/retry`, { method: "POST" }), "Ошибки поставлены на повтор")}>Повторить ошибки</button>}
      </div>
      {!draft && (
        <div className="progress">
          <div className="bar"><div style={{ width: `${row.recipients_count ? Math.min(100, (100 * total) / row.recipients_count) : 0}%` }} /></div>
          <span>Получателей: <b>{row.recipients_count}</b> · доставлено: <b>{row.delivered_count}</b> · ошибок: <b className={row.errors_count ? "error" : ""}>{row.errors_count}</b></span>
          {row.cancel_reason && <span className="muted">Причина отмены: {row.cancel_reason}</span>}
        </div>
      )}
      <div className="split">
        <FieldForm resource="broadcasts" row={row} onSaved={setRow} hide={hide} />
        {draft && (
          <aside className="preview">
            <h3>Предпросмотр</h3>
            {previewError && <p className="error">{previewError}</p>}
            {preview && (
              <>
                {preview.messages.map((m: any, i: number) => (
                  <div className="bubble" key={i}>
                    {m.photo && <div className="muted small">[афиша]</div>}
                    <div className="bubble-text" dangerouslySetInnerHTML={{ __html: safeHtml(m.text) }} />
                    {m.buttons.map((r: string[], j: number) => (
                      <div className="bubble-buttons" key={j}>{r.map((b) => <span key={b}>{b}</span>)}</div>
                    ))}
                  </div>
                ))}
                <p>Получателей: <b>{preview.recipients_count}</b> (Телеграм: {preview.channels.telegram}, MAX: {preview.channels.max})</p>
                <details>
                  <summary>Список получателей</summary>
                  <ul className="recipients">{preview.recipients.map((r: any) => <li key={r.id}><Link to={`/r/participants/${r.id}`}>{r.label}</Link></li>)}</ul>
                </details>
              </>
            )}
          </aside>
        )}
      </div>
      {cancel && (
        <Modal title="Отменить рассылку" onClose={() => setCancel(false)} footer={<button className="danger" onClick={() => act(() => api(`/api/broadcasts/${row.id}/cancel`, { body: { reason } }), "Рассылка отменена")}>Отменить</button>}>
          <label className="field"><span>Причина *</span><textarea autoFocus value={reason} onChange={(e) => setReason(e.target.value)} /></label>
          <p className="muted">Уже доставленные сообщения не отзываются.</p>
        </Modal>
      )}
    </>
  );
}

const broadcastTabs = (ctx: Ctx): Tab[] => [
  { key: "main", title: "Рассылка", render: () => <BroadcastMain {...ctx} /> },
  { key: "deliveries", title: "Доставка", render: () => <Sub resource="deliveries" fixed={{ broadcast: String(ctx.row.id) }} /> },
];

const CARDS: Record<string, (ctx: Ctx) => Tab[]> = {
  events: eventTabs,
  participants: participantTabs,
  questionnaires: questionnaireTabs,
  questions: questionTabs,
  runs: runTabs,
  broadcasts: broadcastTabs,
};
