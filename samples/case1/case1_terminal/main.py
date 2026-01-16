import cv2
import os
import numpy as np
from mtcnn.mtcnn import MTCNN
from sklearn.metrics.pairwise import cosine_similarity
from utils.acl_resource import AclResource
from utils.model_processor import ModelProcessor
import datetime

# --- Configuration ---
FACE_RECOGNITION_MODEL_PATH = 'models/mobilefacenet.om'
MTCNN_PNET_PATH = 'models/pnet.onnx'
MTCNN_RNET_PATH = 'models/rnet.onnx'
MTCNN_ONET_PATH = 'models/onet.onnx'
DATASET_PATH = 'datasets'
ATTENDANCE_FILE = 'attendance.csv'
SIMILARITY_THRESHOLD = 0.7

# --- Helper Functions ---

def preprocess_image(img, target_size=(112, 112)):
    """Preprocesses an image for the face recognition model."""
    # Resize and normalize
    img = cv2.resize(img, target_size)
    img = img.astype(np.float32) / 255.0
    img = (img - 0.5) / 0.5  # Normalize to [-1, 1]
    img = np.transpose(img, (2, 0, 1))  # HWC to CHW
    return np.expand_dims(img, axis=0) # Add batch dimension

def load_known_faces(model_processor):
    """Loads known face embeddings from the dataset directory."""
    known_face_embeddings = []
    known_face_names = []

    if not os.path.exists(DATASET_PATH):
        print(f"Dataset directory not found: {DATASET_PATH}")
        return known_face_embeddings, known_face_names

    for name in os.listdir(DATASET_PATH):
        user_dir = os.path.join(DATASET_PATH, name)
        if os.path.isdir(user_dir):
            user_embeddings = []
            for img_name in os.listdir(user_dir):
                img_path = os.path.join(user_dir, img_name)
                img = cv2.imread(img_path)
                if img is not None:
                    # Preprocess for MobileFaceNet
                    preprocessed_img = preprocess_image(img)
                    # Get embedding
                    embedding = model_processor.predict(preprocessed_img)[0]
                    user_embeddings.append(embedding.flatten())
            
            if user_embeddings:
                # Average the embeddings for a more robust representation
                known_face_embeddings.append(np.mean(user_embeddings, axis=0))
                known_face_names.append(name)
                print(f"Loaded face embeddings for: {name}")

    return np.array(known_face_embeddings), known_face_names

def log_attendance(name):
    """Logs the attendance of a person to a CSV file."""
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    # Check if the file exists to write headers
    file_exists = os.path.isfile(ATTENDANCE_FILE)
    
    with open(ATTENDANCE_FILE, 'a', newline='') as f:
        if not file_exists:
            f.write("Name,Date,Time\n")
        
        # Simple check to avoid duplicate entries in a short time
        # A more robust solution would check the last entry time for the user
        f.write(f"{name},{date_str},{time_str}\n")
    print(f"Attendance logged for {name} at {time_str}")

# --- Main Application ---
def main():
    # Initialize Ascend NPU resources
    with AclResource() as acl_resource:
        # Load face recognition model
        face_recognition_proc = ModelProcessor(acl_resource, FACE_RECOGNITION_MODEL_PATH)
        face_recognition_proc.load_model()

        # Load known faces
        known_face_embeddings, known_face_names = load_known_faces(face_recognition_proc)

        if not known_face_names:
            print("No known faces loaded. Please register faces first using register_face.py")
            return

        # Initialize face detector (MTCNN)
        detector = MTCNN()

        # Start video capture
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("Error: Could not open camera.")
            return

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Detect faces
            faces = detector.detect_faces(frame)

            for face in faces:
                x, y, w, h = face['box']
                x1, y1, x2, y2 = max(0, x), max(0, y), x + w, y + h
                face_img = frame[y1:y2, x1:x2]

                # Get embedding for the detected face
                preprocessed_face = preprocess_image(face_img)
                current_embedding = face_recognition_proc.predict(preprocessed_face)[0].flatten()

                # Compare with known faces
                similarities = cosine_similarity([current_embedding], known_face_embeddings)[0]
                best_match_index = np.argmax(similarities)
                
                name = "Unknown"
                color = (0, 0, 255) # Red for unknown

                if similarities[best_match_index] > SIMILARITY_THRESHOLD:
                    name = known_face_names[best_match_index]
                    color = (0, 255, 0) # Green for known
                    log_attendance(name) # Log attendance

                # Draw bounding box and name
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, name, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)

            # Display the resulting frame
            cv2.imshow('Face Recognition Attendance System', frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        # Cleanup
        cap.release()
        cv2.destroyAllWindows()
        face_recognition_proc.unload_model()

if __name__ == '__main__':
    main()