"""
Run this INSIDE your activated .venv, from the repo root:

    .venv\\Scripts\\python.exe audit_requirements.py

What it does:
1. Recursively scans every .py file in the repo (so it also picks up
   optimize_weights.py, scan_all_weights.py, etc. -- not just the 4 files
   already reviewed) for top-level imports.
2. Maps each imported module name back to the pip package that provides it.
3. Walks each such package's installed metadata (Requires-Dist) recursively
   to build the full transitive closure -- i.e. everything pip would install
   anyway just because something you actually import depends on it.
4. Diffs that closure against requirements.txt and prints three buckets:
   DIRECTLY IMPORTED, PULLED IN TRANSITIVELY, and ORPHANED (candidates to
   actually remove).

This only tells you what's unused by CODE that exists in this repo right
now -- it can't know about e.g. a notebook, a CI step, or a script you run
manually that isn't a .py file under this tree.
"""
import ast
import pathlib
import re
import sys
from importlib import metadata

# Deliberately NOT using __file__ here -- some runners (VS Code's "Code
# Runner" extension in particular) can mangle it when they build the launch
# command. Using the current working directory means this just requires you
# to `cd` into the repo root before running, which is more predictable.
REPO_ROOT = pathlib.Path.cwd()

# Import name -> distribution name, for the common cases where they differ.
IMPORT_TO_DIST_OVERRIDES = {
    "cv2": "opencv-python",
    "yaml": "PyYAML",
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "dotenv": "python-dotenv",
}


def find_py_files():
    skip_dirs = {".venv", "node_modules", ".git", ".next", "build", "dist", "__pycache__"}
    for p in REPO_ROOT.rglob("*.py"):
        if not any(part in skip_dirs for part in p.parts):
            yield p


def top_level_imports():
    imports = set()
    for f in find_py_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imports.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:  # skip relative imports
                    imports.add(node.module.split(".")[0])
    return imports


def resolve_dist_name(import_name):
    if import_name in IMPORT_TO_DIST_OVERRIDES:
        return IMPORT_TO_DIST_OVERRIDES[import_name]
    try:
        # importlib.metadata can map top-level import names to dist names
        # via packages_distributions() on Python 3.10+
        mapping = metadata.packages_distributions()
        if import_name in mapping:
            return mapping[import_name][0]
    except Exception:
        pass
    return import_name


def transitive_closure(dist_names):
    seen = set()
    stack = list(dist_names)
    while stack:
        name = stack.pop()
        norm = re.sub(r"[-_.]+", "-", name).lower()
        if norm in seen:
            continue
        seen.add(norm)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        requires = dist.requires or []
        for req in requires:
            # strip environment markers / version specifiers, keep the name
            dep_name = re.split(r"[\s;<>=!\[]", req.strip(), 1)[0]
            if dep_name:
                stack.append(dep_name)
    return seen


def load_requirements():
    req_file = REPO_ROOT / "requirements.txt"
    if not req_file.exists():
        sys.exit(
            f"Couldn't find requirements.txt at {req_file}\n"
            f"Run this from your repo root (where requirements.txt actually lives), e.g.:\n"
            f"  cd D:\\projects\\EcoVisionCode\n"
            f"  .venv\\Scripts\\python.exe audit_requirements.py"
        )
    raw = req_file.read_bytes()
    # PowerShell's `> requirements.txt` redirect defaults to UTF-16LE with a
    # BOM on Windows -- reading that as plain UTF-8 silently corrupts roughly
    # every other "line". Detect the actual encoding instead of assuming.
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16-le")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16-be")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        text = raw.decode("utf-8")

    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[=<>!\[]", line, 1)[0].strip()
        if name:
            names.append(name)
    return names


def main():
    imports = top_level_imports()
    direct_dists = {resolve_dist_name(i) for i in imports}
    direct_norm = {re.sub(r"[-_.]+", "-", d).lower() for d in direct_dists}

    closure = transitive_closure(direct_dists)

    req_names = load_requirements()
    print(f"Found {len(imports)} distinct top-level imports across {sum(1 for _ in find_py_files())} .py files.\n")

    print("=== DEBUG: raw imports -> resolved distribution name ===")
    for i in sorted(imports):
        print(f"  {i:25s} -> {resolve_dist_name(i)}")
    print(f"\nDEBUG: normalized direct_norm set has {len(direct_norm)} entries: {sorted(direct_norm)}")
    print(f"DEBUG: transitive closure has {len(closure)} entries\n")

    directly_used, transitively_used, orphaned = [], [], []
    for name in req_names:
        norm = re.sub(r"[-_.]+", "-", name).lower()
        if norm in direct_norm:
            directly_used.append(name)
        elif norm in closure:
            transitively_used.append(name)
        else:
            orphaned.append(name)

    print("=== DIRECTLY IMPORTED (keep) ===")
    for n in sorted(directly_used):
        print(" ", n)

    print("\n=== PULLED IN TRANSITIVELY (pip installs these anyway -- safe to leave pinned or drop the explicit pin) ===")
    for n in sorted(transitively_used):
        print(" ", n)

    print("\n=== NOT FOUND AS A DEPENDENCY OF ANYTHING IMPORTED (review before removing) ===")
    for n in sorted(orphaned):
        print(" ", n)

    print(f"\n{len(orphaned)} of {len(req_names)} pinned packages look orphaned by the code in this repo.")
    print("Double check these aren't used by a .bat script, notebook, or something outside this repo's .py files before deleting.")


if __name__ == "__main__":
    main()