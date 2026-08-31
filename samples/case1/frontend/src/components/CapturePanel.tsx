import {
  Camera,
  CameraOff,
  Check,
  ImagePlus,
  MonitorSmartphone,
  RefreshCw,
  Upload,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ApiClient } from "../types";

export type CaptureMode = "device" | "browser" | "upload";

export interface CaptureValue {
  mode: CaptureMode;
  previewUrl: string;
  file?: File;
  tempPath?: string;
  imageBase64?: string;
}

interface CapturePanelProps {
  api: ApiClient;
  value: CaptureValue | null;
  onChange: (value: CaptureValue | null) => void;
  resetToken?: number;
  allowUpload?: boolean;
  allowDevice?: boolean;
  allowBrowser?: boolean;
  title?: string;
}

function filePreview(file: File): string {
  return URL.createObjectURL(file);
}

export function CapturePanel({
  api,
  value,
  onChange,
  resetToken = 0,
  allowUpload = true,
  allowDevice = true,
  allowBrowser = true,
  title = "采集图像",
}: CapturePanelProps) {
  const initialMode: CaptureMode = allowUpload ? "upload" : allowDevice ? "device" : "browser";
  const [mode, setMode] = useState<CaptureMode>(initialMode);
  const [cameraError, setCameraError] = useState("");
  const [capturing, setCapturing] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const objectPreviewRef = useRef<string | null>(null);

  const releaseObjectPreview = () => {
    const objectUrl = objectPreviewRef.current;
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectPreviewRef.current = null;
    }
  };

  const stopBrowserCamera = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  };

  useEffect(() => {
    releaseObjectPreview();
    onChange(null);
    setCameraError("");
    setMode(initialMode);
  }, [allowDevice, allowUpload, initialMode, onChange, resetToken]);

  useEffect(() => () => {
    stopBrowserCamera();
    releaseObjectPreview();
  }, []);

  useEffect(() => {
    if (!value || value.previewUrl !== objectPreviewRef.current) releaseObjectPreview();
  }, [value]);

  useEffect(() => {
    if (mode !== "browser") {
      stopBrowserCamera();
      return;
    }
    let cancelled = false;
    setCameraError("");
    const start = async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        setCameraError("当前浏览器不支持摄像头访问");
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: true });
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play().catch(() => undefined);
        }
      } catch {
        setCameraError("无法打开浏览器摄像头，请检查权限");
      }
    };
    void start();
    return () => {
      cancelled = true;
      stopBrowserCamera();
    };
  }, [mode]);

  const selectMode = (nextMode: CaptureMode) => {
    setMode(nextMode);
    releaseObjectPreview();
    onChange(null);
  };

  const captureDevice = async () => {
    setCapturing(true);
    setCameraError("");
    try {
      const result = await api.captureDevice();
      if (!result.success || !result.temp_path) {
        setCameraError(result.error || "设备抓拍失败");
        return;
      }
      onChange({
        mode: "device",
        tempPath: result.temp_path,
        previewUrl: result.preview_url || `/uploads/${encodeURIComponent(result.temp_path)}`,
      });
    } catch (error) {
      setCameraError(error instanceof Error ? error.message : "设备抓拍失败");
    } finally {
      setCapturing(false);
    }
  };

  const captureBrowser = () => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !streamRef.current) {
      setCameraError("浏览器摄像头尚未就绪");
      return;
    }
    const width = video.videoWidth || 640;
    const height = video.videoHeight || 480;
    canvas.width = width;
    canvas.height = height;
    canvas.getContext("2d")?.drawImage(video, 0, 0, width, height);
    const imageBase64 = canvas.toDataURL("image/jpeg", 0.9);
    onChange({ mode: "browser", imageBase64, previewUrl: imageBase64 });
    stopBrowserCamera();
  };

  const uploadFile = (file?: File) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setCameraError("请选择图像文件");
      return;
    }
    releaseObjectPreview();
    const previewUrl = filePreview(file);
    objectPreviewRef.current = previewUrl;
    onChange({ mode: "upload", file, previewUrl });
    setCameraError("");
  };

  const isCaptured = Boolean(value);

  return (
    <section className="capture-panel" aria-label={title}>
      <div className="capture-panel__heading">
        <div>
          <p className="eyebrow">IMAGE INPUT</p>
          <h3>{title}</h3>
        </div>
        {isCaptured ? <span className="success-mark"><Check size={16} aria-hidden="true" />已就绪</span> : null}
      </div>

      <div className="mode-tabs" role="tablist" aria-label="图像来源">
        {allowUpload ? (
          <button
            type="button"
            role="tab"
            aria-selected={mode === "upload"}
            className={mode === "upload" ? "mode-tab is-active" : "mode-tab"}
            onClick={() => selectMode("upload")}
          >
            <Upload size={17} aria-hidden="true" />上传照片
          </button>
        ) : null}
        {allowDevice ? (
          <button
            type="button"
            role="tab"
            aria-selected={mode === "device"}
            className={mode === "device" ? "mode-tab is-active" : "mode-tab"}
            onClick={() => selectMode("device")}
          >
            <MonitorSmartphone size={17} aria-hidden="true" />设备摄像头
          </button>
        ) : null}
        {allowBrowser ? (
          <button
            type="button"
            role="tab"
            aria-selected={mode === "browser"}
            className={mode === "browser" ? "mode-tab is-active" : "mode-tab"}
            onClick={() => selectMode("browser")}
          >
            <Camera size={17} aria-hidden="true" />浏览器摄像头
          </button>
        ) : null}
      </div>

      <div className="capture-stage">
        {value ? (
          <div className="capture-stage__preview">
            <img src={value.previewUrl} alt="已采集的人脸图像" />
            <span className="capture-stage__badge"><Check size={15} aria-hidden="true" />已采集</span>
          </div>
        ) : mode === "upload" ? (
          <label className="upload-dropzone">
            <ImagePlus size={30} aria-hidden="true" />
            <strong>选择图像文件</strong>
            <span>PNG、JPEG</span>
            <input
              aria-label="选择图像文件"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(event) => uploadFile(event.target.files?.[0])}
            />
          </label>
        ) : mode === "device" && allowDevice ? (
          <div className="capture-stage__device">
            <img src={api.videoFeedUrl} alt="设备摄像头预览" />
            <div className="capture-stage__overlay">设备实时预览</div>
          </div>
        ) : allowBrowser ? (
          <div className="capture-stage__browser">
            <video ref={videoRef} autoPlay muted playsInline aria-label="浏览器摄像头预览" />
            <canvas ref={canvasRef} className="visually-hidden" />
            {cameraError ? <div className="capture-stage__error"><CameraOff size={21} aria-hidden="true" />{cameraError}</div> : null}
          </div>
        ) : null}
      </div>

      <div className="capture-actions">
        {value ? (
          <button type="button" className="button button--secondary" onClick={() => onChange(null)}>
            <RefreshCw size={18} aria-hidden="true" />重新采集
          </button>
        ) : mode === "device" && allowDevice ? (
          <button type="button" className="button button--primary" onClick={() => void captureDevice()} disabled={capturing}>
            <Camera size={18} aria-hidden="true" />{capturing ? "抓取中" : "抓取画面"}
          </button>
        ) : mode === "browser" && allowBrowser ? (
          <button type="button" className="button button--primary" onClick={captureBrowser}>
            <Camera size={18} aria-hidden="true" />拍摄当前画面
          </button>
        ) : null}
        {cameraError && mode !== "browser" ? <span className="inline-error" role="alert">{cameraError}</span> : null}
      </div>
    </section>
  );
}
