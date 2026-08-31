import { Camera, ImagePlus, ScanLine } from "lucide-react";
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
}: ImageStageProps) {
  return (
    <div className={cx("image-stage", !imageUrl && "image-stage--empty-state", active && "image-stage--active", className)}>
      {imageUrl ? (
        <img src={imageUrl} alt={title} className={`image-stage__image image-stage__image--${objectFit}`} />
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
      <div className="image-stage__overlay image-stage__overlay--top"><span>{active ? "实时帧" : "输入画面"}</span><span className={active ? "live-dot" : ""}>{active ? "LIVE" : "READY"}</span></div>
      <div className="image-stage__overlay image-stage__overlay--bottom"><span>128 × 128 ROI</span><span>质量检查</span></div>
    </div>
  );
}
