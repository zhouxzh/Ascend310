import cv2
import numpy as np
from ascend_inference import FaceSystem

def test_inference():
    print("Initializing FaceSystem...")
    fs = FaceSystem()
    
    # Create a dummy image (random noise) or black image
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.circle(img, (320, 240), 50, (255, 255, 255), -1) # Draw a "face"
    
    print("Running detection...")
    faces = fs.detect(img)
    print(f"Detected {len(faces)} faces.")
    
    # Fake a face crop
    face_crop = img[200:300, 280:380]
    face_crop = cv2.resize(face_crop, (112, 112))
    
    print("Running recognition...")
    emb = fs.get_embedding(face_crop)
    print(f"Embedding shape: {emb.shape}")
    print(f"Embedding sample: {emb[:5]}")
    
    fs.release()

if __name__ == "__main__":
    test_inference()
