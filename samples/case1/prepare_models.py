import os
import requests
import zipfile
import subprocess
import time

MODEL_DIR = 'models'
BUFFALO_S_URL = 'https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip'

def download_file(url, save_path):
    print(f"Downloading {url} to {save_path}...")
    try:
        # Add timeout and user agent
        headers = {'User-Agent': 'Mozilla/5.0'}
        with requests.get(url, stream=True, headers=headers, timeout=30) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            downloaded = 0
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    # Simple progress
                    if total_size > 0 and downloaded % (1024*1024) == 0:
                        print(f"\rDownloaded {downloaded/1024/1024:.1f} MB / {total_size/1024/1024:.1f} MB", end='')
        print("\nDownload complete.")
        return True
    except Exception as e:
        print(f"\nError downloading: {e}")
        # Clean up partial file
        if os.path.exists(save_path):
            os.remove(save_path)
        return False

def unzip_file(zip_path, extract_to):
    print(f"Unzipping {zip_path}...")
    try:
        if not zipfile.is_zipfile(zip_path):
             print("Error: File is not a valid zip file.")
             return False
             
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
        print("Unzip complete.")
        return True
    except Exception as e:
        print(f"Error unzipping: {e}")
        return False

def convert_to_om(onnx_path, output_name, input_shape=None):
    # This function constructs the ATC command
    # SOC_VERSION should be checked from npu-smi, here assuming Ascend310B4 as seen in logs
    soc_version = "Ascend310B4" 
    
    cmd = [
        "atc",
        f"--model={onnx_path}",
        "--framework=5",  # 5 is ONNX
        f"--output={output_name}",
        f"--soc_version={soc_version}",
    ]
    
    if input_shape:
        cmd.append(f"--input_shape={input_shape}")
        
    print(f"Converting {onnx_path} to OM...")
    print("Command:", " ".join(cmd))
    
    try:
        subprocess.run(cmd, check=True)
        print("Conversion successful.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Conversion failed: {e}")
        return False

def main():
    if not os.path.exists(MODEL_DIR):
        os.makedirs(MODEL_DIR)
        
    zip_path = os.path.join(MODEL_DIR, 'buffalo_s.zip')
    
    # 1. Download
    if os.path.exists(zip_path):
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                if zip_ref.testzip() is not None:
                    print("Zip file corrupted, deleting...")
                    os.remove(zip_path)
        except zipfile.BadZipFile:
             print("Invalid zip file, deleting...")
             os.remove(zip_path)

    if not os.path.exists(zip_path):
        if not download_file(BUFFALO_S_URL, zip_path):
            print("Download failed. Attempting to use existing ONNX files if present...")
    
    # 2. Unzip (only if zip exists)
    if os.path.exists(zip_path):
        unzip_file(zip_path, MODEL_DIR)

    # 3. Convert
    det_onnx = os.path.join(MODEL_DIR, 'det_10g.onnx') 
    if not os.path.exists(det_onnx):
         det_onnx = os.path.join(MODEL_DIR, 'det_500m.onnx')
         
    rec_onnx = os.path.join(MODEL_DIR, 'w600k_mbf.onnx')
    
    if os.path.exists(det_onnx):
        # RetinaFace input: 1,3,640,640
        if not os.path.exists(os.path.join(MODEL_DIR, 'face_detection.om')):
            convert_to_om(det_onnx, os.path.join(MODEL_DIR, 'face_detection'), "input.1:1,3,640,640")
        else:
            print("face_detection.om already exists. Skipping conversion.")
    else:
        print("Detection ONNX model not found.")

    if os.path.exists(rec_onnx):
        # ArcFace input: 1,3,112,112
        if not os.path.exists(os.path.join(MODEL_DIR, 'face_recognition.om')):
            convert_to_om(rec_onnx, os.path.join(MODEL_DIR, 'face_recognition'), "input.1:1,3,112,112")
        else:
            print("face_recognition.om already exists. Skipping conversion.")
    else:
        print("Recognition ONNX model not found.")

if __name__ == '__main__':
    main()
