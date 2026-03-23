import os
import requests
import zipfile
import subprocess
import time
import argparse

MODEL_DIR = 'models'
BUFFALO_S_URL = 'https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip'

def download_file(url, save_path):
    print(f"Downloading {url} to {save_path}...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        with requests.get(url, stream=True, headers=headers, timeout=30) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            downloaded = 0
            start_time = time.time()

            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)

                    # Calculate progress
                    elapsed = time.time() - start_time
                    if elapsed > 0:
                        speed = downloaded / elapsed / 1024 / 1024  # MB/s
                        if total_size > 0:
                            percent = downloaded / total_size * 100
                            bar_len = 40
                            filled = int(bar_len * downloaded / total_size)
                            bar = '█' * filled + '░' * (bar_len - filled)
                            print(f"\r[{bar}] {percent:.1f}% {downloaded/1024/1024:.1f}/{total_size/1024/1024:.1f}MB {speed:.2f}MB/s", end='')
                        else:
                            print(f"\rDownloaded {downloaded/1024/1024:.1f}MB @ {speed:.2f}MB/s", end='')

        print("\n✓ Download complete.")
        return True
    except Exception as e:
        print(f"\n✗ Error downloading: {e}")
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
    parser = argparse.ArgumentParser(description='Download and convert face recognition models')
    parser.add_argument('--download-only', action='store_true', help='Only download models, skip conversion')
    parser.add_argument('--convert-only', action='store_true', help='Only convert models, skip download')
    parser.add_argument('--force', action='store_true', help='Force re-download and re-convert')
    args = parser.parse_args()

    if not os.path.exists(MODEL_DIR):
        os.makedirs(MODEL_DIR)

    zip_path = os.path.join(MODEL_DIR, 'buffalo_s.zip')
    det_onnx = os.path.join(MODEL_DIR, 'det_500m.onnx')
    if not os.path.exists(det_onnx):
        det_onnx = os.path.join(MODEL_DIR, 'det_10g.onnx')
    rec_onnx = os.path.join(MODEL_DIR, 'w600k_mbf.onnx')
    det_om = os.path.join(MODEL_DIR, 'face_detection.om')
    rec_om = os.path.join(MODEL_DIR, 'face_recognition.om')

    # Check current status
    has_zip = os.path.exists(zip_path)
    has_onnx = os.path.exists(det_onnx) and os.path.exists(rec_onnx)
    has_om = os.path.exists(det_om) and os.path.exists(rec_om)

    print("=== Model Status ===")
    print(f"ZIP file: {'✓' if has_zip else '✗'}")
    print(f"ONNX models: {'✓' if has_onnx else '✗'}")
    print(f"OM models: {'✓' if has_om else '✗'}")
    print()

    # Determine what to do
    need_download = not has_onnx or args.force
    need_convert = not has_om or args.force

    if args.convert_only:
        need_download = False
    elif args.download_only:
        need_convert = False

    # If everything exists and no force, skip
    if has_om and not args.force and not args.download_only and not args.convert_only:
        print("✓ All models ready. Nothing to do.")
        print("  Use --force to re-download and re-convert")
        return

    # Download phase
    if need_download and not args.convert_only:
        print("=== Download Phase ===")
        if has_zip and not args.force:
            print("✓ ZIP file exists, skipping download")
        else:
            if args.force and has_zip:
                print("Removing existing ZIP file...")
                os.remove(zip_path)

            if not download_file(BUFFALO_S_URL, zip_path):
                print("✗ Download failed")
                if not has_onnx:
                    return

        # Unzip if needed
        if os.path.exists(zip_path) and not has_onnx:
            unzip_file(zip_path, MODEL_DIR)
        elif has_onnx:
            print("✓ ONNX models exist, skipping unzip")

    # Convert phase
    if need_convert and not args.download_only:
        print("\n=== Conversion Phase ===")

        # Re-check ONNX paths
        det_onnx = os.path.join(MODEL_DIR, 'det_500m.onnx')
        if not os.path.exists(det_onnx):
            det_onnx = os.path.join(MODEL_DIR, 'det_10g.onnx')

        if os.path.exists(det_onnx):
            if args.force and os.path.exists(det_om):
                print("Removing existing face_detection.om...")
                os.remove(det_om)

            if not os.path.exists(det_om):
                convert_to_om(det_onnx, os.path.join(MODEL_DIR, 'face_detection'), "input.1:1,3,640,640")
            else:
                print("✓ face_detection.om exists, skipping")
        else:
            print("✗ Detection ONNX not found")

        if os.path.exists(rec_onnx):
            if args.force and os.path.exists(rec_om):
                print("Removing existing face_recognition.om...")
                os.remove(rec_om)

            if not os.path.exists(rec_om):
                convert_to_om(rec_onnx, os.path.join(MODEL_DIR, 'face_recognition'), "input.1:1,3,112,112")
            else:
                print("✓ face_recognition.om exists, skipping")
        else:
            print("✗ Recognition ONNX not found")

    print("\n=== Done ===")
    if os.path.exists(det_om) and os.path.exists(rec_om):
        print("✓ All models ready!")
    else:
        print("⚠ Some models are missing")

if __name__ == '__main__':
    main()
