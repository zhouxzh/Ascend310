import type {
  ApiClient,
  AttendanceRecord,
  CaptureResult,
  ClockInInput,
  ClockInResult,
  HealthStatus,
  User,
  UserFormInput,
} from "./types";

type JsonObject = Record<string, unknown>;
type FetchLike = typeof fetch;

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const asObject = (value: unknown): JsonObject =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as JsonObject) : {};

const asArray = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

const stringValue = (value: unknown, fallback = ""): string =>
  typeof value === "string" || typeof value === "number" ? String(value) : fallback;

const numberValue = (value: unknown, fallback = 0): number => {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : fallback;
};

const parseUser = (value: unknown): User => {
  const object = asObject(value);
  return {
    id: numberValue(object.id),
    name: stringValue(object.name, "未命名"),
    avatar: stringValue(object.avatar) || null,
    created_at: stringValue(object.created_at) || null,
  };
};

const parseRecord = (value: unknown): AttendanceRecord => {
  const object = asObject(value);
  return {
    id: numberValue(object.id),
    user_id: numberValue(object.user_id),
    name: stringValue(object.name) || null,
    timestamp: stringValue(object.timestamp) || null,
    type: stringValue(object.type) || null,
    image_path: stringValue(object.image_path) || null,
  };
};

const parseError = async (response: Response): Promise<string> => {
  try {
    const body = asObject(await response.json());
    return stringValue(body.error || body.detail, response.statusText || "请求失败");
  } catch {
    return response.statusText || "请求失败";
  }
};

const getDefaultFetch = (): FetchLike => {
  if (typeof window !== "undefined") return window.fetch.bind(window);
  return fetch;
};

async function requestJson<T>(fetcher: FetchLike, path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetcher(path, {
      credentials: "same-origin",
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...init?.headers,
      },
    });
  } catch (error) {
    throw new ApiError(error instanceof Error ? error.message : "无法连接到服务");
  }
  if (!response.ok) throw new ApiError(await parseError(response), response.status);
  if (response.status === 204) return {} as T;
  return (await response.json()) as T;
}

const listFromPayload = (payload: unknown, key: string): unknown[] => {
  if (Array.isArray(payload)) return payload;
  const object = asObject(payload);
  if (Array.isArray(object[key])) return object[key] as unknown[];
  if (object.data && Array.isArray(object.data)) return object.data as unknown[];
  return [];
};

export function createApiClient(fetcher: FetchLike = getDefaultFetch()): ApiClient {
  return {
    videoFeedUrl: "/video_feed",

    async health() {
      const payload = await requestJson<unknown>(fetcher, `/api/health?t=${Date.now()}`);
      const object = asObject(payload);
      return {
        status: object.status === "ok" ? "ok" : "degraded",
        ready: object.ready === true,
        camera_ready: object.camera_ready === true,
        error: stringValue(object.error) || null,
      } satisfies HealthStatus;
    },

    async listUsers() {
      const payload = await requestJson<unknown>(fetcher, `/api/users?t=${Date.now()}`);
      return listFromPayload(payload, "users").map(parseUser);
    },

    async addUser(input: UserFormInput) {
      const form = new FormData();
      form.append("name", input.name);
      if (input.image) form.append("image", input.image);
      if (input.tempPath) form.append("temp_path", input.tempPath);
      if (input.imageBase64) form.append("image_base64", input.imageBase64);
      return requestJson(fetcher, "/api/users", { method: "POST", body: form });
    },

    async updateUser(id, name) {
      return requestJson(fetcher, `/api/users/${id}`, {
        method: "PUT",
        body: JSON.stringify({ name }),
      });
    },

    async deleteUser(id) {
      return requestJson(fetcher, `/api/users/${id}`, { method: "DELETE" });
    },

    async captureDevice() {
      return requestJson<CaptureResult>(fetcher, "/api/camera/capture", { method: "POST" });
    },

    async clockIn(input: ClockInInput) {
      const form = new FormData();
      if (input.image) form.append("image", input.image);
      if (input.imageBase64) form.append("image_base64", input.imageBase64);
      return requestJson<ClockInResult>(fetcher, "/api/clockin", { method: "POST", body: form });
    },

    async listAttendance() {
      const payload = await requestJson<unknown>(fetcher, "/api/attendance");
      return listFromPayload(payload, "records").map(parseRecord);
    },
  };
}

