from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response
import cv2
import os
import numpy as np
from mtcnn.mtcnn import MTCNN
from sklearn.metrics.pairwise import cosine_similarity
from utils.acl_resource import AclResource
from utils.model_processor import ModelProcessor
import datetime
from werkzeug.utils import secure_filename
from PIL import Image
import base64
import io
import signal
import atexit
import threading
import time
import json

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # 请更改为安全的密钥

# 添加自定义Jinja2过滤器
@app.template_filter('starts_with')
def starts_with_filter(text, prefix):
    """检查文本是否以指定前缀开始"""
    return str(text).startswith(str(prefix))

# --- Configuration ---
FACE_RECOGNITION_MODEL_PATH = 'models/mobilefacenet.om'
MTCNN_PNET_PATH = 'models/pnet.onnx'
MTCNN_RNET_PATH = 'models/rnet.onnx'
MTCNN_ONET_PATH = 'models/onet.onnx'
DATASET_PATH = 'datasets'
ATTENDANCE_FILE = 'attendance.csv'
SIMILARITY_THRESHOLD = 0.7
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp'}

# 考勤配置
ATTENDANCE_INTERVAL_HOURS = 8  # 再次打卡的间隔时间（小时）
ATTENDANCE_CONFIG_FILE = 'attendance_config.json'

# 确保上传文件夹存在
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# 全局变量
acl_resource = None
face_recognition_proc = None
detector = None
known_face_embeddings = []
known_face_names = []

# 摄像头相关变量
camera = None
camera_lock = threading.Lock()
is_camera_active = False
last_recognition_time = 0
recognition_cooldown = 2  # 识别冷却时间（秒）

# 最新识别结果
latest_recognition_results = []
results_lock = threading.Lock()

