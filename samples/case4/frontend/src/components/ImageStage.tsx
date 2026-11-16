import { Camera, ImagePlus, ScanLine } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button, cx } from "./ui";

interface ImageStageProps {
  imageUrl?: string;
  title: string;
  description: string;
  active?: boolean;
  error?: boolean;
  onUpload?: () => void;
  onCamera?: () => void;
  className?: string;
  objectFit?: "contain" | "cover";
  boxes?: Array<{index: number; bboxXyxy?: [number, number, number, number]; detectedPalmSide?: string; palmSide?: string}>;
  countdown?: number;
  palmSideLabel?: string;
}

export function ImageStage({
  imageUrl,
  title,
  description,
  active = false,
  error = false,
  onUpload,
  onCamera,
  className,
  objectFit = "contain",
  boxes = [],
  countdown,
  palmSideLabel,
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
  const imageLeft = (stageSize.width - imageSize.width * scale) / 2;
  const imageTop = (stageSize.height - imageSize.height * scale) / 2;
  return (
    <div ref={stageRef} style={{aspectRatio: imageSize.width && imageSize.height ? `${imageSize.width} / ${imageSize.height}` : "16 / 9"}} className={cx("image-stage", !imageUrl && "image-stage--empty-state", active && "image-stage--active", className)}>
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
      {imageSize.width > 0 && boxes.map((box) => box.bboxXyxy ? <div
        key={box.index}
        className={cx("image-stage__box", (box.detectedPalmSide || box.palmSide) === "right" && "image-stage__box--right")}
        style={{left: imageLeft + box.bboxXyxy[0] * scale, top: imageTop + box.bboxXyxy[1] * scale,
          width: (box.bboxXyxy[2] - box.bboxXyxy[0]) * scale,
          height: (box.bboxXyxy[3] - box.bboxXyxy[1]) * scale}}
        aria-label={`${(box.detectedPalmSide || box.palmSide) === "right" ? "右掌" : (box.detectedPalmSide || box.palmSide) === "left" ? "左掌" : "手掌"} ${box.index}`}
      ><span>{(box.detectedPalmSide || box.palmSide) === "right" ? "右" : (box.detectedPalmSide || box.palmSide) === "left" ? "左" : box.index}</span></div> : null)}
      {countdown !== undefined && countdown > 0 ? <div className="image-stage__countdown" role="status" aria-label={`采集倒计时 ${countdown} 秒`}>{countdown}</div> : null}
      <div className="image-stage__overlay image-stage__overlay--top"><span>{palmSideLabel || (active ? "实时帧" : "输入画面")}</span><span className={error ? "stage-error" : active ? "live-dot" : ""}>{error ? "ERROR" : active ? "LIVE" : "READY"}</span></div>
      <div className="image-stage__overlay image-stage__overlay--bottom"><span>128 × 128 ROI</span><span>质量检查</span></div>
    </div>
  );
}
