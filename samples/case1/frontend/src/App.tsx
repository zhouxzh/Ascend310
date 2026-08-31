import {
  Activity,
  CalendarCheck2,
  CheckCircle2,
  Clock3,
  LayoutDashboard,
  RefreshCw,
  ScanFace,
  ShieldCheck,
  UserPlus,
  UsersRound,
  Wifi,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { getDefaultApi } from "./api";
import type { ApiClient, AttendanceRecord, HealthStatus, PageKey, User } from "./types";
import { CapturePanel, type CaptureValue } from "./components/CapturePanel";
import { LiveFeed } from "./components/LiveFeed";
import { RecordTable } from "./components/RecordTable";
import { UserTable } from "./components/UserTable";

interface AppProps {
  api?: ApiClient;
}

const pageFromPath = (path: string): PageKey => {
  if (path === "/users_page") return "users";
  if (path === "/attendance_page") return "attendance";
  return "dashboard";
};

const pagePath: Record<PageKey, string> = {
  dashboard: "/",
  users: "/users_page",
  attendance: "/attendance_page",
};

const pageMeta: Record<PageKey, { label: string; hint: string; icon: typeof LayoutDashboard }> = {
  dashboard: { label: "总览", hint: "运行状态与最近记录", icon: LayoutDashboard },
  users: { label: "用户管理", hint: "注册与维护人脸档案", icon: UsersRound },
  attendance: { label: "考勤记录", hint: "手动打卡与今日记录", icon: CalendarCheck2 },
};

function isToday(value?: string | null): boolean {
  if (!value) return true;
  const parsed = new Date(value.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return true;
  const today = new Date();
  return parsed.getFullYear() === today.getFullYear()
    && parsed.getMonth() === today.getMonth()
    && parsed.getDate() === today.getDate();
}

function sortNewest(records: AttendanceRecord[]): AttendanceRecord[] {
  return [...records].sort((left, right) => {
    const leftTime = left.timestamp ? new Date(left.timestamp.replace(" ", "T")).getTime() : 0;
    const rightTime = right.timestamp ? new Date(right.timestamp.replace(" ", "T")).getTime() : 0;
    return rightTime - leftTime;
  });
}

function Notice({ tone, children }: { tone: "success" | "error" | "warning"; children: string }) {
  return <div className={`notice notice--${tone}`} role="alert">{children}</div>;
}

function Metric({ icon: Icon, label, value, tone }: { icon: typeof Activity; label: string; value: string | number; tone?: "blue" | "green" | "amber" }) {
  return (
    <article className={`metric metric--${tone || "blue"}`}>
      <span className="metric__icon"><Icon size={20} aria-hidden="true" /></span>
      <div><span className="metric__label">{label}</span><strong>{value}</strong></div>
    </article>
  );
}

interface ManualCheckinProps {
  api: ApiClient;
  onCompleted: () => Promise<void>;
}

function ManualCheckin({ api, onCompleted }: ManualCheckinProps) {
  const [capture, setCapture] = useState<CaptureValue | null>(null);
  const [resetToken, setResetToken] = useState(0);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ tone: "success" | "error" | "warning"; text: string } | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!capture || (!capture.file && !capture.imageBase64)) {
      setStatus({ tone: "warning", text: "请先选择或拍摄一张图像" });
      return;
    }
    setBusy(true);
    setStatus(null);
    try {
      const result = await api.clockIn({ image: capture.file, imageBase64: capture.imageBase64 });
      if (result.success && result.match) {
        setStatus({ tone: "success", text: `打卡成功：${result.user || "已识别用户"}` });
        setCapture(null);
        setResetToken((token) => token + 1);
        await onCompleted();
      } else if (result.success) {
        setStatus({ tone: "warning", text: "未匹配到已注册用户" });
      } else {
        setStatus({ tone: "error", text: result.error || "打卡失败" });
      }
    } catch (error) {
      setStatus({ tone: "error", text: error instanceof Error ? error.message : "打卡失败" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel manual-checkin" aria-label="手动打卡">
      <header className="panel__header">
        <div><p className="eyebrow">MANUAL CHECK-IN</p><h2>手动打卡</h2></div>
        <Clock3 size={21} className="panel__header-icon" aria-hidden="true" />
      </header>
      <form onSubmit={(event) => void submit(event)}>
        <CapturePanel
          api={api}
          value={capture}
          onChange={setCapture}
          resetToken={resetToken}
          allowDevice={false}
          title="打卡图像"
        />
        {status ? <Notice tone={status.tone}>{status.text}</Notice> : null}
        <button type="submit" className="button button--primary button--full" disabled={busy}>
          <ScanFace size={19} aria-hidden="true" />{busy ? "识别中" : "提交打卡"}
        </button>
      </form>
    </section>
  );
}

interface DashboardProps {
  api: ApiClient;
  users: User[];
  records: AttendanceRecord[];
  onRefresh: () => Promise<void>;
  health: HealthStatus | null;
}

function Dashboard({ api, users, records, onRefresh, health }: DashboardProps) {
  const todayRecords = records.filter((record) => isToday(record.timestamp));
  const serviceReady = health?.status === "ok" && health.ready;
  const serviceLabel = serviceReady ? "服务就绪" : health ? "服务降级" : "状态未知";
  return (
    <>
      <section className="page-intro">
        <div>
          <p className="eyebrow">EDGE IDENTITY WORKBENCH</p>
          <h1>人脸考勤</h1>
          <p className="page-intro__text">采集、识别与本地考勤记录集中在同一工作台。</p>
        </div>
        <div className={`intro-mark${serviceReady ? "" : " intro-mark--warning"}`}>
          <ShieldCheck size={38} aria-hidden="true" /><span>{serviceLabel}</span>
        </div>
      </section>

      <section className="metric-grid" aria-label="运行概览">
        <Metric icon={UsersRound} label="已注册用户" value={users.length} tone="blue" />
        <Metric icon={CalendarCheck2} label="今日记录" value={todayRecords.length} tone="green" />
        <Metric icon={Activity} label="推理服务" value={serviceLabel} tone="amber" />
      </section>

      <div className="content-grid content-grid--dashboard">
        <LiveFeed src={api.videoFeedUrl} />
        <ManualCheckin api={api} onCompleted={onRefresh} />
      </div>

      <section className="panel panel--table">
        <header className="panel__header">
          <div><p className="eyebrow">TODAY</p><h2>今日最近记录</h2></div>
          <button type="button" className="button button--quiet" onClick={() => void onRefresh()}><RefreshCw size={17} aria-hidden="true" />刷新</button>
        </header>
        <RecordTable records={sortNewest(todayRecords).slice(0, 8)} compact />
      </section>
    </>
  );
}

interface UsersPageProps {
  api: ApiClient;
  users: User[];
  onRefresh: () => Promise<void>;
}

function UsersPage({ api, users, onRefresh }: UsersPageProps) {
  const [name, setName] = useState("");
  const [capture, setCapture] = useState<CaptureValue | null>(null);
  const [resetToken, setResetToken] = useState(0);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) {
      setStatus({ tone: "error", text: "请填写姓名" });
      return;
    }
    if (!capture) {
      setStatus({ tone: "error", text: "请先采集人脸图像" });
      return;
    }
    setBusy(true);
    setStatus(null);
    try {
      const result = await api.addUser({
        name,
        image: capture.file,
        tempPath: capture.tempPath,
        imageBase64: capture.imageBase64,
      });
      if (!result.success) {
        setStatus({ tone: "error", text: result.error || "用户注册失败" });
        return;
      }
      setName("");
      setCapture(null);
      setResetToken((token) => token + 1);
      setStatus({ tone: "success", text: "用户注册成功" });
      await onRefresh();
    } catch (error) {
      setStatus({ tone: "error", text: error instanceof Error ? error.message : "用户注册失败" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="page-intro page-intro--compact">
        <div><p className="eyebrow">IDENTITY STORE</p><h1>用户管理</h1><p className="page-intro__text">登记经过授权的人脸样本，并维护本地用户档案。</p></div>
        <div className="intro-mark intro-mark--teal"><UserPlus size={38} aria-hidden="true" /><span>{users.length} USERS</span></div>
      </section>
      <div className="content-grid content-grid--users">
        <section className="panel">
          <header className="panel__header"><div><p className="eyebrow">ENROLLMENT</p><h2>新增用户</h2></div><UserPlus size={21} className="panel__header-icon" aria-hidden="true" /></header>
          <form onSubmit={(event) => void submit(event)} className="enrollment-form">
            <label className="field"><span>姓名</span><input aria-label="姓名" value={name} onChange={(event) => setName(event.target.value)} placeholder="输入姓名" maxLength={64} /></label>
            <CapturePanel api={api} value={capture} onChange={setCapture} resetToken={resetToken} title="人脸样本" />
            {status ? <Notice tone={status.tone}>{status.text}</Notice> : null}
            <button type="submit" className="button button--primary button--full" disabled={busy}>
              <UserPlus size={19} aria-hidden="true" />{busy ? "注册中" : "注册用户"}
            </button>
          </form>
        </section>
        <UserTable api={api} users={users} onChanged={onRefresh} />
      </div>
    </>
  );
}

interface AttendancePageProps {
  api: ApiClient;
  records: AttendanceRecord[];
  onRefresh: () => Promise<void>;
}

function AttendancePage({ api, records, onRefresh }: AttendancePageProps) {
  const todayRecords = sortNewest(records.filter((record) => isToday(record.timestamp)));
  return (
    <>
      <section className="page-intro page-intro--compact">
        <div><p className="eyebrow">ATTENDANCE LOG</p><h1>考勤记录</h1><p className="page-intro__text">设备自动识别和手动打卡均保留在本地记录中。</p></div>
        <div className="intro-mark intro-mark--orange"><CalendarCheck2 size={38} aria-hidden="true" /><span>{todayRecords.length} TODAY</span></div>
      </section>
      <div className="content-grid content-grid--dashboard">
        <LiveFeed src={api.videoFeedUrl} title="设备摄像头（自动打卡）" />
        <ManualCheckin api={api} onCompleted={onRefresh} />
      </div>
      <section className="panel panel--table">
        <header className="panel__header"><div><p className="eyebrow">LATEST EVENTS</p><h2>今日最近记录</h2></div><button type="button" className="button button--quiet" onClick={() => void onRefresh()}><RefreshCw size={17} aria-hidden="true" />刷新</button></header>
        <RecordTable records={todayRecords} />
      </section>
    </>
  );
}

export default function App({ api: injectedApi }: AppProps) {
  const [api] = useState<ApiClient>(() => injectedApi ?? getDefaultApi());
  const [page, setPage] = useState<PageKey>(() => pageFromPath(window.location.pathname));
  const [users, setUsers] = useState<User[]>([]);
  const [records, setRecords] = useState<AttendanceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState("");
  const [health, setHealth] = useState<HealthStatus | null>(null);

  const refreshUsers = useCallback(async () => {
    try {
      setUsers(await api.listUsers());
      setConnectionError("");
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : "用户接口不可用");
    }
  }, [api]);

  const refreshRecords = useCallback(async () => {
    try {
      setRecords(await api.listAttendance());
      setConnectionError("");
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : "考勤接口不可用");
    }
  }, [api]);

  const refreshHealth = useCallback(async () => {
    if (!api.health) return;
    try {
      setHealth(await api.health());
    } catch (error) {
      setHealth({
        status: "degraded",
        ready: false,
        camera_ready: false,
        error: error instanceof Error ? error.message : "健康接口不可用",
      });
    }
  }, [api]);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    await Promise.all([refreshUsers(), refreshRecords(), refreshHealth()]);
    setLoading(false);
  }, [refreshHealth, refreshRecords, refreshUsers]);

  useEffect(() => {
    void refreshAll();
    const timer = window.setInterval(() => void Promise.all([refreshRecords(), refreshHealth()]), 5_000);
    return () => window.clearInterval(timer);
  }, [refreshAll, refreshHealth, refreshRecords]);

  useEffect(() => {
    const onPopState = () => setPage(pageFromPath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = (nextPage: PageKey) => {
    setPage(nextPage);
    if (window.location.pathname !== pagePath[nextPage]) window.history.pushState({}, "", pagePath[nextPage]);
  };

  const todayCount = useMemo(() => records.filter((record) => isToday(record.timestamp)).length, [records]);
  const meta = pageMeta[page];
  const PageIcon = meta.icon;
  const topbarDegraded = health?.status === "degraded";
  const topbarLabel = connectionError
    ? "服务异常"
    : loading
      ? "连接中"
      : topbarDegraded
        ? "服务降级"
        : health
          ? "服务在线"
          : "状态未知";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark"><ScanFace size={28} aria-hidden="true" /></span>
          <div><p className="brand-kicker">CASE 1 / ASCEND 310B</p><strong>人脸考勤工作台</strong></div>
        </div>
        <div className="topbar-status" aria-live="polite"><span className={`status-dot${connectionError ? " status-dot--error" : loading ? " status-dot--loading" : topbarDegraded ? " status-dot--warning" : ""}`} />{topbarLabel}<Wifi size={17} aria-hidden="true" /></div>
      </header>
      <div className="workspace">
        <aside className="sidebar">
          <p className="sidebar-label">工作区</p>
          <nav className="sidebar-nav" aria-label="主导航">
            {(Object.keys(pageMeta) as PageKey[]).map((key) => {
              const item = pageMeta[key];
              const Icon = item.icon;
              return <button key={key} type="button" className={`nav-item${page === key ? " is-active" : ""}`} aria-current={page === key ? "page" : undefined} onClick={() => navigate(key)}><Icon size={21} aria-hidden="true" /><span><strong>{item.label}</strong><small>{item.hint}</small></span></button>;
            })}
          </nav>
          <div className="sidebar-footer"><div className="sidebar-health"><span><CheckCircle2 size={18} aria-hidden="true" /></span><div><strong>本地推理</strong><small>模型与数据保留在设备</small></div></div><span className="sidebar-version">PyACL · SQLite</span></div>
        </aside>
        <main className="main-content">
          <div className="content-heading"><div className="content-heading__title"><PageIcon size={22} aria-hidden="true" /><span>{meta.label}</span></div><span className="content-heading__hint">{meta.hint}</span></div>
          {connectionError ? <Notice tone="error">{connectionError}</Notice> : null}
          {page === "dashboard" ? <Dashboard api={api} users={users} records={records} onRefresh={refreshAll} health={health} /> : null}
          {page === "users" ? <UsersPage api={api} users={users} onRefresh={refreshUsers} /> : null}
          {page === "attendance" ? <AttendancePage api={api} records={records} onRefresh={refreshRecords} /> : null}
        </main>
      </div>
    </div>
  );
}

export { isToday, pageFromPath };
