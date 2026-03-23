from flask import Flask, request, jsonify, render_template, send_from_directory, Response
import os
import cv2
import numpy as np
import base64
import time
from datetime import datetime
import database
from ascend_inference import FaceSystem
from camera import VideoCamera

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 全局对象
face_system = None
video_camera = None

def get_face_system():
    global face_system
    if face_system is None:
        try:
            face_system = FaceSystem()
        except Exception as e:
            print(f"初始化人脸系统失败: {e}")
            face_system = None
    return face_system

def get_video_camera():
    global video_camera
    if video_camera is None:
        fs = get_face_system()
        if fs:
            try:
                video_camera = VideoCamera(fs)
            except Exception as e:
                print(f"初始化摄像头失败: {e}")
    return video_camera

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/users_page')
def users_page():
    return render_template('users.html')

@app.route('/attendance_page')
def attendance_page():
    return render_template('attendance.html')

@app.route('/api/users', methods=['GET'])
def list_users():
    try:
        users = database.get_users()
        result = []
        for u in users:
            u_dict = dict(u)
            del u_dict['embedding']
            result.append(u_dict)
        return jsonify(result)
    except Exception as e:
        print(f"获取用户列表失败: {e}")
        return jsonify([])

@app.route('/api/camera/capture', methods=['POST'])
def capture_from_device():
    cam = get_video_camera()
    if cam is None:
        return jsonify({"error": "摄像头不可用"}), 503

    frame = cam.get_snapshot()
    if frame is None:
        return jsonify({"error": "抓取画面失败"}), 500

    # 保存到临时文件
    filename = f"capture_{int(time.time())}.jpg"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    cv2.imwrite(filepath, frame)

    return jsonify({"success": True, "temp_path": filename})

@app.route('/api/users', methods=['POST'])
def add_user():
    name = request.form.get('name')
    img = None

    # 检查是使用上传文件、抓拍的画面还是 base64 数据
    if 'image' in request.files and request.files['image'].filename != '':
        file = request.files['image']
        filename = f"{int(time.time())}_{file.filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        img = cv2.imread(filepath)
    elif 'temp_path' in request.form:
        temp_filename = request.form.get('temp_path')
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
        if os.path.exists(filepath):
            img = cv2.imread(filepath)
        else:
            return jsonify({"error": "抓拍文件未找到"}), 400
    elif 'image_base64' in request.form:
        data = request.form['image_base64']
        if ',' in data:
            data = data.split(',')[1]
        img_data = base64.b64decode(data)
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    else:
         return jsonify({"error": "未提供图片"}), 400

    if img is None:
        return jsonify({"error": "无效的图片"}), 400

    fs = get_face_system()
    if fs is None:
        return jsonify({"error": "人脸系统未初始化"}), 500

    try:
        faces = fs.detect(img)
        if len(faces) > 0:
            best_face = max(faces, key=lambda b: (b[2]-b[0]) * (b[3]-b[1]))
            x1, y1, x2, y2 = map(int, best_face)
            h, w = img.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            face_img = img[y1:y2, x1:x2]
        else:
            face_img = img

        embedding = fs.get_embedding(face_img)
        embedding_blob = embedding.tobytes()

        # 保存头像
        avatar_filename = f"avatar_{int(time.time())}.jpg"
        avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], avatar_filename)
        cv2.imwrite(avatar_path, face_img)

        user_id = database.add_user(name, embedding_blob, avatar_filename)
        print(f"用户已添加: {name} (ID: {user_id})")
        return jsonify({"success": True, "user_id": user_id})
    except Exception as e:
        print(f"添加用户失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    database.delete_user(user_id)
    return jsonify({"success": True})

@app.route('/api/users/<int:user_id>', methods=['PUT'])
def update_user(user_id):
    name = request.json.get('name')
    if name:
        database.update_user_name(user_id, name)
        return jsonify({"success": True})
    return jsonify({"error": "姓名不能为空"}), 400

@app.route('/api/clockin', methods=['POST'])
def clockin():
    # 手动打卡（上传或客户端摄像头）
    img = None
    filepath = "unknown"

    if 'image' in request.files:
        file = request.files['image']
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"clockin_{int(time.time())}.jpg")
        file.save(filepath)
        img = cv2.imread(filepath)
    elif 'image_base64' in request.form:
        data = request.form['image_base64']
        if ',' in data:
            data = data.split(',')[1]
        img_data = base64.b64decode(data)
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        filepath = "client_camera"

    if img is None:
        return jsonify({"error": "无图片数据"}), 400

    fs = get_face_system()
    
    faces = fs.detect(img)
    if len(faces) > 0:
        best_face = max(faces, key=lambda b: (b[2]-b[0]) * (b[3]-b[1]))
        x1, y1, x2, y2 = map(int, best_face)
        h, w = img.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        face_img = img[y1:y2, x1:x2]
    else:
        face_img = img
    
    target_embedding = fs.get_embedding(face_img)
    
    users = database.get_users()
    max_sim = -1.0
    best_match = None
    threshold = 0.5
    
    for u in users:
        db_emb = np.frombuffer(u['embedding'], dtype=np.float32)
        sim = np.dot(target_embedding, db_emb) / (np.linalg.norm(target_embedding) * np.linalg.norm(db_emb) + 1e-6)
        if sim > max_sim:
            max_sim = sim
            best_match = u

    if best_match and max_sim > threshold:
        database.add_attendance(best_match['id'], 'manual', filepath)
        return jsonify({
            "success": True, 
            "match": True, 
            "user": best_match['name'], 
            "similarity": float(max_sim)
        })
    else:
        return jsonify({
            "success": True, 
            "match": False,
            "similarity": float(max_sim)
        })

@app.route('/api/attendance', methods=['GET'])
def list_attendance():
    records = database.get_attendance()
    return jsonify([dict(r) for r in records])

def gen(camera):
    while True:
        frame = camera.get_frame()
        if frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        else:
            time.sleep(0.1)

@app.route('/video_feed')
def video_feed():
    cam = get_video_camera()
    if cam is None:
        return "摄像头不可用", 503
    return Response(gen(cam),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    database.init_db()
    get_face_system()
    # 启动时初始化摄像头（如果可用）
    get_video_camera()

    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
