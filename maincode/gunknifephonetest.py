import cv2
import time
import dxcam
import numpy as np
from ultralytics import YOLO

# --- 1. DXCAM HIGH-SPEED SETUP ---
# Grab monitor 0. Ensure OBS Windowed Projector is at top-left.
camera = dxcam.create(device_idx=0, output_color="BGR")
REGION = (0, 0, 1280, 720) # Capture area (Left, Top, Right, Bottom)
camera.start(region=REGION, target_fps=60, video_mode=True)

# --- 2. MODEL INITIALIZATION ---
# Load your specific weights and force them to the GPU
weights_path = r"D:\projects\EcoVisionCode\weights\weights.pt"
model = YOLO(weights_path).to('cuda')

# Define target classes and their visual markers
# Note: Ensure these names match your Roboflow labels exactly
TARGET_CLASSES = {
    'Gun': (0, 0, 255),    # Red
    'Knife': (0, 165, 255), # Orange
    'Phone': (255, 255, 0)  # Cyan
}

# --- 3. PERFORMANCE TUNING ---
CONFIDENCE_THRESHOLD = 0.30 
INFERENCE_SIZE = 416 # Lowering from 640 to 416 can double FPS on Nano

prev_time = 0
frame_count = 0

print("🔍 Weapon Detector Active: Targeting Gun, Knife, and Phone.")

while True:
    frame = camera.get_latest_frame()
    if frame is None: continue
    frame_count += 1

    # --- 4. DETECTION ENGINE ---
    # stream=True is more memory efficient for high FPS loops
    results = model.predict(
        source=frame, 
        conf=CONFIDENCE_THRESHOLD, 
        imgsz=INFERENCE_SIZE, 
        device=0, 
        verbose=False,
        stream=True
    )

    for r in results:
        boxes = r.boxes
        for box in boxes:
            # Get class name
            cls_id = int(box.cls[0])
            label = model.names[cls_id]

            # Only process our specific target classes
            if label in TARGET_CLASSES:
                # Coordinates
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                conf = float(box.conf[0])
                color = TARGET_CLASSES[label]

                # Draw Bounding Box & Label
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                tag = f"{label} {conf:.2f}"
                cv2.putText(frame, tag, (x1, y1 - 10), 1, 1, color, 2)

    # --- 5. FPS & SCREEN MANAGEMENT ---
    curr_time = time.time()
    fps = 1 / (curr_time - prev_time) if prev_time > 0 else 0
    prev_time = curr_time

    # Display HUD
    cv2.rectangle(frame, (0, 0), (250, 40), (0, 0, 0), -1)
    cv2.putText(frame, f"SENTINEL FPS: {fps:.1f}", (10, 25), 2, 0.6, (0, 255, 0), 1)

    cv2.imshow("EcoVision Weapon Detection", frame)

    # Prevent "Hall of Mirrors" by moving the window away from the capture region
    if frame_count == 1:
        cv2.moveWindow("EcoVision Weapon Detection", 1300, 50)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

camera.stop()
cv2.destroyAllWindows()