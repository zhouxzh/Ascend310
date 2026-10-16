import { Camera, ImagePlus, ScanLine } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { RoiGuideConfig } from "../lib/types";
import { Button, cx } from "./ui";

interface ImageStageProps {
  imageUrl?: string;
  title: string;
  description: string;
  active?: boolean;
  onUpload?: () => void;
  onCamera?: () => void;
  className?: string;
  objectFit?: "contain" | "cover";
  guide?: boolean;
  guideConfig?: RoiGuideConfig;
}

export function ImageStage({
  imageUrl,
  title,
  description,
  active = false,
  onUpload,
  onCamera,
  className,
  objectFit = "contain",
  guide = false,
  guideConfig = {center_x: 0.5, center_y: 0.5, side_ratio: 0.9},
}: ImageStageProps) {
  const stageRef = useRef<HTMLDivElement>(null);
  const [stageSize, setStageSize] = useState({width: 0, height: 0});
  const [imageSize, setImageSize] = useState({width: 0, height: 0});
  useEffect(() => {
    if (!stageRef.current) return;
    const observer = new ResizeObserver(([entry]) => setStageSize({width: entry.contentRect.width, height: entry.contentRect.height}));
    observer.observe(stageRef.current);
    return () => observer.disconnect();
  }, []);
  const scale = Math.min(stageSize.width / (imageSize.width || 1), stageSize.height / (imageSize.height || 1));
  const side = Math.min(imageSize.width, imageSize.height) * guideConfig.side_ratio * scale;
  const guideStyle = {
    width: side,
    height: side,
    left: (stageSize.width - imageSize.width * scale) / 2 + imageSize.width * scale * guideConfig.center_x,
    top: (stageSize.height - imageSize.height * scale) / 2 + imageSize.height * scale * guideConfig.center_y,
  };
  return (
    <div ref={stageRef} className={cx("image-stage", !imageUrl && "image-stage--empty-state", active && "image-stage--active", className)}>
      {imageUrl ? (
        <img src={imageUrl} alt={title} onLoad={(event) => {const image = event.currentTarget; setImageSize({width: image.naturalWidth, height: image.naturalHeight});}} className={`image-stage__image image-stage__image--${objectFit}`} />
      ) : (
        <div className="image-stage__empty">
          <span className="image-stage__scan" aria-hidden="true"><ScanLine /></span>
          <strong>{title}</strong>
          <p>{description}</p>
          {(onUpload || onCamera) && (
            <div className="image-stage__actions">
              {onUpload ? <Button variant="secondary" icon={ImagePlus} onClick={onUpload}>选择图像</Button> : null}
              {onCamera ? <Button variant="secondary" icon={Camera} onClick={onCamera}>打开摄像头</Button> : null}
            </div>
          )}
        </div>
      )}
      {guide && imageSize.width > 0 ? <div style={guideStyle} className="image-stage__guide" aria-hidden="true"><span>掌心放入此框</span></div> : null}
      <div className="image-stage__overlay image-stage__overlay--top"><span>{active ? "实时帧" : "输入画面"}</span><span className={active ? "live-dot" : ""}>{active ? "LIVE" : "READY"}</span></div>
      <div className="image-stage__overlay image-stage__overlay--bottom"><span>128 × 128 ROI</span><span>质量检查</span></div>
    </div>
  );
}