const svgDataUri = (label: string, background: string, foreground: string): string => {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480"><rect width="640" height="480" fill="${background}"/><circle cx="320" cy="190" r="74" fill="${foreground}" opacity=".24"/><path d="M185 420c18-98 76-144 135-144s117 46 135 144" fill="${foreground}" opacity=".24"/><text x="320" y="452" text-anchor="middle" fill="${foreground}" font-family="Arial,sans-serif" font-size="24">${label}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
};

const fakeImage = svgDataUri("CASE 1 / FAKE FRAME", "#e8f1f4", "#18324b");
const fakeNow = (): string => {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
};

export function createFakeApi(): ApiClient {
  let nextUserId = 3;
  let nextRecordId = 3;
  let users: User[] = [
    { id: 1, name: "林晓", avatar: fakeImage, created_at: fakeNow() },
    { id: 2, name: "周宁", avatar: fakeImage, created_at: fakeNow() },
  ];
  let records: AttendanceRecord[] = [
    { id: 2, user_id: 2, name: "周宁", timestamp: fakeNow(), type: "camera_auto", image_path: fakeImage },
    { id: 1, user_id: 1, name: "林晓", timestamp: fakeNow(), type: "manual", image_path: fakeImage },
  ];

  return {
    videoFeedUrl: fakeImage,

    async health() {
      return { status: "ok", ready: true, camera_ready: true, error: null } satisfies HealthStatus;
    },

    async listUsers() {
      return users.map((user) => ({ ...user }));
    },

    async addUser(input) {
      if (!input.name.trim()) return { success: false, error: "姓名不能为空" };
      const avatar = input.imageBase64 || fakeImage;
      const user: User = {
        id: nextUserId++,
        name: input.name.trim(),
        avatar,
        created_at: fakeNow(),
      };
      users = [...users, user];
      return { success: true, user_id: user.id };
    },

    async updateUser(id, name) {
      if (!name.trim()) return { success: false, error: "姓名不能为空" };
      users = users.map((user) => (user.id === id ? { ...user, name: name.trim() } : user));
      records = records.map((record) => (record.user_id === id ? { ...record, name: name.trim() } : record));
      return { success: true };
    },

    async deleteUser(id) {
      users = users.filter((user) => user.id !== id);
      return { success: true };
    },

    async captureDevice() {
      return { success: true, temp_path: "fake-capture.jpg", preview_url: fakeImage };
    },

    async clockIn() {
      const user = users[0];
      if (!user) return { success: true, match: false, similarity: 0 };
      const record: AttendanceRecord = {
        id: nextRecordId++,
        user_id: user.id,
        name: user.name,
        timestamp: fakeNow(),
        type: "manual",
        image_path: fakeImage,
      };
      records = [record, ...records];
      return { success: true, match: true, user: user.name, similarity: 0.93 };
    },

    async listAttendance() {
      return records.map((record) => ({ ...record }));
    },
  };
}

export function getUploadUrl(path?: string | null): string {
  if (!path) return "";
  if (/^(data:|blob:|https?:\/\/)/i.test(path)) return path;
  return `/uploads/${path.split("/").map(encodeURIComponent).join("/")}`;
}

export function getDefaultApi(): ApiClient {
  const fakeQuery = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("fake") === "1";
  const fakeEnv = import.meta.env.VITE_FAKE_API === "1";
  return fakeQuery || fakeEnv ? createFakeApi() : createApiClient();
}
