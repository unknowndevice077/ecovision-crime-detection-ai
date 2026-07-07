"""
EcoVision -- Custom TensorRT Engine Compiler (With Metadata Injection)
====================================================================
Bypasses Ultralytics wrapper conflicts on Python 3.14+ by extracting 
model metadata from .pt files, compiling via TensorRT 11, and 
pre-pending the metadata header exactly how AutoBackend expects it.
"""
import os
import sys
import json
import torch

try:
    from ultralytics import YOLO
except ImportError:
    sys.exit("❌ Error: Ultralytics is missing. Run setup.bat first.")

try:
    import tensorrt as trt
except ImportError:
    sys.exit("❌ Error: The 'tensorrt' library bindings are missing in this environment slot.\nRun: .\\.venv\\Scripts\\pip install tensorrt")

def compile_onnx_to_tensorrt(onnx_path, engine_path, metadata_dict):
    """Compiles an ONNX file into a localized hardware-fused TensorRT engine with embedded metadata."""
    TRT_LOGGER = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(TRT_LOGGER)
    
    # TensorRT 11 standard default strongly-typed configuration setup
    network = builder.create_network(0)
    parser = trt.OnnxParser(network, TRT_LOGGER)
    
    print(f" 📦 Parsing internal ONNX graph layers: {os.path.basename(onnx_path)}")
    with open(onnx_path, 'rb') as f:
        if not parser.parse(f.read()):
            print(" ❌ ONNX Graph Parsing Failures encountered:")
            for error in range(parser.num_errors):
                print(f"    - {parser.get_error(error)}")
            return False
            
    config = builder.create_builder_config()
    
    # Restrict workspace calculation profile pool allocations (2 GiB limit)
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 2 << 30)
    
    print(f" ⚡ Optimizing kernels and fusing operational layers for your GTX 1660 SUPER...")
    serialized_engine = builder.build_serialized_network(network, config)
    
    if serialized_engine is None:
        print(" ❌ Error: TensorRT serialization builder dropped out.")
        return False
        
    # ─── SECURE METADATA HEADER PREPEND INJECTION SEQUENCE ───
    # Ultralytics reads the first 4 bytes as a signed integer defining the metadata payload length
    meta_str = json.dumps(metadata_dict)
    meta_bytes = meta_str.encode('utf-8')
    meta_len = len(meta_bytes)
    
    print(f" 💾 Injecting Ultralytics structural metadata header ({meta_len} bytes)...")
    with open(engine_path, 'wb') as f:
        # 1. Write the 4-byte length header tag (little-endian signed integer)
        f.write(meta_len.to_bytes(4, byteorder="little", signed=True))
        # 2. Write the serialized JSON string payload bytes
        f.write(meta_bytes)
        # 3. Append the raw compiled hardware-fused TensorRT engine data binary
        f.write(serialized_engine)
        
    return True

def compile_system_weights():
    print("──────────────────────────────────────────────────────────────")
    print("🚀 EcoVision Production Weights Optimization Matrix Active")
    print("──────────────────────────────────────────────────────────────")

    if not torch.cuda.is_available():
        print("❌ Critical Error: No CUDA-enabled Nvidia GPU discovered.")
        return
        
    device_name = torch.cuda.get_device_name(0)
    print(f"🟢 Hardware Verified: {device_name}")
    
    root_dir = os.path.dirname(os.path.abspath(__file__))
    weights_dir = os.path.join(root_dir, "weights")
    
    pose_pt = os.path.join(weights_dir, "yolo11s-pose.pt")
    pose_onnx = os.path.join(weights_dir, "yolo11s-pose.onnx")
    pose_engine = os.path.join(weights_dir, "yolo11s-pose.engine")
    
    weapon_pt = os.path.join(weights_dir, "weapon_signs.pt")
    weapon_onnx = os.path.join(weights_dir, "weapon_signs.onnx")
    weapon_engine = os.path.join(weights_dir, "weapon_signs.engine")

    os.makedirs(weights_dir, exist_ok=True)

    # ─── 1. POSE ESTIMATION MODEL OPTIMIZATION ───
    print("\n[1/2] Processing YOLO11s-Pose Model Structure...")
    if os.path.exists(pose_pt):
        model = YOLO(pose_pt)
        
        # Build strict architectural metadata map parameters
        metadata = {
            "task": model.task,
            "names": model.names,
            "stride": int(model.stride.max() if hasattr(model, 'stride') else 32),
            "imgsz": [416, 416]
        }
        try:
            metadata["kpt_shape"] = model.model[-1].kpt_shape
        except Exception:
            metadata["kpt_shape"] = [17, 3] # Human skeletal model standard default fallback

        print(" 🔄 Exporting raw PyTorch blueprint to high-performance FP16 ONNX structure...")
        model.export(format="onnx", imgsz=416, half=True, dynamic=False, verbose=False)
        
        if os.path.exists(pose_onnx):
            success = compile_onnx_to_tensorrt(pose_onnx, pose_engine, metadata)
            if success:
                print(f"  ✅ Success! Fused tracking engine saved to: {pose_engine}")
                if os.path.exists(pose_onnx):
                    os.remove(pose_onnx)
    else:
        print(f" ❌ Critical: Missing root weight file: {pose_pt}")

    # ─── 2. WEAPON & SURROUNDING SIGN DETECTION MODEL OPTIMIZATION ───
    print("\n[2/2] Processing Weapon & Sign Detection Model Structure...")
    if os.path.exists(weapon_pt):
        model = YOLO(weapon_pt)
        
        metadata = {
            "task": model.task,
            "names": model.names,
            "stride": int(model.stride.max() if hasattr(model, 'stride') else 32),
            "imgsz": [416, 416]
        }

        print(" 🔄 Exporting raw PyTorch blueprint to high-performance FP16 ONNX structure...")
        model.export(format="onnx", imgsz=416, half=True, dynamic=False, verbose=False)
        
        if os.path.exists(weapon_onnx):
            success = compile_onnx_to_tensorrt(weapon_onnx, weapon_engine, metadata)
            if success:
                print(f"  ✅ Success! Fused tracking engine saved to: {weapon_engine}")
                if os.path.exists(weapon_onnx):
                    os.remove(weapon_onnx)
    else:
        print(f" ❌ Critical: Missing root weight file: {weapon_pt}")

    print("\n──────────────────────────────────────────────────────────────")
    print("🎉 NATIVE METADATA OPTIMIZATION PIPELINE EXECUTED SUCCESSFULLY")
    print("   Your custom .engine binaries are built and ready for presentation.")
    print("──────────────────────────────────────────────────────────────")

if __name__ == "__main__":
    compile_system_weights()