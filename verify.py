#!/usr/bin/env python3
"""
EcoVision Security Sentinel - System Verification Script
Tests all critical components and dependencies
"""

import sys
import subprocess
from typing import Tuple, List

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def print_header():
    print(f"\n{Colors.BLUE}{'='*60}")
    print("EcoVision Security Sentinel - System Verification")
    print(f"{'='*60}{Colors.END}\n")

def check_python_version() -> bool:
    """Check if Python 3.9+ is installed"""
    version_info = sys.version_info
    if version_info.major >= 3 and version_info.minor >= 9:
        print(f"{Colors.GREEN}✓{Colors.END} Python {version_info.major}.{version_info.minor}.{version_info.micro}")
        return True
    else:
        print(f"{Colors.RED}✗{Colors.END} Python {version_info.major}.{version_info.minor} (need 3.9+)")
        return False

def check_import(module_name: str, package_name: str = None) -> bool:
    """Check if a Python module can be imported"""
    if package_name is None:
        package_name = module_name
    
    try:
        __import__(module_name)
        print(f"{Colors.GREEN}✓{Colors.END} {package_name}")
        return True
    except ImportError:
        print(f"{Colors.RED}✗{Colors.END} {package_name} (not installed)")
        return False

def check_command(cmd: str, name: str) -> bool:
    """Check if a command exists"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            shell=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print(f"{Colors.GREEN}✓{Colors.END} {name}")
            return True
        else:
            print(f"{Colors.RED}✗{Colors.END} {name}")
            return False
    except Exception as e:
        print(f"{Colors.RED}✗{Colors.END} {name} - {str(e)}")
        return False

def main():
    print_header()
    
    results: List[Tuple[str, bool]] = []
    
    # Check Python
    print(f"{Colors.BLUE}[1] Python Environment{Colors.END}")
    results.append(("Python 3.9+", check_python_version()))
    print()
    
    # Check Critical Dependencies
    print(f"{Colors.BLUE}[2] Critical Dependencies{Colors.END}")
    critical_packages = [
        ("fastapi", "FastAPI"),
        ("uvicorn", "Uvicorn"),
        ("websockets", "WebSockets"),
        ("pydantic", "Pydantic"),
        ("requests", "Requests"),
    ]
    for module, name in critical_packages:
        results.append((name, check_import(module, name)))
    print()
    
    # Check Optional Dependencies
    print(f"{Colors.BLUE}[3] Optional Dependencies{Colors.END}")
    optional_packages = [
        ("cv2", "OpenCV"),
        ("numpy", "NumPy"),
        ("torch", "PyTorch"),
        ("ultralytics", "YOLOv8"),
        ("PIL", "Pillow"),
    ]
    for module, name in optional_packages:
        results.append((name, check_import(module, name)))
    print()
    
    # Check External Commands
    print(f"{Colors.BLUE}[4] External Tools{Colors.END}")
    results.append(("Node.js", check_command("node --version", "Node.js")))
    results.append(("npm", check_command("npm --version", "npm")))
    results.append(("Git", check_command("git --version", "Git")))
    print()
    
    # Summary
    print(f"{Colors.BLUE}{'='*60}")
    print("Verification Summary")
    print(f"{'='*60}{Colors.END}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"\nResults: {passed}/{total} checks passed\n")
    
    if passed == total:
        print(f"{Colors.GREEN}✓ All checks passed! System is ready.{Colors.END}\n")
        print("Next steps:")
        print("1. Run: python server.py")
        print("2. Run: npm run dev")
        print("3. Open: http://localhost:3000\n")
        return 0
    else:
        failed = [name for name, result in results if not result]
        print(f"{Colors.RED}✗ Some checks failed:{Colors.END}")
        for name in failed:
            print(f"  - {name}")
        print()
        print("Fix missing dependencies with:")
        print("  pip install -r requirements.txt")
        print("  npm install")
        print()
        return 1

if __name__ == "__main__":
    sys.exit(main())
