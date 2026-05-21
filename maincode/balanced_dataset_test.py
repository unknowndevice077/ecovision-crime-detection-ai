import os
import yaml
from collections import Counter

# --- CONFIG: POINT TO YOUR DATASET ---
# Change this path to where your 'train', 'valid', and 'data.yaml' are located
DATASET_ROOT = r"D:\projects\EcoVisionCode\dataset" 

def run_audit():
    yaml_path = os.path.join(DATASET_ROOT, "data.yaml")
    
    if not os.path.exists(yaml_path):
        print(f"❌ Error: data.yaml not found at {yaml_path}")
        return

    # 1. Load the Class Names from YAML
    with open(yaml_path, 'r') as f:
        data = yaml.safe_load(f)
        class_names = data.get('names', {})
        # Some YAMLs use lists, some use dicts. Standardize to dict.
        if isinstance(class_names, list):
            class_names = {i: name for i, name in enumerate(class_names)}

    print(f"📂 Auditing: {DATASET_ROOT}")
    print(f"🏷️  YAML Classes Detected: {class_names}\n")

    # 2. Count Labels in the 'train' folder
    train_labels_path = os.path.join(DATASET_ROOT, "train", "labels")
    if not os.path.exists(train_labels_path):
        print(f"❌ Error: 'train/labels' folder not found!")
        return

    label_counts = Counter()
    total_files = 0

    for file in os.listdir(train_labels_path):
        if file.endswith(".txt"):
            total_files += 1
            with open(os.path.join(train_labels_path, file), 'r') as f:
                for line in f:
                    try:
                        class_id = int(line.split()[0])
                        label_counts[class_id] += 1
                    except:
                        continue

    # 3. Print the Report
    print(f"📊 Audit Results ({total_files} images checked):")
    print("-" * 45)
    print(f"{'ID':<5} | {'Class Name':<15} | {'Count':<10} | {'Status'}")
    print("-" * 45)
    
    # Calculate average for balance check
    avg_count = sum(label_counts.values()) / len(class_names) if class_names else 0

    for cid in sorted(class_names.keys()):
        count = label_counts[cid]
        name = class_names[cid]
        
        # Balance Check: Within 20% of average?
        is_balanced = "✅ OK" if (count > avg_count * 0.8) else "⚠️  LOW"
        if count == 0: is_balanced = "❌ EMPTY"

        print(f"{cid:<5} | {name:<15} | {count:<10} | {is_balanced}")
    print("-" * 45)

if __name__ == "__main__":
    run_audit()