# Intelligent Face Recognition Attendance System

## Model Conversion

The face recognition model needs to be in the `.om` format to run on the Ascend NPU. You can convert the provided ONNX model to the `.om` format using the Ascend Tensor Compiler (ATC).

**Prerequisites:**

*   Ascend CANN Toolkit installed and configured.
*   The `atc` command-line tool available in your environment.

**Command:**

```bash
atc --model=models/mobilefacenet.onnx --framework=5 --output=models/mobilefacenet --input_format=NCHW --input_shape="actual_input_1:1,3,112,112" --soc_version=Ascend310 --log=info
```

**Explanation of Parameters:**

*   `--model`: Path to the input ONNX model.
*   `--framework`: Framework type (5 for ONNX).
*   `--output`: Path and name for the output `.om` model (the `.om` extension is added automatically).
*   `--input_format`: Input data format.
*   `--input_shape`: Shape of the input tensor.
*   `--soc_version`: The specific Ascend processor you are using (e.g., `Ascend310`).
*   `--log`: Log level.

After running this command, you should have a `mobilefacenet.om` file in your `models` directory, and the application should be able to load it correctly.


This project is a real-time face recognition attendance system designed to run on the Ascend 310B NPU. It uses a USB camera to capture video, detects faces using MTCNN, and recognizes individuals using MobileFaceNet.

## Project Structure

```
d:\ascend310-exp\case1/
├── datasets/              # Stores registered face images
│   └── .gitkeep
├── models/                # Stores the converted `.om` models
│   └── .gitkeep
├── utils/                 # Utility scripts for ACL and model processing
│   ├── __init__.py
│   ├── acl_resource.py
│   └── model_processor.py
├── main.py                # Main application script
├── register_face.py       # Script to enroll new users
├── requirements.txt       # Python dependencies
└── attendance.csv         # Log file for attendance records
```

## Setup and Installation

### 1. Hardware

- Ascend 310B Developer Kit
- USB Camera

### 2. Software

- **Operating System**: Ubuntu 20.04 or compatible
- **CANN Toolkit**: Version 7.0 or higher
- **Python**: Version 3.8.x

### 3. Installation Steps

1.  **Clone the repository:**

    ```bash
    git clone <your-repo-link>
    cd <your-repo-directory>
    ```

2.  **Install Python dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

3.  **Prepare the Models:**

    -   You need to have the `mobilefacenet.om` and `mtcnn.onnx` models.
    -   Place the `mobilefacenet.om` file in the `models/` directory.
    -   The `mtcnn.onnx` model is used by the `mtcnn` library and should be handled automatically if the library is installed correctly.

    *Note: The `.om` model must be converted from its original format (e.g., ONNX, TensorFlow) using the Ascend Tensor Compiler (ATC) tool provided with the CANN toolkit.*

    Example ATC command:

    ```bash
    atc --model=./mobilefacenet.onnx --framework=5 --output=./mobilefacenet --input_format=NCHW --input_shape="data:1,3,112,112" --soc_version=Ascend310B1
    ```

## How to Use

### 1. Register Faces

Before running the main application, you need to register the faces of the users you want to recognize.

1.  Connect a USB camera.
2.  Run the `register_face.py` script:

    ```bash
    python register_face.py
    ```

3.  Follow the on-screen prompts:
    -   Enter the name of the person.
    -   A window will appear showing the camera feed.
    -   Press the **'S'** key to save a picture. The system will collect 20 images.
    -   Press the **'Q'** key to quit at any time.

    The images will be saved in a new folder under the `datasets/` directory, named after the person.

### 2. Run the Attendance System

Once you have registered the faces, you can start the attendance system.

1.  Run the `main.py` script:

    ```bash
    python main.py
    ```

2.  The system will open a window displaying the camera feed.
3.  When a registered person is detected, their name will be displayed in green, and their attendance will be logged in the `attendance.csv` file.
4.  Unknown faces will be marked as "Unknown" in red.
5.  Press the **'Q'** key to stop the application.

### 3. View Attendance Records

The attendance records are saved in `attendance.csv` with the following format:

```csv
Name,Date,Time
John_Doe,2023-10-27,09:00:15
```