"use client";
import EvidencePanel from "@/components/EvidencePanel";
import type { EvidenceBundle } from "@/lib/api";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  BookOpen,
  Check,
  ChevronRight,
  CircleUserRound,
  ClipboardCheck,
  FileText,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Radio,
  RefreshCw,
  Send,
  ShieldAlert,
  Users,
  WifiOff,
  Wrench,
} from "lucide-react";
import MarkdownContent from "@/components/MarkdownContent";
import CasePhotos from "@/components/field/CasePhotos";
import { ClosureSummary, CloseCaseDialog } from "@/components/field/CaseClosure";
import CaseDetailsEditor from "@/components/field/CaseDetailsEditor";
import CaseDispatchEditor from "@/components/field/CaseDispatchEditor";
import {
  createQueueScope,
  discardRejected,
  flush,
  legacyPendingCount,
  pending,
  rejected,
  retryRejected,
  send,
  type QueuedAction,
} from "@/lib/offlineQueue";
import { useAuth } from "@/lib/auth";
import { loadSnapshot, saveSnapshot } from "@/lib/caseCache";
import { formatDateTime } from "@/lib/utils";
import api, {
  currentSession,
  getApiErrorMessage,
  type DocItem,
  type FieldCase,
  type FieldMember,
  type FieldStatus,
  type FieldWorkspace,
} from "@/lib/api";

const STATUS_LABEL: Record<FieldStatus, string> = {
  open: "Mới tiếp nhận",
  in_progress: "Đang xử lý",
  waiting: "Chờ khách hàng",
  escalated: "Chuyển cấp",
  resolved: "Đã hoàn tất",
};

const STATUS_STYLE: Record<FieldStatus, string> = {
  open: "bg-blue-50 text-blue-700",
  in_progress: "bg-indigo-50 text-indigo-700",
  waiting: "bg-amber-50 text-amber-700",
  escalated: "bg-rose-50 text-rose-700",
  resolved: "bg-emerald-50 text-emerald-700",
};

const EMPTY_FORM = {
  title: "",
  symptom: "",
  customer_label: "",
  address: "",
  device_model: "",
};

