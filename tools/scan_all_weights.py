import os
import sys

try:
    import torch
    from ultralytics import YOLO
except ImportError:
    print("❌ Error: Required libraries missing. Run: pip install torch ultralytics")
    sys.exit(1)

# --- CONFIGURATION ---
WEIGHTS_FOLDER = r"D:\projects\EcoVisionCode\weights"

def diagnostic_scan():
    if not os.path.exists(WEIGHTS_FOLDER):
        print(f"❌ Error: The directory '{WEIGHTS_FOLDER}' does not exist.")
        return

    pt_files = [f for f in os.listdir(WEIGHTS_FOLDER) if f.endswith('.pt')]
    
    if not pt_files:
        print(f"ℹ️ No .pt files found inside: {WEIGHTS_FOLDER}")
        return

    print("=" * 75)
    print(f"🔍 SENTINEL METADATA DECODER: Analyzing Weight Layers")
    print("=" * 75)

    for idx, filename in enumerate(pt_files, 1):
        file_path = os.path.join(WEIGHTS_FOLDER, filename)
        print(f"\n[{idx}/{len(pt_files)}] 📦 MODEL: {filename}")
        
        try:
            # Safely initialize using the official Ultralytics wrapper
            model = YOLO(file_path)
            
            # Extract basic tracking configurations
            task_type = getattr(model, 'task', 'Unknown')
            class_map = getattr(model, 'names', {})
            print(f"   🔹 Task Mode       : {task_type.upper()}")
            print(f"   🔹 Class Dict      : {class_map}")

            # Dig into the underlying PyTorch dictionary structure if available
            if hasattr(model, 'ckpt') and model.ckpt is not None:
                ckpt_keys = list(model.ckpt.keys())
                print(f"   🔹 Checkpoint Keys : {ckpt_keys}")
                
                # Check for fitness scores or embedded training performance evaluations
                fitness = model.ckpt.get('best_fitness', None)
                metrics = model.ckpt.get('train_metrics', None)
                
                if fitness is not None:
                    print(f"   🔹 Saved Fitness   : {fitness}")
                
                if metrics:
                    print(f"   🔹 Embedded mAP Scores Found:")
                    for k, v in metrics.items():
                        if 'mAP' in k:
                            val_display = f"{v*100:.2f}%" if isinstance(v, float) else v
                            print(f"      👉 {k}: {val_display}")
                else:
                    print("   ⚠️  Notice: This file has been stripped of internal training logs.")
            else:
                print("   ⚠️  Notice: Raw checkpoint dictionary is completely unavailable.")

        except Exception as e:
            # This will print the actual technical Python error holding up the system
            print(f"   ❌ Engine Exception: {str(e)}")
            
        print("-" * 75)

if __name__ == "__main__":
    diagnostic_scan()