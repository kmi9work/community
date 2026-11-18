import { useEffect, useState } from "react";
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { api, setUnauthorizedHandler, type ResourceMeta, type SavedView } from "./api";
import { DataTable } from "./components/DataTable";
import { CreateDialog } from "./components/FieldForm";
import { RefSelect } from "./components/fields";
import { errorText, ToastProvider, useToast } from "./components/ui";
import { MeContext, MetaContext, useMe, useMeta, type Me } from "./context";
import { CardPage } from "./pages/CardPage";

const MAIN = [
  ["participants", "Участники"],
  ["events", "Мероприятия"],
  ["questionnaires", "Опросы"],
  ["broadcasts", "Рассылки"],
  ["appeals", "Обращения"],
];
const DICTS = [
  ["event_types", "Типы мероприятий"],
  ["levels", "Уровни участия"],
  ["activity_types", "Типы деятельности"],
  ["roles", "Роли на игре"],
  ["consent_documents", "Документы о согласии"],
];

export default function App() {
  return (
    <ToastProvider>
      <BrowserRouter>
        <Root />
      </BrowserRouter>
    </ToastProvider>
  );
}

function Root() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [meta, setMeta] = useState<Record<string, ResourceMeta> | null>(null);
  useEffect(() => {
    setUnauthorizedHandler(() => setMe(null));
    api<Me>("/api/auth/me").then(setMe).catch(() => setMe(null));
  }, []);
  useEffect(() => {
    if (me) api("/api/meta").then((m) => setMeta(m.resources)).catch(() => setMeta(null));
  }, [me?.id]);
  if (me === undefined) return <div className="center muted">Загрузка…</div>;
  if (me === null) return <Login onLogin={setMe} />;
  if (!meta) return <div className="center muted">Загрузка…</div>;
  return (
    <MeContext.Provider value={{ me, setMe }}>
      <MetaContext.Provider value={meta}>
        <Layout />
      </MetaContext.Provider>
    </MeContext.Provider>
  );
}

function Login({ onLogin }: { onLogin: (m: Me) => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await api<Me>("/api/auth/login", { body: { login, password } }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="center">
      <form className="login" onSubmit={submit}>
        <h1>CRM сообщества</h1>
        <label className="field"><span>Логин</span><input autoFocus autoComplete="username" value={login} onChange={(e) => setLogin(e.target.value)} /></label>
        <label className="field"><span>Пароль</span><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy || !login || !password}>Войти</button>
      </form>
    </div>
  );
}

function Layout() {
  const { me, setMe } = useMe();
  const [menu, setMenu] = useState(false);
  const logout = async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    setMe(null);
  };
  return (
    <div className={`layout ${menu ? "menu-open" : ""}`}>
      <header className="topbar">
        <button className="icon burger" aria-label="Меню" onClick={() => setMenu(!menu)}>☰</button>
        <Link to="/" className="brand">CRM сообщества</Link>
      </header>
      <nav className="sidebar" onClick={() => setMenu(false)}>
        <div className="brand desktop">CRM сообщества</div>
        {MAIN.map(([r, t]) => <NavLink key={r} to={`/r/${r}`}>{t}</NavLink>)}
        <div className="nav-group">Справочники</div>
        {DICTS.map(([r, t]) => <NavLink key={r} to={`/r/${r}`}>{t}</NavLink>)}
        <div className="nav-group">Ещё</div>
        <NavLink to="/control">Контроль</NavLink>
        <NavLink to="/r/admins">Администраторы</NavLink>
        <NavLink to="/profile">Профиль</NavLink>
        <div className="nav-foot">
          <span className="muted">{me?.name || me?.login}</span>
          <button className="ghost small" onClick={logout}>Выйти</button>
        </div>
      </nav>
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to="/r/events" replace />} />
          <Route path="/r/:resource" element={<ListPage />} />
          <Route path="/r/:resource/:id" element={<CardRoute />} />
          <Route path="/control" element={<Control />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="*" element={<p className="page muted">Страница не найдена</p>} />
        </Routes>
      </main>
    </div>
  );
}

function CardRoute() {
  const { resource = "", id = "" } = useParams();
  return <CardPage key={`${resource}-${id}`} />;
}

function ListPage() {
  const { resource = "" } = useParams();
  const meta = useMeta(resource);
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);
  return (
    <div className="page" key={resource}>
      <h1>{meta.title}</h1>
      <DataTable key={resource} resource={resource} syncUrl onCreate={() => setAdding(true)} />
      {adding && <CreateDialog resource={resource} onClose={() => setAdding(false)} onCreated={(row) => navigate(`/r/${resource}/${row.id}`)} />}
    </div>
  );
}

function Control() {
  const [views, setViews] = useState<SavedView[]>([]);
  useEffect(() => {
    api<SavedView[]>("/api/views").then((v) => setViews(v.filter((x) => x.is_system))).catch(() => {});
  }, []);
  const link = (v: SavedView) => {
    const p = new URLSearchParams();
    Object.entries(v.params.filters || {}).forEach(([k, val]) => p.set(`f.${k}`, val));
    if (v.params.sort) p.set("sort", v.params.sort);
    return `/r/${v.resource}?${p.toString()}`;
  };
  return (
    <div className="page">
      <h1>Контроль</h1>
      <p className="muted">Представления, которые показывают проблемы.</p>
      <div className="cards">
        {views.map((v) => (
          <Link key={v.id} className="card-link" to={link(v)}>{v.name}</Link>
        ))}
      </div>
    </div>
  );
}

function Profile() {
  const { me, setMe } = useMe();
  const toast = useToast();
  const [name, setName] = useState(me?.name || "");
  const [participant, setParticipant] = useState(me?.participant || null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const save = async () => {
    try {
      const body: any = { name, participant_id: participant ? participant.id : 0 };
      if (next) Object.assign(body, { current_password: current, new_password: next });
      setMe(await api<Me>("/api/auth/me", { method: "PATCH", body }));
      setCurrent("");
      setNext("");
      toast("Профиль сохранён");
    } catch (e) {
      toast(errorText(e), "error");
    }
  };
  return (
    <div className="page">
      <h1>Профиль</h1>
      <div className="form single">
        <label className="field"><span>Логин</span><div className="value">{me?.login}</div></label>
        <label className="field"><span>Имя</span><input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="field"><span>Своя карточка участника</span><RefSelect resource="participants" value={participant} onChange={setParticipant} /><small className="muted">Сюда уходят тестовые сообщения «Отправить себе». Сначала зарегистрируйтесь в боте.</small></label>
        <label className="field"><span>Текущий пароль</span><input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></label>
        <label className="field"><span>Новый пароль (от 10 символов)</span><input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></label>
        <div className="form-actions"><button className="primary" onClick={save}>Сохранить</button></div>
      </div>
    </div>
  );
}