export default function FieldOperationsPage() {
  const { user } = useAuth();
  const [workspaces, setWorkspaces] = useState<FieldWorkspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState<number | null>(null);
  const [cases, setCases] = useState<FieldCase[]>([]);
  const [members, setMembers] = useState<FieldMember[]>([]);
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  // M12: đóng ca luôn qua biên bản — chọn "Đã hoàn tất" mở hộp thoại thay vì đổi trạng thái thẳng.
  const [closing, setClosing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  // Hàng đợi khi mất sóng: số thao tác đang chờ gửi, và dòng báo cho người dùng.
  const [queued, setQueued] = useState<QueuedAction[]>([]);
  const [rejectedActions, setRejectedActions] = useState<QueuedAction[]>([]);
  const [legacyCount, setLegacyCount] = useState(0);
  const userId = user?.id;
  const queueScope = useMemo(
    () => (userId && workspaceId ? createQueueScope(api.defaults.baseURL!, userId, workspaceId) : null),
    [userId, workspaceId],
  );
  const [notice, setNotice] = useState("");
  const [online, setOnline] = useState(true);
  const [note, setNote] = useState("");
  const [stepTitle, setStepTitle] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [answerEvidence, setAnswerEvidence] = useState<EvidenceBundle>({});
  const [memberEmail, setMemberEmail] = useState("");
  const [snapshotAt, setSnapshotAt] = useState<string | null>(null);

  const selectedCase = useMemo(() => cases.find((item) => item.id === selectedId) ?? null, [cases, selectedId]);

  const load = useCallback(
    async (id?: number | null) => {
      setError("");
      try {
        const wsResponse = await api.get<FieldWorkspace[]>("/field/workspaces");
        const available = wsResponse.data;
        setWorkspaces(available);
        const savedId = Number(localStorage.getItem("field_workspace_id"));
        const active = available.find((item) => item.id === (id ?? savedId)) ?? available[0] ?? null;
        if (!active) {
          setWorkspaceId(null);
          setCases([]);
          setMembers([]);
          setDocs([]);
          return;
        }
        setWorkspaceId(active.id);
        localStorage.setItem("field_workspace_id", String(active.id));
        const [caseResponse, memberResponse, docResponse] = await Promise.all([
          api.get<FieldCase[]>(`/field/cases?workspace_id=${active.id}`),
          api.get<FieldMember[]>(`/field/workspaces/${active.id}/members`),
          api.get<DocItem[]>(`/documents?workspace_id=${active.id}`),
        ]);
        setCases(caseResponse.data);
        setMembers(memberResponse.data);
        setDocs(docResponse.data);
        setSnapshotAt(null);
        setSelectedId((previous) =>
          caseResponse.data.some((item) => item.id === previous) ? previous : (caseResponse.data[0]?.id ?? null),
        );
        if (user) {
          void saveSnapshot(api.defaults.baseURL!, user.id, {
            workspaces: available,
            workspaceId: active.id,
            cases: caseResponse.data,
            members: memberResponse.data,
            docs: docResponse.data,
          });
        }
      } catch (err) {
        // Mất mạng (không có phản hồi): mở bản lưu gần nhất để kỹ thuật viên vẫn xem được ca.
        const offline = !(err as { response?: unknown })?.response;
        const snapshot = offline && user ? await loadSnapshot(api.defaults.baseURL!, user.id, id ?? null) : null;
        if (snapshot) {
          setWorkspaces(snapshot.workspaces);
          setWorkspaceId(snapshot.workspaceId);
          setCases(snapshot.cases);
          setMembers(snapshot.members);
          setDocs(snapshot.docs);
          setSnapshotAt(snapshot.savedAt);
          setSelectedId((previous) =>
            snapshot.cases.some((item) => item.id === previous) ? previous : (snapshot.cases[0]?.id ?? null),
          );
        } else {
          setError(getApiErrorMessage(err));
        }
      } finally {
        setLoading(false);
      }
    },
    [user],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const refreshQueue = useCallback(async () => {
    if (!queueScope) return;
    const [waiting, failed, legacy] = await Promise.all([
      pending(queueScope),
      rejected(queueScope),
      legacyPendingCount(),
    ]);
    setQueued(waiting);
    setRejectedActions(failed);
    setLegacyCount(legacy);
  }, [queueScope]);

  // Capture the session and validate ownership again for every replay: a login switch
  // (new session epoch) must never replay the previous account's queued edits.
  const queueSender = useCallback(() => {
    const epoch = currentSession();
    return (method: "post" | "patch", url: string, body: Record<string, unknown>) => {
      const current = JSON.parse(localStorage.getItem("rag_user") || "null");
      if (!queueScope || !epoch || current?.id !== queueScope.userId || currentSession() !== epoch) {
        return Promise.reject(new Error("Hàng đợi đang chờ đăng nhập lại đúng tài khoản."));
      }
      return api.request({ method, url, data: body });
    };
  }, [queueScope]);

  const syncQueue = useCallback(async () => {
    if (!queueScope) return;
    try {
      const { sent } = await flush(queueScope, queueSender());
      await refreshQueue();
      if (sent > 0) {
        setNotice(`Đã gửi ${sent} thao tác đã lưu.`);
        await load(queueScope.workspaceId);
      }
    } catch (err) {
      setError(getApiErrorMessage(err));
    }
  }, [queueScope, queueSender, refreshQueue, load]);

  useEffect(() => {
    setQueued([]);
    setRejectedActions([]);
    const goOnline = () => {
      setOnline(true);
      void syncQueue();
    };
    const goOffline = () => setOnline(false);
    setOnline(navigator.onLine);
    void syncQueue();
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    const timer = window.setInterval(() => {
      if (navigator.onLine) void syncQueue();
    }, 20000);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
      window.clearInterval(timer);
    };
  }, [syncQueue]);

  async function reviewAction(action: QueuedAction, retry: boolean) {
    if (!queueScope) return;
    if (!retry && !window.confirm("Xóa thao tác bị từ chối khỏi thiết bị?")) return;
    try {
      if (retry) {
        await retryRejected(queueScope, action.id);
        await syncQueue();
      } else {
        await discardRejected(queueScope, action.id);
      }
      await refreshQueue();
    } catch (err) {
      setError(getApiErrorMessage(err));
    }
  }

  // Dòng thông báo tự tắt sau vài giây; nếu tắt ngay khi đồng bộ xong thì người
  // dùng không kịp thấy là thao tác của mình đã lên.
  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(""), 12000);
    return () => window.clearTimeout(t);
  }, [notice]);

  async function createCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.post<FieldCase>(`/field/workspaces/${workspaceId}/cases`, {
        ...form,
        title: form.title.trim(),
        symptom: form.symptom.trim(),
        customer_label: form.customer_label.trim() || null,
        address: form.address.trim() || null,
        device_model: form.device_model.trim() || null,
      });
      setForm(EMPTY_FORM);
      setFormOpen(false);
      await load(workspaceId);
      setSelectedId(response.data.id);
    } catch (err) {
      setError(getApiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function updateStatus(status: FieldStatus) {
    if (!selectedCase) return;
    await queueCase("patch", `/field/cases/${selectedCase.id}`, { status }, `Đổi trạng thái ca #${selectedCase.id}`);
  }

  /**
   * Mọi thay đổi của một ca sửa đi qua đây. Còn mạng thì gửi thẳng; mất sóng thì
   * xếp vào hàng đợi trong máy và gửi lại khi có mạng, theo đúng thứ tự đã làm.
   * Mỗi thao tác mang một mã riêng nên lần gửi lại không tạo thêm bản ghi.
   */
  async function queueCase(method: "post" | "patch", url: string, body: Record<string, unknown>, label: string) {
    if (!queueScope) return false;
    setBusy(true);
    setError("");
    try {
      const { queued } = await send(queueScope, queueSender(), method, url, body, label);
      await refreshQueue();
      if (queued) {
        setNotice(`"${label}" đã lưu trong máy, đang chờ gửi khi kết nối và phiên đăng nhập hoạt động.`);
      } else {
        setNotice("");
        await load(workspaceId);
      }
      return true;
    } catch (err) {
      setError(getApiErrorMessage(err));
      await refreshQueue().catch(() => undefined);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function mutateCase(action: () => Promise<unknown>) {
    if (!workspaceId) return;
    setBusy(true);
    setError("");
    try {
      await action();
      await load(workspaceId);
    } catch (err) {
      setError(getApiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function addNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedCase || !note.trim()) return;
    const detail = note.trim();
    if (await queueCase("post", `/field/cases/${selectedCase.id}/events`, { detail }, "Ghi nhận kết quả")) setNote("");
  }

  async function addStep(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedCase || !stepTitle.trim()) return;
    const title = stepTitle.trim();
    if (await queueCase("post", `/field/cases/${selectedCase.id}/steps`, { title }, `Thêm bước: ${title}`))
      setStepTitle("");
  }

  async function askAssistant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedCase || !question.trim()) return;
    setBusy(true);
    setError("");
    setAnswer("");
    setAnswerEvidence({});
    try {
      const response = await api.post<{ answer: string; sources: string[] } & EvidenceBundle>(
        `/field/cases/${selectedCase.id}/assist`,
        { question: question.trim() },
        // Ollama chạy CPU có thể mất cả phút; mặc định 15s của axios là quá ngắn.
        { timeout: 180000 },
      );
      setAnswerEvidence(response.data);
      setAnswer(
        `${response.data.answer}${
          response.data.sources.length ? `\n\nNguồn: ${response.data.sources.join(", ")}` : ""
        }`,
      );
    } catch (err) {
      setError(getApiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function addMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !memberEmail.trim()) return;
    await mutateCase(() =>
      api.post(`/field/workspaces/${workspaceId}/members`, {
        email: memberEmail.trim(),
      }),
    );
    setMemberEmail("");
  }

  async function createWorkspace() {
    const name = window.prompt("Tên đội mới");
    if (!name?.trim()) return;
    setBusy(true);
    try {
      const response = await api.post<FieldWorkspace>("/field/workspaces", {
        name: name.trim(),
      });
      localStorage.setItem("field_workspace_id", String(response.data.id));
      await load(response.data.id);
    } catch (err) {
      setError(getApiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function uploadKnowledge(file: File | undefined) {
    if (!file || !workspaceId) return;
    const body = new FormData();
    body.append("file", file);
    setBusy(true);
    setError("");
    try {
      await api.post(`/documents/upload?workspace_id=${workspaceId}`, body, {
        headers: { "Content-Type": "multipart/form-data" },
        // Lập chỉ mục file lớn (embedding theo batch có giãn cách) mất vài phút.
        timeout: 600000,
      });
      await load(workspaceId);
    } catch (err) {
      setError(getApiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function assignCase(memberId: string) {
    if (!selectedCase) return;
    await mutateCase(() =>
      api.patch(`/field/cases/${selectedCase.id}`, {
        assigned_to: memberId ? Number(memberId) : null,
      }),
    );
  }

  async function completeStep(stepId: number, done: boolean) {
    if (!selectedCase) return;
    await queueCase(
      "patch",
      `/field/cases/${selectedCase.id}/steps/${stepId}`,
      {
        status: done ? "todo" : "done",
        result_note: done ? null : "Xác nhận hoàn tất tại hiện trường",
      },
      done ? "Bỏ đánh dấu một bước" : "Tick một bước checklist",
    );
  }

  const activeCount = cases.filter((item) => item.status !== "resolved").length;
  const waitingCount = cases.filter((item) => item.status === "waiting").length;
  const completedCount = cases.filter((item) => item.status === "resolved").length;

  return (
    <div className="h-full overflow-y-auto bg-background">
      <div className="mx-auto max-w-[1500px] space-y-5 p-4 md:p-7">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.16em] text-indigo-600">
              <Wrench className="h-4 w-4" /> Hiện trường
            </div>
            <h1 className="mt-2 text-2xl font-bold tracking-tight text-slate-900 md:text-3xl">
              Hỗ trợ xử lý ca tại hiện trường
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-slate-500">
              Tiếp nhận ca tổng đài đã bàn giao, tra quy trình đúng model thiết bị và ghi nhận kết quả xử lý.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={workspaceId ?? ""}
              onChange={(event) => {
                const id = Number(event.target.value);
                localStorage.setItem("field_workspace_id", String(id));
                void load(id);
              }}
              className="h-10 w-full min-w-0 shrink-0 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 sm:w-auto sm:max-w-[220px]"
            >
              {workspaces.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={createWorkspace}
              className="flex h-10 shrink-0 items-center gap-2 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50"
            >
              <Plus className="h-4 w-4" /> Tạo đội
            </button>
            <button
              type="button"
              onClick={() => setFormOpen(true)}
              className="flex h-10 shrink-0 items-center gap-2 whitespace-nowrap rounded-lg bg-indigo-600 px-4 text-sm font-semibold text-white shadow-xs hover:bg-indigo-700"
            >
              <Plus className="h-4 w-4" /> Tạo ca sửa
            </button>
          </div>
        </header>

        {snapshotAt && (
          <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <WifiOff className="h-4 w-4" /> Đang xem bản lưu offline lúc {new Date(snapshotAt).toLocaleString("vi-VN")}.
            Thay đổi sẽ được xếp hàng và gửi khi có mạng.
          </div>
        )}
        {error && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"
          >
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
          </div>
        )}

        {(!online || queued.length > 0 || notice) && (
          <div
            role="status"
            className={`sticky top-0 z-20 flex items-start gap-2 rounded-xl border px-4 py-3 text-sm shadow-xs backdrop-blur-sm ${
              !online || queued.length > 0
                ? "border-amber-300 bg-amber-50/95 text-amber-900"
                : "border-emerald-300 bg-emerald-50/95 text-emerald-900"
            }`}
          >
            <WifiOff className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="flex-1">
              <p className="font-semibold">
                {!online ? "Đang mất kết nối" : queued.length > 0 ? "Còn thao tác chờ gửi" : "Đã đồng bộ"}
              </p>
              <p className="mt-0.5 text-[13px] leading-5">
                {notice ||
                  (queued.length > 0
                    ? `${queued.length} thao tác đang chờ: ${queued.map((q) => q.label).join(" · ")}`
                    : "Thao tác vẫn ghi được; máy sẽ tự gửi lên khi có sóng trở lại.")}
              </p>
            </div>
          </div>
        )}

        {legacyCount > 0 && (
          <p role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            Có {legacyCount} thao tác từ phiên bản cũ chưa xác định được tài khoản gốc. Dữ liệu vẫn được giữ trên máy;
            cần đối chiếu trước khi gửi lại.
          </p>
        )}
        {rejectedActions.length > 0 && (
          <section
            aria-label="Thao tác cần xem lại"
            className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900"
          >
            <p className="font-semibold">{rejectedActions.length} thao tác chưa được máy chủ chấp nhận</p>
            {rejectedActions.map((action) => (
              <div key={action.id} className="mt-3 border-t border-rose-200 pt-3">
                <p className="font-medium">{action.label}</p>
                <p className="mt-1 whitespace-pre-wrap">
                  {typeof action.body.detail === "string" ? action.body.detail : JSON.stringify(action.body)}
                </p>
                <p className="mt-1">{action.error}</p>
                <div className="mt-2 flex gap-4">
                  <button
                    type="button"
                    className="font-semibold underline"
                    onClick={() => void reviewAction(action, true)}
                  >
                    Thử lại
                  </button>
                  <button type="button" className="underline" onClick={() => void reviewAction(action, false)}>
                    Bỏ thao tác
                  </button>
                </div>
              </div>
            ))}
          </section>
        )}

        <section className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <Metric icon={Radio} label="Ca đang hoạt động" value={activeCount} tone="indigo" />
          <Metric icon={Activity} label="Chờ khách hàng" value={waitingCount} tone="amber" />
          <Metric icon={ClipboardCheck} label="Đã hoàn tất" value={completedCount} tone="green" />
          <Metric icon={Users} label="Thành viên đội" value={members.length} tone="blue" />
        </section>

        {formOpen && (
          <form onSubmit={createCase} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="font-semibold text-slate-900">Tạo ca sửa / bàn giao hiện trường</h2>
                <p className="mt-1 text-xs text-slate-500">Ghi lại triệu chứng và những gì tổng đài đã kiểm tra.</p>
              </div>
              <button type="button" onClick={() => setFormOpen(false)} className="text-sm text-slate-500">
                Đóng
              </button>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              <Input
                label="Tiêu đề *"
                value={form.title}
                onChange={(value) => setForm({ ...form, title: value })}
                placeholder="Mất kết nối Internet"
                required
              />
              <Input
                label="Khách hàng"
                value={form.customer_label}
                onChange={(value) => setForm({ ...form, customer_label: value })}
                placeholder="Mã/nhãn khách hàng"
              />
              <Input
                label="Model thiết bị"
                value={form.device_model}
                onChange={(value) => setForm({ ...form, device_model: value })}
                placeholder="ONT / modem"
              />
              <Input
                label="Địa chỉ"
                value={form.address}
                onChange={(value) => setForm({ ...form, address: value })}
                placeholder="Địa chỉ xử lý"
              />
              <label className="text-xs font-semibold text-slate-600 md:col-span-2 xl:col-span-2">
                Triệu chứng và thông tin tổng đài bàn giao *
                <textarea
                  required
                  value={form.symptom}
                  onChange={(event) => setForm({ ...form, symptom: event.target.value })}
                  className="mt-1.5 min-h-20 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-normal outline-hidden focus:border-indigo-400"
                  placeholder="Triệu chứng, bước đã thử, kết quả đo..."
                />
              </label>
            </div>
            <button
              disabled={busy}
              className="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? "Đang tạo..." : "Tạo ca"}
            </button>
          </form>
        )}

        <div className="grid min-h-[560px] gap-5 xl:grid-cols-[minmax(360px,0.9fr)_minmax(520px,1.5fr)]">
          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xs">
            <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
              <div>
                <h2 className="font-semibold text-slate-900">Hàng đợi ca sửa</h2>
                <p className="mt-1 text-xs text-slate-500">{cases.length} ca trong workspace</p>
              </div>
              <button
                type="button"
                onClick={() => void load(workspaceId)}
                className="rounded-lg p-2 text-slate-500 hover:bg-slate-100"
                aria-label="Làm mới"
              >
                <RefreshCw className="h-4 w-4" />
              </button>
            </div>
            <div className="max-h-[690px] overflow-y-auto">
              {loading ? (
                <div className="flex justify-center p-10">
                  <LoaderCircle className="h-5 w-5 animate-spin text-indigo-600" />
                </div>
              ) : cases.length ? (
                cases.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    onClick={() => {
                      setSelectedId(item.id);
                      setAnswer("");
                      setAnswerEvidence({});
                    }}
                    className={`w-full border-b border-slate-100 p-4 text-left transition hover:bg-slate-50 ${selectedId === item.id ? "bg-indigo-50/70" : ""}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 items-start gap-3">
                        <div className="mt-0.5 rounded-lg bg-indigo-100 p-2 text-indigo-700">
                          <Radio className="h-4 w-4" />
                        </div>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-slate-900">{item.title}</p>
                          <p className="mt-1 text-xs text-slate-500">
                            #{item.id} · {item.customer_label || "Khách hàng"}
                          </p>
                        </div>
                      </div>
                      <span
                        className={`shrink-0 rounded-full px-2 py-1 text-[10px] font-semibold ${STATUS_STYLE[item.status]}`}
                      >
                        {STATUS_LABEL[item.status]}
                      </span>
                    </div>
                    <p className="mt-3 line-clamp-2 pl-11 text-xs leading-5 text-slate-600">{item.symptom}</p>
                    <div className="mt-3 flex items-center justify-between pl-11 text-[10px] text-slate-400">
                      <span>{item.device_model || "Chưa xác định thiết bị"}</span>
                      <span>
                        {item.steps.filter((step) => step.status === "done").length}/{item.steps.length} bước
                      </span>
                    </div>
                  </button>
                ))
              ) : (
                <div className="px-6 py-14 text-center">
                  <ClipboardCheck className="mx-auto h-8 w-8 text-slate-300" />
                  <p className="mt-3 text-sm font-semibold text-slate-700">Chưa có ca sửa</p>
                  <p className="mt-1 text-xs text-slate-500">Tạo ca đầu tiên từ thông tin bàn giao.</p>
                </div>
              )}
            </div>
          </section>

          <div className="space-y-5">
            {selectedCase ? (
              <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xs">
                <div className="bg-slate-900 p-5 text-white">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <span className="rounded-full bg-white/10 px-2.5 py-1 text-[10px] font-bold tracking-wide">
                      CA #{selectedCase.id}
                    </span>
                    <select
                      value={selectedCase.status}
                      onChange={(event) => {
                        const next = event.target.value as FieldStatus;
                        if (next === "resolved") setClosing(true);
                        else void updateStatus(next);
                      }}
                      className="rounded-lg border border-white/20 bg-slate-800 px-3 py-2 text-xs text-white"
                    >
                      {Object.entries(STATUS_LABEL).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <h2 className="mt-4 text-xl font-bold">{selectedCase.title}</h2>
                  {/* Serial đã có trong dữ liệu ca nhưng trước đây không hiện ra màn hình.
                      Kỹ thuật viên cần nó để xác nhận đang đứng trước đúng thiết bị —
                      một nhà có thể có hai modem, và model thì trùng nhau. */}
                  <p className="mt-1 text-sm text-slate-300">
                    {selectedCase.customer_label || "Khách hàng"} · {selectedCase.device_model || "Chưa rõ thiết bị"}
                  </p>
                  {selectedCase.serial_number && (
                    <p className="mt-1 text-xs font-mono tracking-wide text-slate-400">
                      S/N {selectedCase.serial_number}
                    </p>
                  )}
                  {selectedCase.address && <p className="mt-2 text-xs text-slate-300">{selectedCase.address}</p>}
                </div>
                {selectedCase.closure && (
                  <div className="px-5 pt-5">
                    <ClosureSummary item={selectedCase} />
                  </div>
                )}
                {closing && (
                  <CloseCaseDialog
                    item={selectedCase}
                    onClose={() => setClosing(false)}
                    onClosed={() => {
                      setClosing(false);
                      void load(workspaceId);
                    }}
                    queue={queueCase}
                  />
                )}
                <div className="grid gap-5 p-5 lg:grid-cols-2">
                  <div className="space-y-5">
                    <div>
                      <div className="flex items-center justify-between">
                        <h3 className="text-sm font-semibold text-slate-900">Thông tin bàn giao</h3>
                        <select
                          value={selectedCase.assigned_to ?? ""}
                          onChange={(event) => void assignCase(event.target.value)}
                          className="max-w-40 rounded-md border border-slate-200 px-2 py-1 text-xs"
                        >
                          <option value="">Chưa phân công</option>
                          {members.map((member) => (
                            <option key={member.user_id} value={member.user_id}>
                              {member.email}
                            </option>
                          ))}
                        </select>
                      </div>
                      <p className="mt-2 rounded-xl bg-slate-50 p-3 text-sm leading-6 text-slate-700">
                        {selectedCase.symptom}
                      </p>
                    </div>
                    <div>
                      <div className="flex items-center justify-between">
                        <h3 className="text-sm font-semibold text-slate-900">Checklist tại hiện trường</h3>
                        <span className="text-[11px] text-slate-500">
                          {selectedCase.steps.filter((step) => step.status === "done").length}/
                          {selectedCase.steps.length} hoàn tất
                        </span>
                      </div>
                      <div className="mt-2 space-y-2">
                        {selectedCase.steps.map((step) => (
                          <button
                            type="button"
                            key={step.id}
                            onClick={() => void completeStep(step.id, step.status === "done")}
                            className="flex w-full items-start gap-2 rounded-lg border border-slate-100 p-3 text-left hover:bg-slate-50"
                          >
                            <span
                              className={`mt-0.5 rounded-full p-1 ${step.status === "done" ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-400"}`}
                            >
                              <Check className="h-3 w-3" />
                            </span>
                            <span className="flex-1">
                              <span
                                className={`block text-xs font-semibold ${step.status === "done" ? "text-slate-400 line-through" : "text-slate-800"}`}
                              >
                                {step.title}
                              </span>
                              {step.instruction && (
                                <span className="mt-1 block text-[11px] leading-4 text-slate-500">
                                  {step.instruction}
                                </span>
                              )}
                              {step.source && (
                                <span className="mt-1 block text-[10px] text-indigo-600">Nguồn: {step.source}</span>
                              )}
                            </span>
                          </button>
                        ))}
                        <form onSubmit={addStep} className="flex gap-2">
                          <input
                            value={stepTitle}
                            onChange={(event) => setStepTitle(event.target.value)}
                            className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-xs"
                            placeholder="Thêm bước checklist..."
                          />
                          <button
                            className="rounded-lg border border-slate-200 px-3 text-slate-600 hover:bg-slate-50"
                            aria-label="Thêm bước"
                          >
                            <Plus className="h-4 w-4" />
                          </button>
                        </form>
                      </div>
                    </div>
                    <CasePhotos
                      caseId={selectedCase.id}
                      currentUserId={user?.id}
                      isOwner={workspaces.find((w) => w.id === workspaceId)?.role === "owner"}
                    />
                    {workspaces.find((w) => w.id === workspaceId)?.role === "owner" && (
                      <>
                        <CaseDetailsEditor
                          item={selectedCase}
                          onSaved={() => void load(workspaceId)}
                          queue={queueCase}
                        />
                        <CaseDispatchEditor item={selectedCase} onSaved={() => void load(workspaceId)} />
                      </>
                    )}
                  </div>
                  <div className="space-y-5">
                    <div className="rounded-xl border border-indigo-100 bg-indigo-50/60 p-4">
                      <div className="flex items-center gap-2 text-sm font-semibold text-indigo-950">
                        <BookOpen className="h-4 w-4" /> Trợ lý theo tri thức đội
                      </div>
                      <p className="mt-1 text-xs leading-5 text-indigo-900/70">
                        Tra cứu tài liệu kỹ thuật gắn với model và triệu chứng của ca này. Trợ lý chỉ đưa gợi ý có căn
                        cứ.
                      </p>
                      <form onSubmit={askAssistant} className="mt-3 flex gap-2">
                        <input
                          value={question}
                          onChange={(event) => setQuestion(event.target.value)}
                          className="min-w-0 flex-1 rounded-lg border border-indigo-100 bg-white px-3 py-2 text-xs outline-hidden focus:border-indigo-400"
                          placeholder="Hỏi về bước kiểm tra tiếp theo..."
                        />
                        <button
                          disabled={busy}
                          className="rounded-lg bg-indigo-600 px-3 text-white disabled:opacity-50"
                          aria-label="Hỏi trợ lý"
                        >
                          <Send className="h-4 w-4" />
                        </button>
                      </form>
                      {answer && (
                        <div className="mt-3 rounded-lg bg-white p-3 text-[13px] leading-6 text-slate-700">
                          <MarkdownContent content={answer} />
                          <EvidencePanel evidence={answerEvidence.evidence} audit={answerEvidence.citation_audit} />
                        </div>
                      )}
                      <p className="mt-2 flex items-center gap-1 text-[10px] text-indigo-900/60">
                        <ShieldAlert className="h-3 w-3" /> Không tự động thao tác thiết bị; kỹ thuật viên xác nhận
                        trước khi thực hiện.
                      </p>
                    </div>

                    <div>
                      <h3 className="text-sm font-semibold text-slate-900">Nhật ký ca sửa</h3>
                      <form onSubmit={addNote} className="mt-2 flex gap-2">
                        <input
                          value={note}
                          onChange={(event) => setNote(event.target.value)}
                          className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-xs"
                          placeholder="Ghi nhận kết quả hiện trường..."
                        />
                        <button
                          className="rounded-lg border border-slate-200 px-3 text-slate-600 hover:bg-slate-50"
                          aria-label="Lưu ghi chú"
                        >
                          <Send className="h-4 w-4" />
                        </button>
                      </form>
                      <div className="mt-3 max-h-52 space-y-3 overflow-y-auto">
                        {[...selectedCase.events].reverse().map((event) => (
                          <div key={event.id} className="flex gap-2">
                            <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-indigo-400" />
                            <div>
                              <p className="text-xs text-slate-700">{event.detail}</p>
                              <EvidencePanel
                                evidence={event.evidence}
                                audit={event.citation_audit}
                                checkVersion={async (reference) =>
                                  (
                                    await api.get(
                                      `/field/cases/${selectedCase.id}/events/${event.id}/evidence/${encodeURIComponent(reference)}`,
                                    )
                                  ).data.document_status
                                }
                              />
                              <p className="mt-1 text-[10px] text-slate-400">{formatDateTime(event.created_at)}</p>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </section>
            ) : (
              <div className="flex min-h-[350px] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white text-center">
                <ChevronRight className="h-8 w-8 text-slate-300" />
                <h2 className="mt-3 text-sm font-semibold text-slate-700">Chọn một ca để xem chi tiết</h2>
                <p className="mt-1 max-w-xs text-xs leading-5 text-slate-500">
                  Thông tin bàn giao, checklist, tri thức và nhật ký sẽ được gom theo từng ca sửa.
                </p>
              </div>
            )}

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <FileText className="h-4 w-4 text-indigo-600" /> Tri thức dùng chung
                  </h2>
                  <p className="mt-1 text-xs text-slate-500">{docs.length} tài liệu trong kho của đội</p>
                </div>
                {workspaces.find((item) => item.id === workspaceId)?.role === "owner" && (
                  <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50">
                    <Plus className="h-3.5 w-3.5" /> Nạp SOP/manual
                    <input
                      className="hidden"
                      type="file"
                      accept=".pdf,.docx,.doc,.txt,.png,.jpg,.jpeg,.webp"
                      onChange={(event) => {
                        void uploadKnowledge(event.target.files?.[0]);
                        event.currentTarget.value = "";
                      }}
                    />
                  </label>
                )}
              </div>
              {docs.length > 0 ? (
                <div className="mt-4 flex flex-wrap gap-2">
                  {docs.map((doc) => (
                    <span
                      key={doc.name}
                      className="inline-flex max-w-full items-center gap-1.5 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-700"
                    >
                      <FileText className="h-3.5 w-3.5 shrink-0 text-indigo-500" />
                      <span className="truncate">{doc.name}</span>
                      <span className="text-slate-400">{doc.chunks}</span>
                    </span>
                  ))}
                </div>
              ) : (
                <p className="mt-4 rounded-lg bg-slate-50 p-3 text-xs text-slate-500">
                  Nạp tài liệu thao tác để trợ lý tìm đúng hướng dẫn. Nội dung sẽ được chia sẻ với các thành viên trong
                  đội.
                </p>
              )}
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <Users className="h-4 w-4 text-indigo-600" /> Đội kỹ thuật
                  </h2>
                  <p className="mt-1 text-xs text-slate-500">{members.length} thành viên trong đội</p>
                </div>
                <Activity className="h-4 w-4 text-slate-300" />
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {members.map((member) => (
                  <span
                    key={member.user_id}
                    className="inline-flex items-center gap-1.5 rounded-full bg-slate-50 px-3 py-1.5 text-[11px] text-slate-700"
                  >
                    <CircleUserRound className="h-3.5 w-3.5 text-indigo-500" /> {member.email}{" "}
                    <span className="text-slate-400">{member.role}</span>
                  </span>
                ))}
              </div>
              {workspaces.find((item) => item.id === workspaceId)?.role === "owner" && (
                <form onSubmit={addMember} className="mt-4 flex gap-2 border-t border-slate-100 pt-4">
                  <input
                    type="email"
                    required
                    value={memberEmail}
                    onChange={(event) => setMemberEmail(event.target.value)}
                    className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-xs"
                    placeholder="Email tài khoản đã đăng ký"
                  />
                  <button className="rounded-lg bg-slate-900 px-3 text-xs font-semibold text-white">
                    Thêm thành viên
                  </button>
                </form>
              )}
            </section>
            {busy && (
              <p className="flex items-center gap-2 text-xs text-slate-500">
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> Đang lưu thay đổi...
              </p>
            )}
            <p className="flex items-center gap-1 text-[10px] text-slate-400">
              <MessageSquareText className="h-3 w-3" /> Bổ sung cho quy trình tổng đài hiện có, không thay thế; dữ liệu
              demo là giả lập.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: typeof Activity;
  label: string;
  value: number;
  tone: "indigo" | "amber" | "green" | "blue";
}) {
  const tones = {
    indigo: "bg-indigo-50 text-indigo-700",
    amber: "bg-amber-50 text-amber-700",
    green: "bg-emerald-50 text-emerald-700",
    blue: "bg-sky-50 text-sky-700",
  };
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-slate-500">{label}</span>
        <span className={`rounded-lg p-2 ${tones[tone]}`}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <p className="mt-3 text-2xl font-bold text-slate-900">{value}</p>
    </div>
  );
}

function Input({
  label,
  value,
  onChange,
  placeholder,
  required,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  required?: boolean;
}) {
  return (
    <label className="text-xs font-semibold text-slate-600">
      {label}
      <input
        required={required}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1.5 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm font-normal outline-hidden focus:border-indigo-400"
        placeholder={placeholder}
      />
    </label>
  );
}
