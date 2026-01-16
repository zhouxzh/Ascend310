import cv2
import os
from mtcnn.mtcnn import MTCNN
import numpy as np

def register_face():
    # Prompt for user's name
    name = input("Please enter your name: ")
    if not name:
        print("Name cannot be empty. Exiting.")
        return

    # Create a directory for the user
    save_path = os.path.join('datasets', name)
    if not os.path.exists(save_path):
        os.makedirs(save_path)

    # Initialize MTCNN for face detection
    detector = MTCNN()

    # Open the camera
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open camera.")
        return

    img_count = 0
    max_imgs = 20  # Number of images to save

    while img_count < max_imgs:
        # Read a frame from the camera
        ret, frame = cap.read()
        if not ret:
            print("Error: Could not read frame.")
            break

        # Detect faces in the frame
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        faces = detector.detect_faces(frame_rgb)

        # Draw rectangles around detected faces and display text
        for face in faces:
            x, y, width, height = face['box']
            cv2.rectangle(frame, (x, y), (x+width, y+height), (0, 255, 0), 2)

            # Display instructions
            cv2.putText(frame, f"Press 'S' to save. Saved: {img_count}/{max_imgs}", (10, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame, "Press 'Q' to quit.", (10, 60), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        # Show the frame
        cv2.imshow('Register Face - Press S to Save, Q to Quit', frame)

        # Wait for key press
        key = cv2.waitKey(1) & 0xFF

        if key == ord('s'):
            # Save the detected face
            if faces:
                # We only save the first detected face
                x, y, width, height = faces[0]['box']
                # Add some padding to the bounding box
                x1, y1 = max(0, x - 20), max(0, y - 20)
                x2, y2 = min(frame.shape[1], x + width + 20), min(frame.shape[0], y + height + 20)
                face_img = frame[y1:y2, x1:x2]

                # Resize to a standard size if needed, e.g., 160x160 for FaceNet models
                face_img = cv2.resize(face_img, (160, 160))

                # Save the image
                img_path = os.path.join(save_path, f"{img_count + 1}.jpg")
                cv2.imwrite(img_path, face_img)
                print(f"Saved image: {img_path}")
                img_count += 1
            else:
                print("No face detected to save.")

        elif key == ord('q'):
            print("Quitting registration.")
            break

    # Release the camera and destroy all windows
    cap.release()
    cv2.destroyAllWindows()

    if img_count == max_imgs:
        print(f"Successfully collected {max_imgs} images for {name}.")

if __name__ == '__main__':
    register_face()