import cv2
import subprocess
import time
import socket
import sys
import threading

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
NOTEBOOK_IP = "192.168.1.72"      # 你的笔记本IP
RTMP_PORT = 1935
STREAM_ID = "live/stream"
RTMP_URL = f"rtmp://{NOTEBOOK_IP}:{RTMP_PORT}/{STREAM_ID}"

CAMERA_ID = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FPS = 20
FRAME_INTERVAL = 1.0 / FPS


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

# ========== 检查FFmpeg RTMP支持 ==========
def check_ffmpeg_rtmp():
    try:
        result = subprocess.run(['ffmpeg', '-protocols'], capture_output=True, text=True, timeout=5)
        return 'rtmp' in result.stdout
    except Exception:
        return False

if not check_ffmpeg_rtmp():
    print("错误：当前FFmpeg不支持RTMP协议。请安装支持RTMP的FFmpeg。")
    sys.exit(1)
else:
    print("FFmpeg RTMP 支持检查通过")

rtmp_ok, rtmp_error = check_tcp_port(NOTEBOOK_IP, RTMP_PORT)
if not rtmp_ok:
    print(f"错误：无法连接 RTMP 服务端 {NOTEBOOK_IP}:{RTMP_PORT}，原因：{rtmp_error}")
    sys.exit(1)
else:
    print(f"RTMP 服务端连通性检查通过: {NOTEBOOK_IP}:{RTMP_PORT}")

# ========== 初始化摄像头 ==========
cap = cv2.VideoCapture(CAMERA_ID)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
cap.set(cv2.CAP_PROP_FPS, FPS)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    print("无法打开摄像头")
    sys.exit(1)

# ========== 构建FFmpeg命令（MediaMTX兼容写法） ==========
ffmpeg_cmd = [
    'ffmpeg', '-y',
    '-f', 'rawvideo',
    '-pix_fmt', 'bgr24',
    '-video_size', f'{FRAME_WIDTH}x{FRAME_HEIGHT}',
    '-framerate', str(FPS),
    '-i', 'pipe:0',
    '-an',
    '-c:v', 'libx264',
    '-preset', 'ultrafast',
    '-tune', 'zerolatency',
    '-pix_fmt', 'yuv420p',
    '-profile:v', 'baseline',
    '-g', str(FPS * 2),
    '-keyint_min', str(FPS * 2),
    '-sc_threshold', '0',
    '-bf', '0',
    '-x264-params', 'nal-hrd=cbr:force-cfr=1',
    '-f', 'flv',
    '-flvflags', 'no_duration_filesize',
    '-rtmp_live', 'live',
    RTMP_URL
]

print(f"启动推流: {RTMP_URL}")
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

# 等待0.5秒检查进程是否立即崩溃
time.sleep(0.5)
if ffmpeg_proc.poll() is not None:
    print("FFmpeg 进程启动失败，请检查错误信息。")
    cap.release()
    sys.exit(1)
else:
    print("FFmpeg 进程启动成功，开始推流...")
    print("如果服务端仍提示 unable to parse H264 config: EOF，优先检查 MediaMTX 是否允许该路径发布，或改用更标准的 x264/RTMP 参数后重试。")

# ========== 主循环 ==========
frame_count = 0 # 初始化计数器
try:
    while True:
        loop_start = time.time()

        ret, frame = cap.read()
        if not ret:
            print("无法读取摄像头帧")
            break

        # ---------- 在此添加你的目标检测/跟踪代码 ----------
        # 示例：绘制矩形和IP文字
        cv2.rectangle(frame, (100, 100), (200, 200), (0, 255, 0), 2)
        cv2.putText(frame, f"310B IP: {local_ip}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        # 将原始BGR帧写入FFmpeg标准输入
        try:
            ffmpeg_proc.stdin.write(frame.tobytes())
        except BrokenPipeError:
            print("FFmpeg进程的管道已断开")
            exit_code = ffmpeg_proc.poll()
            if exit_code is not None:
                print(f"FFmpeg 已退出，返回码: {exit_code}")
                print("这通常表示 RTMP 服务端在握手后主动断开连接。若 MediaMTX 日志包含 unable to parse H264 config: EOF，说明服务端未成功解析 H.264 配置头。")
            break
        except Exception as e:
            print(f"写入管道时发生异常: {e}")
            break

        # 控制帧率
        elapsed = time.time() - loop_start
        sleep_time = FRAME_INTERVAL - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

        # 递增帧计数器
        frame_count += 1
        
        # 定期检查FFmpeg进程状态（每30帧）
        if frame_count % 30 == 0 and ffmpeg_proc.poll() is not None:
            print(f"FFmpeg进程已退出，返回码: {ffmpeg_proc.poll()}")
            print("请同时查看 RTMP 服务端日志；若出现 unable to parse H264 config: EOF，通常是编码参数或服务端 RTMP 解析兼容性问题。")
            break

        # 按 'q' 键退出（如果有显示窗口）
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    # ========== 清理资源 ==========
    cap.release()
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