def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def load_attendance_config():
    """加载考勤配置"""
    global ATTENDANCE_INTERVAL_HOURS
    try:
        if os.path.exists(ATTENDANCE_CONFIG_FILE):
            with open(ATTENDANCE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                ATTENDANCE_INTERVAL_HOURS = config.get('interval_hours', 8)
        else:
            # 创建默认配置文件
            save_attendance_config()
    except Exception as e:
        print(f"加载考勤配置失败: {e}")
        ATTENDANCE_INTERVAL_HOURS = 8

def save_attendance_config():
    """保存考勤配置"""
    try:
        config = {
            'interval_hours': ATTENDANCE_INTERVAL_HOURS,
            'description': '再次打卡的间隔时间（小时）'
        }
        with open(ATTENDANCE_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存考勤配置失败: {e}")

def get_attendance_config():
    """获取考勤配置"""
    return {
        'interval_hours': ATTENDANCE_INTERVAL_HOURS
    }

def preprocess_image(img, target_size=(112, 112)):
    """Preprocesses an image for the face recognition model."""
    # Resize and normalize
    img = cv2.resize(img, target_size)
    img = img.astype(np.float32) / 255.0
    img = (img - 0.5) / 0.5  # Normalize to [-1, 1]
    img = np.transpose(img, (2, 0, 1))  # HWC to CHW
    return np.expand_dims(img, axis=0) # Add batch dimension

def load_known_faces():
    """Load known faces from the dataset directory."""
    global known_face_embeddings, known_face_names
    
    known_face_embeddings = []
    known_face_names = []
    
    if not os.path.exists(DATASET_PATH):
        print(f"Dataset path {DATASET_PATH} does not exist")
        return
    
    for name in os.listdir(DATASET_PATH):
        user_dir = os.path.join(DATASET_PATH, name)
        if os.path.isdir(user_dir):
            user_embeddings = []
            for img_name in os.listdir(user_dir):
                img_path = os.path.join(user_dir, img_name)
                try:
                    img = cv2.imread(img_path)
                    if img is not None:
                        # Preprocess for MobileFaceNet
                        preprocessed_img = preprocess_image(img)
                        # Get embedding with error handling
                        try:
                            embedding = face_recognition_proc.predict(preprocessed_img)[0]
                            user_embeddings.append(embedding.flatten())
                        except RuntimeError as e:
                            print(f"Error processing {img_path}: {e}")
                            # Continue with other images instead of failing completely
                            continue
                except Exception as e:
                    print(f"Error loading image {img_path}: {e}")
                    continue

            if user_embeddings:
                # Average embeddings for this user
                avg_embedding = np.mean(user_embeddings, axis=0)
                known_face_embeddings.append(avg_embedding)
                known_face_names.append(name)
            else:
                print(f"Warning: No valid embeddings found for user {name}")

    print(f"Loaded {len(known_face_names)} known faces: {known_face_names}")

def log_attendance(name):
    """记录考勤到CSV文件，支持可配置的间隔时间"""
    current_time = datetime.datetime.now()
    timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
    
    # 检查考勤文件是否存在，不存在则创建并添加表头
    if not os.path.exists(ATTENDANCE_FILE):
        with open(ATTENDANCE_FILE, 'w', encoding='utf-8') as f:
            f.write("Name,Timestamp\n")
    
    # 检查该人员是否在间隔时间内已经打过卡
    try:
        with open(ATTENDANCE_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for line in lines[1:]:  # 跳过表头
                if line.strip():
                    parts = line.strip().split(',')
                    if len(parts) >= 2:
                        logged_name, logged_time = parts[0], parts[1]
                        if logged_name == name:
                            # 解析上次打卡时间
                            try:
                                last_time = datetime.datetime.strptime(logged_time, "%Y-%m-%d %H:%M:%S")
                                time_diff = current_time - last_time
                                # 检查是否在间隔时间内
                                if time_diff.total_seconds() < ATTENDANCE_INTERVAL_HOURS * 3600:
                                    remaining_hours = ATTENDANCE_INTERVAL_HOURS - (time_diff.total_seconds() / 3600)
                                    return {
                                        'success': False, 
                                        'message': f'距离上次打卡时间不足{ATTENDANCE_INTERVAL_HOURS}小时，还需等待{remaining_hours:.1f}小时',
                                        'remaining_hours': remaining_hours
                                    }
                            except ValueError:
                                continue  # 时间格式错误，跳过这条记录
    except Exception as e:
        print(f"读取考勤文件失败: {e}")
    
    # 记录考勤
    try:
        with open(ATTENDANCE_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{name},{timestamp}\n")
        return {
            'success': True, 
            'message': f'{name} 打卡成功！时间：{timestamp}',
            'timestamp': timestamp
        }
    except Exception as e:
        print(f"写入考勤文件失败: {e}")
        return {
            'success': False, 
            'message': '打卡失败，请重试',
            'error': str(e)
        }

def cleanup_resources():
    """清理资源"""
    global acl_resource, face_recognition_proc, detector, camera, is_camera_active
    
    try:
        # 清理摄像头资源
        release_camera()
        
        if face_recognition_proc:
            face_recognition_proc.unload_model()
            face_recognition_proc = None
        
        if acl_resource:
            acl_resource.__exit__(None, None, None)
            acl_resource = None
        
        detector = None
        print("Resources cleaned up successfully")
    except Exception as e:
        print(f"Error cleaning up resources: {e}")

def initialize_models():
    """初始化模型和资源"""
    global acl_resource, face_recognition_proc, detector
    
    try:
        # Clean up any existing resources first
        cleanup_resources()
        
        # Initialize Ascend NPU resources
        acl_resource = AclResource()
        acl_resource.__enter__()
        
        # Load face recognition model
        face_recognition_proc = ModelProcessor(acl_resource, FACE_RECOGNITION_MODEL_PATH)
        face_recognition_proc.load_model()
        
        # Initialize face detector (MTCNN)
        detector = MTCNN()
        
        # Load known faces
        load_known_faces()
        
        return True
    except Exception as e:
        print(f"Error initializing models: {e}")
        cleanup_resources()  # Clean up on failure
        return False

@app.route('/')
def index():
    """主页"""
    return render_template('index.html')

@app.route('/recognize', methods=['GET', 'POST'])
def recognize():
    """人脸识别页面"""
    if request.method == 'POST':
        # 检查是否有文件上传
        if 'file' not in request.files:
            flash('没有选择文件')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('没有选择文件')
            return redirect(request.url)
        
        if file and allowed_file(file.filename):
            try:
                # 读取图片
                image_data = file.read()
                nparr = np.frombuffer(image_data, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                
                if img is None:
                    flash('无法读取图片文件')
                    return redirect(request.url)
                
                # 检测人脸
                faces = detector.detect_faces(img)
                
                results = []
                for i, face in enumerate(faces):
                    x, y, w, h = face['box']
                    x1, y1, x2, y2 = max(0, x), max(0, y), x + w, y + h
                    face_img = img[y1:y2, x1:x2]
                    
                    if face_img.size > 0:
                        # 获取人脸特征
                        preprocessed_face = preprocess_image(face_img)
                        current_embedding = face_recognition_proc.predict(preprocessed_face)[0].flatten()
                        
                        # 与已知人脸比较
                        if known_face_embeddings:
                            similarities = cosine_similarity([current_embedding], known_face_embeddings)[0]
                            best_match_index = np.argmax(similarities)
                            
                            name = "未知"
                            confidence = similarities[best_match_index]
                            
                            if confidence > SIMILARITY_THRESHOLD:
                                name = known_face_names[best_match_index]
                                # 记录考勤
                                attendance_logged = log_attendance(name)
                                results.append({
                                    'name': name,
                                    'confidence': float(confidence),
                                    'box': [x1, y1, x2, y2],
                                    'attendance_logged': attendance_logged
                                })
                            else:
                                results.append({
                                    'name': name,
                                    'confidence': float(confidence),
                                    'box': [x1, y1, x2, y2],
                                    'attendance_logged': False
                                })
                        else:
                            results.append({
                                'name': "未知",
                                'confidence': 0.0,
                                'box': [x1, y1, x2, y2],
                                'attendance_logged': False
                            })
                
                # 将图片转换为base64用于显示
                _, buffer = cv2.imencode('.jpg', img)
                img_base64 = base64.b64encode(buffer).decode('utf-8')
                
                return render_template('recognize.html', 
                                     results=results, 
                                     image=img_base64,
                                     has_results=True)
                
            except Exception as e:
                flash(f'处理图片时出错: {str(e)}')
                return redirect(request.url)
        else:
            flash('不支持的文件格式。请上传 PNG, JPG, JPEG, GIF 或 BMP 格式的图片。')
            return redirect(request.url)
    
    return render_template('recognize.html', has_results=False)

@app.route('/register', methods=['GET', 'POST'])
def register():
    """人脸注册页面"""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('请输入姓名')
            return redirect(request.url)
        
        # 检查是否有文件上传
        if 'files' not in request.files:
            flash('没有选择文件')
            return redirect(request.url)
        
        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            flash('没有选择文件')
            return redirect(request.url)
        
        # 创建用户目录
        save_path = os.path.join(DATASET_PATH, name)
        if not os.path.exists(save_path):
            os.makedirs(save_path)
        
        saved_count = 0
        for file in files:
            if file and allowed_file(file.filename):
                try:
                    # 读取图片
                    image_data = file.read()
                    nparr = np.frombuffer(image_data, np.uint8)
                    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    
                    if img is not None:
                        # 检测人脸
                        faces = detector.detect_faces(img)
                        
                        if faces:
                            # 保存第一个检测到的人脸
                            face = faces[0]
                            x, y, w, h = face['box']
                            x1, y1, x2, y2 = max(0, x), max(0, y), x + w, y + h
                            face_img = img[y1:y2, x1:x2]
                            
                            if face_img.size > 0:
                                filename = f"{name}_{saved_count + 1}.jpg"
                                filepath = os.path.join(save_path, filename)
                                cv2.imwrite(filepath, face_img)
                                saved_count += 1
                
                except Exception as e:
                    print(f"Error processing file {file.filename}: {e}")
        
        if saved_count > 0:
            # 重新加载已知人脸
            load_known_faces()
            flash(f'成功注册 {name}，保存了 {saved_count} 张人脸图片')
        else:
            flash('没有检测到有效的人脸图片')
        
        return redirect(request.url)
    
    return render_template('register.html')

@app.route('/attendance')
def attendance():
    """考勤记录页面"""
    records = []
    if os.path.exists(ATTENDANCE_FILE):
        with open(ATTENDANCE_FILE, 'r') as f:
            lines = f.readlines()
            for line in lines[1:]:  # Skip header
                if line.strip():
                    parts = line.strip().split(',')
                    if len(parts) >= 2:
                        records.append({
                            'name': parts[0],
                            'timestamp': parts[1]
                        })
    
    # 按时间倒序排列
    records.reverse()
    return render_template('attendance.html', records=records)

@app.route('/attendance_config_page')
def attendance_config_page():
    """考勤配置页面"""
    return render_template('attendance_config.html')

def init_camera():
    """初始化摄像头"""
    global camera, is_camera_active
    try:
        with camera_lock:
            if camera is None:
                camera = cv2.VideoCapture(0)
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                camera.set(cv2.CAP_PROP_FPS, 30)
            is_camera_active = True
        return True
    except Exception as e:
        print(f"Error initializing camera: {e}")
        return False

def release_camera():
    """释放摄像头"""
    global camera, is_camera_active
    try:
        with camera_lock:
            is_camera_active = False
            if camera is not None:
                camera.release()
                camera = None
        return True
    except Exception as e:
        print(f"Error releasing camera: {e}")
        return False

def recognize_face_in_frame(frame):
    """在帧中识别人脸"""
    global last_recognition_time, latest_recognition_results
    
    current_time = time.time()
    if current_time - last_recognition_time < recognition_cooldown:
        return []
    
    try:
        # 检测人脸
        faces = detector.detect_faces(frame)
        results = []
        
        for face in faces:
            x, y, w, h = face['box']
            x1, y1, x2, y2 = max(0, x), max(0, y), x + w, y + h
            face_img = frame[y1:y2, x1:x2]
            
            if face_img.size > 0:
                # 获取人脸特征
                preprocessed_face = preprocess_image(face_img)
                current_embedding = face_recognition_proc.predict(preprocessed_face)[0].flatten()
                
                # 与已知人脸比较
                if known_face_embeddings:
                    similarities = cosine_similarity([current_embedding], known_face_embeddings)[0]
                    best_match_index = np.argmax(similarities)
                    
                    name = "未知"
                    confidence = similarities[best_match_index]
                    
                    if confidence > SIMILARITY_THRESHOLD:
                        name = known_face_names[best_match_index]
                        # 记录考勤
                        attendance_result = log_attendance(name)
                        results.append({
                            'name': name,
                            'confidence': float(confidence),
                            'box': [x1, y1, x2, y2],
                            'attendance_logged': attendance_result['success'],
                            'attendance_message': attendance_result['message']
                        })
                    else:
                        results.append({
                            'name': name,
                            'confidence': float(confidence),
                            'box': [x1, y1, x2, y2],
                            'attendance_logged': False,
                            'attendance_message': '识别置信度不足'
                        })
                else:
                    results.append({
                        'name': "未知",
                        'confidence': 0.0,
                        'box': [x1, y1, x2, y2],
                        'attendance_logged': False
                    })
        
        last_recognition_time = current_time
        
        # 保存最新识别结果
        with results_lock:
            latest_recognition_results = results.copy()
        
        return results
    except Exception as e:
        print(f"Error in face recognition: {e}")
        return []

def generate_frames():
    """生成视频帧"""
    global camera, is_camera_active
    
    while is_camera_active:
        try:
            with camera_lock:
                if camera is None or not is_camera_active:
                    break
                
                success, frame = camera.read()
                if not success:
                    break
                
                # 水平翻转图像（镜像效果）
                frame = cv2.flip(frame, 1)
                
                # 进行人脸识别
                recognition_results = recognize_face_in_frame(frame)
                
                # 在帧上绘制识别结果
                for result in recognition_results:
                    x1, y1, x2, y2 = result['box']
                    name = result['name']
                    confidence = result['confidence']
                    
                    # 绘制人脸框
                    color = (0, 255, 0) if name != "未知" else (0, 0, 255)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    
                    # 绘制姓名和置信度
                    label = f"{name} ({confidence:.2f})"
                    cv2.putText(frame, label, (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                
                # 编码帧
                ret, buffer = cv2.imencode('.jpg', frame)
                if ret:
                    frame_bytes = buffer.tobytes()
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                
        except Exception as e:
            print(f"Error generating frame: {e}")
            break
        
        time.sleep(0.033)  # ~30 FPS

@app.route('/camera_live')
def camera_live():
    """摄像头实时识别页面"""
    return render_template('camera_live.html')

@app.route('/camera_register')
def camera_register():
    """摄像头注册页面"""
    return render_template('camera_register.html')

@app.route('/video_feed')
def video_feed():
    """视频流"""
    if not init_camera():
        return "Camera initialization failed", 500
    
    return Response(generate_frames(),
                   mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/start_camera', methods=['POST'])
def start_camera():
    """启动摄像头"""
    if init_camera():
        return jsonify({'success': True, 'message': '摄像头启动成功'})
    else:
        return jsonify({'success': False, 'message': '摄像头启动失败'})

@app.route('/stop_camera', methods=['POST'])
def stop_camera():
    """停止摄像头"""
    if release_camera():
        return jsonify({'success': True, 'message': '摄像头已停止'})
    else:
        return jsonify({'success': False, 'message': '停止摄像头失败'})

@app.route('/get_recognition_results', methods=['GET'])
def get_recognition_results():
    """获取最新的识别结果"""
    global latest_recognition_results
    
    with results_lock:
        results = latest_recognition_results.copy()
    
    return jsonify({'results': results})

@app.route('/capture_face', methods=['POST'])
def capture_face():
    """从摄像头捕获人脸用于注册"""
    global camera
    
    data = request.get_json()
    name = data.get('name', '').strip()
    
    if not name:
        return jsonify({'success': False, 'message': '请输入姓名'})
    
    try:
        with camera_lock:
            if camera is None:
                return jsonify({'success': False, 'message': '摄像头未启动'})
            
            success, frame = camera.read()
            if not success:
                return jsonify({'success': False, 'message': '无法获取摄像头画面'})
            
            # 水平翻转图像
            frame = cv2.flip(frame, 1)
            
            # 检测人脸
            faces = detector.detect_faces(frame)
            
            if not faces:
                return jsonify({'success': False, 'message': '未检测到人脸'})
            
            # 创建用户目录
            save_path = os.path.join(DATASET_PATH, name)
            if not os.path.exists(save_path):
                os.makedirs(save_path)
            
            # 保存检测到的人脸
            saved_count = 0
            for i, face in enumerate(faces):
                x, y, w, h = face['box']
                x1, y1, x2, y2 = max(0, x), max(0, y), x + w, y + h
                face_img = frame[y1:y2, x1:x2]
                
                if face_img.size > 0:
                    # 生成文件名
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"{name}_{timestamp}_{i+1}.jpg"
                    filepath = os.path.join(save_path, filename)
                    
                    # 保存人脸图片
                    cv2.imwrite(filepath, face_img)
                    saved_count += 1
            
            if saved_count > 0:
                # 重新加载已知人脸
                load_known_faces()
                return jsonify({
                    'success': True, 
                    'message': f'成功注册 {name}，保存了 {saved_count} 张人脸图片'
                })
            else:
                return jsonify({'success': False, 'message': '保存人脸图片失败'})
                
    except Exception as e:
        return jsonify({'success': False, 'message': f'捕获人脸时出错: {str(e)}'})

@app.route('/user_management')
def user_management():
    """用户管理页面"""
    users = []
    if os.path.exists(DATASET_PATH):
        for user_dir in os.listdir(DATASET_PATH):
            user_path = os.path.join(DATASET_PATH, user_dir)
            if os.path.isdir(user_path):
                # 统计用户的图片数量
                image_count = len([f for f in os.listdir(user_path) 
                                 if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp'))])
                users.append({
                    'name': user_dir,
                    'image_count': image_count,
                    'path': user_path
                })
    
    return render_template('user_management.html', users=users)

@app.route('/delete_user', methods=['POST'])
def delete_user():
    """删除用户"""
    data = request.get_json()
    username = data.get('username', '').strip()
    
    if not username:
        return jsonify({'success': False, 'message': '用户名不能为空'})
    
    user_path = os.path.join(DATASET_PATH, username)
    
    try:
        if os.path.exists(user_path) and os.path.isdir(user_path):
            # 删除用户目录及其所有文件
            import shutil
            shutil.rmtree(user_path)
            
            # 重新加载已知人脸
            load_known_faces()
            
            return jsonify({'success': True, 'message': f'用户 {username} 已删除'})
        else:
            return jsonify({'success': False, 'message': '用户不存在'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'删除用户时出错: {str(e)}'})

@app.route('/get_users', methods=['GET'])
def get_users():
    """获取用户列表"""
    users = []
    if os.path.exists(DATASET_PATH):
        for user_dir in os.listdir(DATASET_PATH):
            user_path = os.path.join(DATASET_PATH, user_dir)
            if os.path.isdir(user_path):
                image_count = len([f for f in os.listdir(user_path) 
                                 if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp'))])
                users.append({
                    'name': user_dir,
                    'image_count': image_count
                })
    
    return jsonify({'users': users})

@app.route('/attendance_config', methods=['GET', 'POST'])
def attendance_config():
    """考勤配置管理"""
    global ATTENDANCE_INTERVAL_HOURS
    
    if request.method == 'POST':
        try:
            data = request.get_json()
            new_interval = data.get('interval_hours')
            
            if new_interval is None or not isinstance(new_interval, (int, float)):
                return jsonify({'error': '间隔时间必须是数字'}), 400
            
            if new_interval <= 0 or new_interval > 24:
                return jsonify({'error': '间隔时间必须在0-24小时之间'}), 400
            
            ATTENDANCE_INTERVAL_HOURS = float(new_interval)
            save_attendance_config()
            
            return jsonify({
                'success': True,
                'message': f'考勤间隔时间已更新为{ATTENDANCE_INTERVAL_HOURS}小时',
                'interval_hours': ATTENDANCE_INTERVAL_HOURS
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        # GET请求，返回当前配置
        return jsonify(get_attendance_config())

def signal_handler(signum, frame):
    """处理程序退出信号"""
    print(f"\nReceived signal {signum}, cleaning up...")
    cleanup_resources()
    exit(0)

if __name__ == '__main__':
    # 注册清理函数
    atexit.register(cleanup_resources)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 加载考勤配置
    load_attendance_config()
    print(f"考勤间隔时间: {ATTENDANCE_INTERVAL_HOURS}小时")

    if initialize_models():
        print("Models initialized successfully")
        print(f"Known faces: {known_face_names}")
        try:
            app.run(debug=True, host='0.0.0.0', port=5000)
        except KeyboardInterrupt:
            print("\nShutting down gracefully...")
        finally:
            cleanup_resources()
    else:
        print("Failed to initialize models")