import { CameraOff, Radio } from "lucide-react";
import { useEffect, useState } from "react";

interface LiveFeedProps {
  src: string;
  title?: string;
  compact?: boolean;
}

export function LiveFeed({ src, title = "设备视频流", compact = false }: LiveFeedProps) {
  const [failed, setFailed] = useState(false);

  useEffect(() => setFailed(false), [src]);

  return (
    <section className={`feed-panel${compact ? " feed-panel--compact" : ""}`} aria-label={title}>
      <header className="feed-panel__header">
        <div className="feed-panel__title">
          <Radio size={18} aria-hidden="true" />
          <span>{title}</span>
        </div>
        <span className={`feed-panel__state${failed ? " feed-panel__state--muted" : ""}`}>
          {failed ? "不可用" : "MJPEG"}
        </span>
      </header>
      <div className="feed-panel__viewport">
        {failed ? (
          <div className="feed-panel__empty" role="status">
            <CameraOff size={34} aria-hidden="true" />
            <strong>视频流不可用</strong>
            <span>请检查设备摄像头或服务状态</span>
          </div>
        ) : (
          <img
            src={src}
            alt="设备摄像头实时画面"
            onError={() => setFailed(true)}
          />
        )}
      </div>
    </section>
  );
}
