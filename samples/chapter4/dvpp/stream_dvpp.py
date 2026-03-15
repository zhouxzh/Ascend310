import cv2
import subprocess
import time
import socket
import sys
import threading
from collections import deque

from utils.video_encoder import VideoEncoder, bgr_to_nv12


class StreamStats:
    def __init__(self, window_size=120):
        self.window_size = window_size
        self.samples = {}
        self.start_time = time.perf_counter()
        self.frame_count = 0
        self.bytes_to_ffmpeg = 0

    def add_sample(self, name, value):
        bucket = self.samples.setdefault(name, deque(maxlen=self.window_size))
        bucket.append(value)

    def average(self, name):
        bucket = self.samples.get(name)
        if not bucket:
            return 0.0
        return sum(bucket) / len(bucket)

    def add_pipe_bytes(self, byte_count):
        self.bytes_to_ffmpeg += byte_count

    def finish_frame(self, loop_ms):
        self.frame_count += 1
        self.add_sample('loop_ms', loop_ms)

    def uptime(self):
        return max(time.perf_counter() - self.start_time, 1e-6)

    def fps(self):
        return self.frame_count / self.uptime()

    def pipe_kbps(self):
        return (self.bytes_to_ffmpeg * 8.0 / 1000.0) / self.uptime()

    def pipe_kb_per_frame(self):
        if self.frame_count == 0:
            return 0.0
        return (self.bytes_to_ffmpeg / 1024.0) / self.frame_count


def build_overlay_lines(stats, ffmpeg_proc):
    ffmpeg_state = 'alive' if ffmpeg_proc.poll() is None else f'exit={ffmpeg_proc.poll()}'
    return [
        'Scheme: DVPP H.264 -> FFmpeg copy -> RTSP',
        f'Target: {FRAME_WIDTH}x{FRAME_HEIGHT} @{FPS}fps GOP={FPS * GOP_SECONDS} bitrate={VIDEO_BITRATE}',
        f'Frames: {stats.frame_count} Uptime: {stats.uptime():.1f}s FFmpeg: {ffmpeg_state}',
        f'FPS(avg): {stats.fps():.2f} Loop: {stats.average("loop_ms"):.1f}ms Capture: {stats.average("capture_ms"):.1f}ms Draw: {stats.average("draw_ms"):.1f}ms',
        f'NV12: {stats.average("nv12_ms"):.1f}ms Submit: {stats.average("encode_submit_ms"):.1f}ms Wait: {stats.average("packet_wait_ms"):.1f}ms PipeWrite: {stats.average("pipe_write_ms"):.1f}ms',
        f'App->FFmpeg: {stats.pipe_kbps():.1f} kbps {stats.pipe_kb_per_frame():.1f} KB/frame',
    ]


