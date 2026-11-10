import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  type ReactNode,
} from "react";
import type {
  Account,
  Video,
  Job,
  Track,
  WindowResult,
  DiscoveredEvent,
  Report,
  Scope,
} from "../models/backend";
import * as auth from "../api/auth";
import * as api from "../api/jobs";
import { listVideos } from "../api/videos";
import { getReport } from "../api/review";
import { ApiError } from "../api/client";

interface Data {
  videos: Video[];
  jobs: Job[];
  videoId: string;
  job: Job | null;
  tracks: Track[];
  events: DiscoveredEvent[];
  windows: WindowResult[];
  scope: Scope | null;
  report: Report | null;
}
const empty: Data = {
  videos: [],
  jobs: [],
  videoId: "",
  job: null,
  tracks: [],
  events: [],
  windows: [],
  scope: null,
  report: null,
};
const Context = createContext<null | ReturnType<typeof useStore>>(null);
function useStore() {
  const [account, setAccount] = useState<Account | null>(null),
    [checking, setChecking] = useState(true),
    [restoring,setRestoring]=useState(false),
    [data, setData] = useState<Data>(empty),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const accountRef = useRef(account);
  accountRef.current = account;
  const generation = useRef(0);
  const selected = useRef("");
  const selectedRun = useRef<string | null>(null);
  const key = (a: Account) => "lessontrace.selection." + a.actor;
  const remember = (patch: Record<string, string>) => {
    const a = accountRef.current;
    if (!a) return;
    let old = {};
    try {
      old = JSON.parse(localStorage.getItem(key(a)) || "{}");
    } catch {
      /* IDs only */
    }
    localStorage.setItem(key(a), JSON.stringify({ ...old, ...patch }));
  };
  const loadJob = useCallback(async (id: string) => {
    const ticket = ++generation.current;
    const priorJob=selected.current;
    selected.current = id;
    setLoading(true);
    setError("");
    try {
      const job = await api.getJob(id);
      const [events, windows] = await Promise.all([
        api.getEvents(id),
        api.getWindows(id),
      ]);
      let tracks: Track[] = [],
        scope: Scope | null = null;
      if (job.run_id && job.stage !== "tracking") {
        [tracks, scope] = await Promise.all([
          api.getTracks(job.run_id),
          api.getScope(job.run_id),
        ]);
      }
      if (ticket !== generation.current) return;
      selectedRun.current = job.run_id;
      setData((d) => ({
        ...d,
        job,
        videoId: job.video_id,
        events,
        windows,
        tracks,
        scope,
          report: d.job?.job_id === job.job_id ? d.report : null,
      }));
      remember({ jobId: id, videoId: job.video_id,...(priorJob&&priorJob!==id?{reportId:''}:{}) });
    } catch (e) {
      if (ticket === generation.current) setError(String(e));
    } finally {
      if (ticket === generation.current) setLoading(false);
    }
  }, []);
  const refresh = useCallback(async () => {
    if (!accountRef.current) return;
    setError("");
    setLoading(true);
    const actor = accountRef.current.actor;
    try {
      const [videos, jobs] = await Promise.all([listVideos(), api.listJobs()]);
      if (accountRef.current?.actor !== actor) return undefined;
      setData((d) => ({ ...d, videos, jobs }));
      return { videos, jobs };
    } catch (e) {
      setError(String(e));
      return undefined;
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    const lost = () => {
      const hadSession = !!accountRef.current;
      accountRef.current = null;
      generation.current++;
      selected.current = "";
      setAccount(null);
      setData(empty);
      setError(hadSession ? "登录已失效，请重新登录" : "请登录后继续");
    };
    window.addEventListener("lessontrace:unauthorized", lost);
    void auth
      .me()
      .then(setAccount)
      .catch((error) => {
        setAccount(null);
        if (!(error instanceof ApiError && error.status === 401))
          setError("无法连接本机工作空间，请启动服务后重试：" + String(error));
      })
      .finally(() => setChecking(false));
    return () => window.removeEventListener("lessontrace:unauthorized", lost);
  }, []);
  useEffect(() => {
    if (!account) return;
    let active=true;setRestoring(true);
    accountRef.current = account;
    void (async () => {
      try {
      const all = await refresh();
      if (!all) return;
      let saved: { jobId?: string; videoId?: string; reportId?: string } = {};
      try {
        saved = JSON.parse(localStorage.getItem(key(account)) || "{}");
      } catch {
        /* ignore malformed IDs */
      }
      const availableJobs = all.jobs.filter((j) =>
        all.videos.some(
          (v) => v.video_id === j.video_id && v.status === "active",
        ),
      );
      const job =
        availableJobs.find((j) => j.job_id === saved.jobId) ||
        (!saved.videoId
          ? availableJobs[0]
          : saved.jobId
            ? availableJobs.find((j) => j.video_id === saved.videoId)
            : undefined);
      if (job) await loadJob(job.job_id);
      else
        setData((d) => ({
          ...d,
          videoId:
            all.videos.find(
              (v) => v.video_id === saved.videoId && v.status === "active",
            )?.video_id ||
            all.videos.find((v) => v.status === "active")?.video_id ||
            "",
        }));
      if (saved.reportId && job) {
        try {
          const r = await getReport(saved.reportId);
          if (active&&accountRef.current?.actor===account.actor&&(r.status === "stale" || r.classroom.run_id === job.run_id))
            setData((d) => ({ ...d, report: r }));
        } catch (e) {
          setError("已恢复任务；旧报告暂不可用：" + String(e));
        }
      }
      } finally {if(active)setRestoring(false);}
    })();
    return()=>{active=false;};
  }, [account, refresh, loadJob]);
  useEffect(() => {
    if (
      !data.job ||
      !["queued", "running", "pausing", "cancelling"].includes(data.job.status)
    )
      return;
    const id = data.job.job_id;
    const t = setInterval(() => {
      if (selected.current === id) void loadJob(id);
    }, 3000);
    return () => clearInterval(t);
  }, [data.job?.job_id, data.job?.status, loadJob]);
  const selectVideo = (videoId: string) => {
    generation.current++;
    setLoading(false);
    setError('');
    selected.current = "";
    selectedRun.current = null;
    setData((d) => ({
      ...d,
      videoId,
      job: null,
      tracks: [],
      events: [],
      windows: [],
      scope: null,
      report: null,
    }));
    remember({ videoId, jobId: "", reportId: "" });
  };
  const setReport = (report: Report) => {
    if (
      report.status === "current" &&
      report.classroom.run_id !== selectedRun.current
    )
      throw Error("报告与当前录像不一致，请先载入对应分析任务");
    setData((d) => ({ ...d, report }));
    remember({ reportId: report.report_id });
  };
  return {
    account,
    checking,
    restoring,
    loading,
    error,
    data,
    setError,
    canWrite: !!account && account.role !== "viewer",
    login: async (identifier: string, password?: string) => {
      const a = await auth.login(identifier, password);
      setAccount(a);
      setError("");
    },
    logout: async () => {
      await auth.logout();
      generation.current++;
      selected.current = "";
      setAccount(null);
      setData(empty);
    },
    refresh,
    loadJob,
    selectVideo,
    setReport,
  };
}
export function SessionProvider({ children }: { children: ReactNode }) {
  const value = useStore();
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export function useSession() {
  const value = useContext(Context);
  if (!value) throw Error("SessionProvider missing");
  return value;
}
