export type PageKey = "dashboard" | "users" | "attendance";

export interface User {
  id: number;
  name: string;
  avatar?: string | null;
  created_at?: string | null;
}

export interface AttendanceRecord {
  id?: number;
  user_id?: number;
  name?: string | null;
  timestamp?: string | null;
  type?: string | null;
  image_path?: string | null;
}

export interface CaptureResult {
  success: boolean;
  temp_path?: string;
  preview_url?: string;
  error?: string;
}

export interface ClockInResult {
  success: boolean;
  match?: boolean;
  user?: string;
  similarity?: number;
  error?: string;
}

export interface HealthStatus {
  status: "ok" | "degraded";
  ready: boolean;
  camera_ready: boolean;
  error?: string | null;
}

export interface UserFormInput {
  name: string;
  image?: File;
  tempPath?: string;
  imageBase64?: string;
}

export interface ClockInInput {
  image?: File;
  imageBase64?: string;
}

export interface ApiClient {
  readonly videoFeedUrl: string;
  health?(): Promise<HealthStatus>;
  listUsers(): Promise<User[]>;
  addUser(input: UserFormInput): Promise<{ success: boolean; user_id?: number; error?: string }>;
  updateUser(id: number, name: string): Promise<{ success: boolean; error?: string }>;
  deleteUser(id: number): Promise<{ success: boolean; error?: string }>;
  captureDevice(): Promise<CaptureResult>;
  clockIn(input: ClockInInput): Promise<ClockInResult>;
  listAttendance(): Promise<AttendanceRecord[]>;
}
