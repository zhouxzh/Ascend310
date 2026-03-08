import cv2
import threading
import time
import numpy as np
import database
from ascend_inference import FaceSystem

class VideoCamera(object):
    def __init__(self, face_system):
        self.lock = threading.Lock()
        self.face_system = face_system
        self.last_frame = None
        self.running = True
        self.last_check_time = 0
        self.check_interval = 2.0 # Check face every 2 seconds
        
        self.video = cv2.VideoCapture(0)
        if not self.video.isOpened():
            print("Error: Could not open video device 0. Please check permissions (sudo chmod 666 /dev/video0).")
            # Don't set running=False here, or at least handle it gracefully.
            # But more importantly, self.lock MUST be initialized before return
            # self.running = False # Let it run but just loop empty?
            # Better: Keep running True but check isOpened in update loop, 
            # and allow retry if needed, or just fail gracefully.
            pass

        # Try to set resolution
        if self.video.isOpened():
            self.video.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.video.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        # Start background thread
        self.thread = threading.Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()

    def __del__(self):
        self.running = False
        if hasattr(self, 'video') and self.video.isOpened():
            self.video.release()

    def update(self):
        while self.running:
            if not self.video.isOpened():
                break
                
            success, frame = self.video.read()
            if not success:
                time.sleep(0.1)
                continue
            
            with self.lock:
                self.last_frame = frame.copy()
            
            # Auto Check-in Logic
            current_time = time.time()
            if current_time - self.last_check_time > self.check_interval:
                self.last_check_time = current_time
                self.process_attendance(frame)
            
            time.sleep(0.03) # ~30 FPS

    def get_frame(self):
        with self.lock:
            if self.last_frame is None:
                return None
            
            # Encode frame for web streaming
            ret, jpeg = cv2.imencode('.jpg', self.last_frame)
            return jpeg.tobytes()

    def get_snapshot(self):
        with self.lock:
            if self.last_frame is None:
                return None
            return self.last_frame.copy()

    def process_attendance(self, frame):
        # Detect
        faces = self.face_system.detect(frame)
        if len(faces) == 0:
            return

        # Pick largest face
        best_face = max(faces, key=lambda b: (b[2]-b[0]) * (b[3]-b[1]))
        x1, y1, x2, y2 = map(int, best_face)
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        
        face_img = frame[y1:y2, x1:x2]
        if face_img.size == 0: 
            return

        # Recognition
        emb = self.face_system.get_embedding(face_img)
        
        # Match
        users = database.get_users()
        max_sim = -1.0
        best_match = None
        
        for u in users:
            db_emb = np.frombuffer(u['embedding'], dtype=np.float32)
            sim = np.dot(emb, db_emb) / (np.linalg.norm(emb) * np.linalg.norm(db_emb) + 1e-6)
            if sim > max_sim:
                max_sim = sim
                best_match = u
        
        threshold = 0.5
        if best_match and max_sim > threshold:
            # Check cooldown? (e.g. don't punch in every 2 seconds)
            # For simplicity, we just log it. In production, check last attendance time.
            print(f"User {best_match['name']} identified ({max_sim:.2f})")
            
            # Check if user checked in recently (e.g. within 1 minute)
            # This requires DB query or caching. 
            # For this demo, we just insert.
            # Ideally: database.add_attendance_if_not_recent(...)
            
            # Using a simple cache here to avoid spamming DB
            # But let's just write to DB for now.
            database.add_attendance(best_match['id'], 'camera_auto', 'local_camera')