def draw_overlay(frame, lines):
    overlay = frame.copy()
    margin = 10
    line_height = 22
    panel_width = min(frame.shape[1] - 2 * margin, 620)
    panel_height = margin * 2 + line_height * len(lines)
    cv2.rectangle(overlay, (margin, margin), (margin + panel_width, margin + panel_height), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)

    for index, line in enumerate(lines):
        y = margin + 22 + index * line_height
        cv2.putText(frame, line, (margin + 10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

# ========== 获取本机IP ==========
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

local_ip = get_local_ip()
print(f"昇腾310B本机IP地址: {local_ip}")

# ========== 配置参数 ==========
NOTEBOOK_IP = "192.168.1.72"      # ⚠️ 修改为你的笔记本实际IP
RTSP_PORT = 8554
STREAM_ID = "live/stream"
GOP_SECONDS = 1
VIDEO_BITRATE = "1200k"
VIDEO_BITRATE_BPS = 1200000

PUBLISH_URL = f"rtsp://{NOTEBOOK_IP}:{RTSP_PORT}/{STREAM_ID}"
stats = StreamStats()

CAMERA_ID = 0                      # 摄像头ID，可改为视频文件路径
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FPS = 20
FRAME_INTERVAL = 1.0 / FPS

# ========== 检查服务端连通性 ==========
def check_tcp_port(host, port, timeout=3.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True, ""
    except Exception as exc:
        return False, str(exc)
    finally:
        sock.close()


# ========== 检查FFmpeg RTSP推流能力 ==========
def check_ffmpeg_rtsp_output():
    try:
        result = subprocess.run(['ffmpeg', '-muxers'], capture_output=True, text=True, timeout=5)
        return ' rtsp ' in f" {result.stdout.lower()} "
    except Exception:
        return False

if not check_ffmpeg_rtsp_output():
    print("错误：当前FFmpeg不支持 RTSP 推流输出（缺少 rtsp muxer）。")
    sys.exit(1)
else:
    print("FFmpeg RTSP 推流能力检查通过")

server_ok, server_error = check_tcp_port(NOTEBOOK_IP, RTSP_PORT)
if not server_ok:
    print(f"错误：无法连接 RTSP 服务端 {NOTEBOOK_IP}:{RTSP_PORT}，原因：{server_error}")
    sys.exit(1)
else:
    print(f"RTSP 服务端连通性检查通过: {NOTEBOOK_IP}:{RTSP_PORT}")

# ========== 初始化摄像头 ==========
cap = cv2.VideoCapture(CAMERA_ID)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
cap.set(cv2.CAP_PROP_FPS, FPS)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # 减小OpenCV内部缓冲区

if not cap.isOpened():
    print("无法打开摄像头")
    sys.exit(1)

# ========== 初始化硬件编码器 ==========
venc = VideoEncoder(
    width=FRAME_WIDTH,
    height=FRAME_HEIGHT,
    fps=FPS,
    codec='h264',
    bitrate=VIDEO_BITRATE_BPS,
    gop=max(1, FPS * GOP_SECONDS),
)

# ========== 启动FFmpeg进程（接收H.264裸流并封装RTSP）==========
ffmpeg_cmd = [
    'ffmpeg', '-y',
    '-loglevel', 'warning',
    '-use_wallclock_as_timestamps', '1',
    '-fflags', '+genpts+nobuffer',
    '-flags', 'low_delay',
    '-f', 'h264',
    '-framerate', str(FPS),
    '-i', 'pipe:0',
    '-an',
    '-c:v', 'copy',
    '-rtsp_transport', 'tcp',
    '-muxdelay', '0.1',
    '-f', 'rtsp',
    PUBLISH_URL,
]

print(f"启动推流: {PUBLISH_URL}")
print("FFmpeg命令:", ' '.join(ffmpeg_cmd))

# ========== 启动FFmpeg子进程，并实时打印stderr ==========
ffmpeg_proc = subprocess.Popen(
    ffmpeg_cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE
)


def log_stderr(proc):
    for line in proc.stderr:
        text = line.decode(errors='replace').strip()
        if text:
            print("[FFmpeg]", text)


thread = threading.Thread(target=log_stderr, args=(ffmpeg_proc,), daemon=True)
thread.start()

# 等待0.5秒，检查进程是否立即崩溃
time.sleep(0.5)
if ffmpeg_proc.poll() is not None:
    cap.release()
    venc.release()
    sys.exit(1)
else:
    print("FFmpeg 进程启动成功，开始推流...")
    print("当前使用 DVPP H.264 编码 + RTSP 低延迟推流。VLC 播放建议使用 --network-caching=50 到 150。")

# ========== 主循环 ==========
frame_count = 0
try:
    while True:
        loop_start = time.perf_counter()

        ret, frame = cap.read()
        capture_ms = (time.perf_counter() - loop_start) * 1000.0
        if not ret:
            print("无法读取摄像头帧")
            break
        stats.add_sample('capture_ms', capture_ms)

        # ---------- 在此添加你的目标检测/跟踪代码 ----------
        # 示例：绘制矩形和IP文字（可替换为推理结果）
        draw_start = time.perf_counter()
        cv2.rectangle(frame, (100, 100), (200, 200), (0, 255, 0), 2)
        cv2.putText(frame, f"310B IP: {local_ip}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        draw_overlay(frame, build_overlay_lines(stats, ffmpeg_proc))
        stats.add_sample('draw_ms', (time.perf_counter() - draw_start) * 1000.0)

        nv12_start = time.perf_counter()
        nv12_frame = bgr_to_nv12(frame)
        stats.add_sample('nv12_ms', (time.perf_counter() - nv12_start) * 1000.0)

        encode_submit_start = time.perf_counter()
        venc.encode(nv12_frame)
        stats.add_sample('encode_submit_ms', (time.perf_counter() - encode_submit_start) * 1000.0)

        try:
            packet_wait_start = time.perf_counter()
            packet = venc.get_packet(block=True, timeout=1.0)
            stats.add_sample('packet_wait_ms', (time.perf_counter() - packet_wait_start) * 1000.0)
            if packet is None:
                print("未收到 DVPP 编码输出，跳过当前帧")
                continue

            pipe_bytes = len(packet)
            pipe_write_start = time.perf_counter()
            ffmpeg_proc.stdin.write(packet)
            while True:
                extra_packet = venc.get_packet(block=False)
                if extra_packet is None:
                    break
                ffmpeg_proc.stdin.write(extra_packet)
                pipe_bytes += len(extra_packet)
            stats.add_sample('pipe_write_ms', (time.perf_counter() - pipe_write_start) * 1000.0)
            stats.add_pipe_bytes(pipe_bytes)
        except BrokenPipeError:
            print("FFmpeg进程的管道已断开")
            exit_code = ffmpeg_proc.poll()
            if exit_code is not None:
                print(f"FFmpeg 已退出，返回码: {exit_code}")
                print("这通常表示 RTSP 服务端主动断开连接，请同时检查 MediaMTX 路径配置和端口监听状态。")
            break
        except Exception as e:
            print(f"写入管道时发生异常: {e}")
            break

        stats.finish_frame((time.perf_counter() - loop_start) * 1000.0)

        # 控制帧率
        elapsed = time.perf_counter() - loop_start
        sleep_time = FRAME_INTERVAL - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

        # 定期检查FFmpeg进程状态（每30帧）
        frame_count += 1
        if frame_count % 30 == 0 and ffmpeg_proc.poll() is not None:
            print(f"FFmpeg进程已退出，返回码: {ffmpeg_proc.poll()}")
            print("请同时查看 RTSP 服务端日志，确认该路径允许发布且没有鉴权拦截。")
            break

        # 按 'q' 键退出（如果有显示窗口）
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    # ========== 清理资源 ==========
    cap.release()
    venc.release()
    if ffmpeg_proc.stdin:
        try:
            ffmpeg_proc.stdin.close()
        except Exception:
            pass
    if ffmpeg_proc.poll() is None:
        ffmpeg_proc.terminate()
    try:
        ffmpeg_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        ffmpeg_proc.kill()
    cv2.destroyAllWindows()
    print("推流结束")