/**
 * Hàng đợi thao tác khi mất sóng.
 *
 * Kỹ thuật viên làm việc ở nơi sóng chập chờn: tick xong một bước checklist rồi
 * mất mạng ngay thì thao tác đó không được phép biến mất. Mọi thay đổi vì vậy đi
 * qua `send()`: gửi thẳng khi còn mạng, còn khi hỏng mạng thì xếp vào IndexedDB
 * của trình duyệt và tự gửi lại khi kết nối trở lại, theo đúng thứ tự đã làm.
 *
 * Gửi lại an toàn được là nhờ phía máy chủ: mỗi thao tác mang một `client_action_id`
 * sinh một lần lúc người dùng bấm, và máy chủ đối chiếu id đó cùng mã băm nội dung
 * yêu cầu (xem backend/field/router.py::_claim_client_action). Gửi hai lần thì lần
 * sau không tạo thêm bản ghi — nên việc gửi lại cả hàng đợi không bao giờ nhân đôi
 * một ghi chú hay một bước checklist.
 *
 * Phạm vi: đây là hàng đợi trong trình duyệt, không phải trong bộ nhớ mã hoá của
 * hệ điều hành. Nó sống qua việc đóng tab và tắt máy, nhưng nằm trong vùng dữ liệu
 * của trình duyệt chứ không phải Keychain hay Keystore.
 */

export type QueuedAction = {
  id: string;
  method: "post" | "patch";
  url: string;
  body: Record<string, unknown>;
  label: string;
  createdAt: number;
};

const DB_NAME = "fieldops";
const STORE = "pending";
const VERSION = 1;

function supported(): boolean {
  return typeof window !== "undefined" && "indexedDB" in window;
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        // khoá chính là createdAt để đọc ra đúng thứ tự đã thao tác
        req.result.createObjectStore(STORE, { keyPath: "id" }).createIndex("seq", "createdAt");
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  return new Promise<T>((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const req = run(t.objectStore(STORE));
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
    t.oncomplete = () => db.close();
  });
}

export function newActionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `a-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export async function pending(): Promise<QueuedAction[]> {
  if (!supported()) return [];
  try {
    const all = await tx<QueuedAction[]>("readonly", (s) => s.getAll() as IDBRequest<QueuedAction[]>);
    return all.sort((a, b) => a.createdAt - b.createdAt);
  } catch {
    return [];
  }
}

async function put(action: QueuedAction): Promise<void> {
  if (!supported()) return;
  try {
    await tx("readwrite", (s) => s.put(action) as IDBRequest<IDBValidKey>);
  } catch {
    /* hết chỗ lưu hoặc trình duyệt chặn: thao tác vẫn báo lỗi ở lớp trên */
  }
}

async function drop(id: string): Promise<void> {
  if (!supported()) return;
  try {
    await tx("readwrite", (s) => s.delete(id) as unknown as IDBRequest<undefined>);
  } catch {
    /* ignore */
  }
}

/** Lỗi mạng (mất sóng, server không trả lời) khác hẳn lỗi nghiệp vụ (400/403/409). */
function isNetworkFailure(err: unknown): boolean {
  if (typeof navigator !== "undefined" && navigator.onLine === false) return true;
  const e = err as { response?: unknown; code?: string };
  if (e?.response) return false; // máy chủ đã trả lời → không phải mất mạng
  return e?.code === "ERR_NETWORK" || e?.code === "ECONNABORTED" || !e?.response;
}

type Sender = (method: "post" | "patch", url: string, body: Record<string, unknown>) => Promise<unknown>;

/**
 * Gửi một thao tác. Trả về `queued: true` nếu nó được xếp lại để gửi sau.
 * Lỗi nghiệp vụ (ca đã đóng, không đủ quyền) vẫn ném ra như cũ: xếp hàng rồi
 * gửi lại một thao tác mà máy chủ đã từ chối thì chỉ lặp lại lỗi đó mãi.
 */
export async function send(
  sender: Sender,
  method: "post" | "patch",
  url: string,
  body: Record<string, unknown>,
  label: string
): Promise<{ queued: boolean }> {
  const action: QueuedAction = {
    id: newActionId(),
    method,
    url,
    label,
    createdAt: Date.now(),
    body: { ...body, client_action_id: body.client_action_id ?? newActionId() },
  };
  try {
    await sender(method, url, action.body);
    return { queued: false };
  } catch (err) {
    if (!isNetworkFailure(err)) throw err;
    await put(action);
    return { queued: true };
  }
}

/** Gửi lại hàng đợi theo đúng thứ tự. Dừng ngay khi mạng lại hỏng. */
export async function flush(sender: Sender): Promise<{ sent: number; left: number }> {
  const queue = await pending();
  let sent = 0;
  for (const action of queue) {
    try {
      await sender(action.method, action.url, action.body);
      await drop(action.id);
      sent += 1;
    } catch (err) {
      if (isNetworkFailure(err)) break; // vẫn mất mạng: giữ nguyên phần còn lại
      // máy chủ từ chối hẳn thao tác này — bỏ ra khỏi hàng đợi, không lặp vô hạn
      await drop(action.id);
    }
  }
  return { sent, left: (await pending()).length };
}
