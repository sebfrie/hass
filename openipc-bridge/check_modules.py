#!/usr/bin/env python3
"""Check installed modules inside the add-on"""
import sys
import importlib

def check_module(module_name):
    """Check that a module is available"""
    try:
        module = importlib.import_module(module_name)
        version = getattr(module, '__version__', 'unknown')
        print(f"✅ {module_name} - OK (version: {version})")
        return True
    except ImportError as e:
        print(f"❌ {module_name} - {e}")
        return False

def main():
    """Main function"""
    print("="*50)
    print("Checking Python modules inside the add-on")
    print("="*50)
    
    modules = [
        "flask",
        "requests",
        "cv2",
        "numpy",
        "PIL",
        "pyzbar",
        "gtts"
    ]
    
    failed = []
    for module in modules:
        if not check_module(module):
            failed.append(module)
    
    print("="*50)
    if failed:
        print(f"❌ Failed modules: {len(failed)}")
        sys.exit(1)
    else:
        print("✅ All modules loaded successfully!")
        sys.exit(0)

if __name__ == "__main__":
    main()