import cv2
import numpy as np
from ultralytics import YOLO
from collections import deque, Counter
from tqdm import tqdm  # Visual progress bar for rendering

# --- 1. PRO SETUP ---
pose_model = YOLO("yolo11n-pose.pt")
crime_judge = YOLO("merged.pt")

# SOURCE: Switch to your video file path
video_path = r"D:\Desktop\Grand Theft Auto V\Grand Theft Auto V 2026.02.09 - 03.22.29.10.mp4"
cap = cv2.VideoCapture(video_path)

# --- 2. CONFIG: PRECISION TUNING ---
STRIKE_VELOCITY = 35    # Lowered for frame-by-frame precision
HIT_ZONE_BUFFER = 50    # Larger 'Aura' to catch fast movements
VOTING_WINDOW = 8       # Faster response for recorded footage
COOLDOWN = 150          # ~5 seconds of alert at 30fps

# Video Properties
fps = cap.get(cv2.CAP_PROP_FPS)
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

# Writer Setup (Matches original video quality)
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out = cv2.VideoWriter('EcoVision_Final_Report_Render.mp4', fourcc, fps, (orig_w, orig_h))

# State Memory
threat_memory = {}
prev_joints = {}
state_buffers = {} 

SKELETON_EDGES = [(0,1),(0,2),(1,3),(2,4),(5,6),(5,7),(7,9),(6,8),(8,10),(11,12),(5,11),(6,12),(11,13),(13,15),(12,14),(14,16)]

print(f"🔥 Starting Clean Render: {total_frames} frames @ {fps} FPS")
pbar = tqdm(total=total_frames)

while cap.isOpened():
    success, frame = cap.read()
    if not success: break
    
    # STEP A: INFERENCE (Full Resolution for maximum accuracy)
    pose_res = pose_model.track(frame, persist=True, verbose=False)
    crime_res = crime_judge.predict(frame, conf=0.15, verbose=False)

    # 1. Map current Person Auras (Relational Detection)
    person_targets = {}
    for r in pose_res:
        if r.boxes is None or r.boxes.id is None: continue
        for b, tid in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.id.int().cpu().tolist()):
            person_targets[tid] = [
                b[0] - HIT_ZONE_BUFFER, b[1] - HIT_ZONE_BUFFER,
                b[2] + HIT_ZONE_BUFFER, b[3] + HIT_ZONE_BUFFER
            ]

    # 2. Extract Weapon Data for the frame
    active_weapons = []
    for r in crime_res:
        for box in r.boxes:
            active_weapons.append({"type": int(box.cls[0]), "box": box.xyxy[0].cpu().numpy()})

    # STEP B: RELATIONAL ANALYSIS
    for r in pose_res:
        if r.keypoints is None or r.boxes.id is None: continue
        
        kpts = r.keypoints.xy.cpu().numpy()
        ids = r.boxes.id.int().cpu().tolist()
        boxes = r.boxes.xyxy.cpu().numpy()

        for tid, joints, b in zip(ids, kpts, boxes):
            # 1. Physics: Euclidean Velocity
            vel = 0
            if tid in prev_joints:
                vel = np.linalg.norm(joints[9:11] - prev_joints[tid][9:11])
            prev_joints[tid] = joints

            # 2. Logic: Hitbox Incursion
            impact_detected = False
            if vel > STRIKE_VELOCITY:
                for target_id, aura in person_targets.items():
                    if tid == target_id: continue
                    for h_idx in [9, 10]: # Check both wrists
                        hx, hy = joints[h_idx]
                        if (aura[0] < hx < aura[2]) and (aura[1] < hy < aura[3]):
                            impact_detected = True

            # 3. Logic: Weapon Proximity (Immediate Assault)
            near_gun = any(w["type"] == 2 and not (w["box"][2]<b[0] or w["box"][0]>b[2] or w["box"][3]<b[1] or w["box"][1]>b[3]) for w in active_weapons)
            near_knife = any(w["type"] == 1 and not (w["box"][2]<b[0] or w["box"][0]>b[2] or w["box"][3]<b[1] or w["box"][1]>b[3]) for w in active_weapons)

            # 4. Temporal Consensus (Stop the Flicker)
            if tid not in state_buffers: state_buffers[tid] = deque(maxlen=VOTING_WINDOW)
            
            instant_state = "PERSON"
            if impact_detected or near_gun: instant_state = "CRITICAL"
            elif near_knife: instant_state = "SUSPICIOUS"
            
            state_buffers[tid].append(instant_state)
            vote = Counter(state_buffers[tid]).most_common(1)[0][0]

            # 5. Persistent State Machine
            if tid not in threat_memory: threat_memory[tid] = {"state": "PERSON", "timer": 0}
            
            if vote == "CRITICAL":
                threat_memory[tid] = {"state": "CRITICAL: ASSAULT", "timer": COOLDOWN}
            elif vote == "SUSPICIOUS":
                if "CRITICAL" not in threat_memory[tid]["state"]:
                    threat_memory[tid] = {"state": "SUSPICIOUS: ARMED", "timer": 60}
            
            if threat_memory[tid]["timer"] > 0:
                threat_memory[tid]["timer"] -= 1
            else:
                threat_memory[tid]["state"] = "PERSON"

            # STEP C: RENDER SKELETON & LABELS
            cur_state = threat_memory[tid]["state"]
            color = (0, 255, 0) # Neutral Green
            if "SUSPICIOUS" in cur_state: color = (0, 255, 255) # Warning Yellow
            if "CRITICAL" in cur_state: color = (0, 0, 255) # Alert Red

            # Smooth Anti-Aliased Skeleton
            for s, e in SKELETON_EDGES:
                p1, p2 = joints[s].astype(int), joints[e].astype(int)
                if p1.any() and p2.any():
                    cv2.line(frame, tuple(p1), tuple(p2), color, 3, cv2.LINE_AA)

            # High-Visibility Label at the Head
            cv2.putText(frame, f"{cur_state} #{tid}", (int(joints[0][0]), int(joints[0][1]-30)), 
                        cv2.FONT_HERSHEY_DUPLEX, 0.7, color, 2, cv2.LINE_AA)

    # WRITE FRAME TO FILE
    out.write(frame)
    pbar.update(1)

cap.release()
out.release()
pbar.close()
print("\n✅ Render Complete: EcoVision_Final_Report_Render.mp4")