import { CalendarClock, ImageOff } from "lucide-react";
import { getUploadUrl } from "../api";
import type { AttendanceRecord } from "../types";

interface RecordTableProps {
  records: AttendanceRecord[];
  compact?: boolean;
  emptyLabel?: string;
}

function formatTimestamp(value?: string | null): string {
  if (!value) return "--";
  const parsed = new Date(value.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

function recordType(value?: string | null): string {
  if (value === "camera_auto") return "设备自动";
  if (value === "manual") return "手动打卡";
  return value || "未知";
}

export function RecordTable({ records, compact = false, emptyLabel = "暂无今日记录" }: RecordTableProps) {
  return (
    <div className={`records-table-wrap${compact ? " records-table-wrap--compact" : ""}`}>
      {records.length === 0 ? (
        <div className="empty-state" role="status">
          <CalendarClock size={26} aria-hidden="true" />
          <span>{emptyLabel}</span>
        </div>
      ) : (
        <table className="records-table">
          <thead>
            <tr>
              <th scope="col">时间</th>
              <th scope="col">人员</th>
              <th scope="col">方式</th>
              <th scope="col">图像</th>
            </tr>
          </thead>
          <tbody>
            {records.map((record) => {
              const image = getUploadUrl(record.image_path);
              return (
                <tr key={`${record.id ?? "record"}-${record.timestamp ?? ""}`}>
                  <td className="record-time">{formatTimestamp(record.timestamp)}</td>
                  <td className="record-name">{record.name || "未命名"}</td>
                  <td><span className="type-chip">{recordType(record.type)}</span></td>
                  <td>
                    {image ? (
                      <img className="record-avatar" src={image} alt={`${record.name || "人员"}考勤图像`} />
                    ) : (
                      <span className="record-avatar record-avatar--empty" title="无图像">
                        <ImageOff size={16} aria-hidden="true" />
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

export { formatTimestamp, recordType };
