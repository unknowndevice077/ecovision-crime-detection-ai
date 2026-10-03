"""
The machine's own settings file: <writable dir>/config.json.

backend.py and maincode/main.py both load the shipped config.json, overlay
config.<APP_ENV>.json, then overlay this file last. It should hold only what
THIS machine changed -- the secret key, the camera it watches, a detector an
admin switched off -- so everything else keeps following the shipped
config.json.

BUG FOUND 2026-10-04: three writers dumped the WHOLE merged config here (the
AI core on its first run, every camera switch, every model on/off toggle).
Because this file is merged last, that snapshot then pinned every model path,
threshold and setting on the machine forever: an update shipping new weights
or a retuned threshold changed nothing there. And in a dev run, where the
writable dir is the repo itself, a toggle wrote config.development.json's
overrides (esp32 disabled, reload on) into the repo's config.json.

Now writers record only the keys they change, in a file marked with
OVERLAY_VERSION. A file without the mark is an old full snapshot: only the
genuinely per-machine keys (LEGACY_KEYS) are taken from it, so it can no
longer override what a newer shipped config.json says.
"""
import json
import os

OVERLAY_VERSION = 2
_VERSION_KEY = "_overlay_version"

# What the old snapshot writers legitimately owned. Detector on/off switches
# are not here on purpose: an old snapshot can't tell a switch someone flipped
# from one it merely copied, and the shipped value is the safe default.
LEGACY_KEYS = (
    ("auth", "secret_key"),
    ("camera", "index"),
    ("camera", "source"),
)


def _same_file(a, b):
    try:
        return bool(a and b) and os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _get(d, keys):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return None, False
        d = d[k]
    return d, True


def _set(d, keys, value):
    for k in keys[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


def _legacy_subset(data):
    out = {}
    for keys in LEGACY_KEYS:
        value, found = _get(data, keys)
        if found:
            _set(out, keys, value)
    return out


def load_overlay(path, base_path=None):
    """Settings this machine overrides, ready to deep-merge over the shipped
    config. {} when there is no file, it is unreadable, or it IS the shipped
    file (dev runs point the writable dir at the repo)."""
    if not path or not os.path.exists(path) or _same_file(path, base_path):
        return {}
    try:
        data = _read_json(path)
    except Exception as e:
        print(f"⚠️  [CONFIG] Ignoring unreadable {path}: {e}")
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get(_VERSION_KEY) == OVERLAY_VERSION:
        return {k: v for k, v in data.items() if k != _VERSION_KEY}
    return _legacy_subset(data)


def save_overrides(path, updates, base_path=None):
    """Records `updates` ({("detection", "robbery", "enabled"): False, ...})
    in the machine's settings file, atomically, touching no other key.

    When the writable file is the shipped config.json itself (dev), the
    change is made in place in that file -- still only the given keys."""
    in_place = _same_file(path, base_path)
    if in_place:
        data = _read_json(path)
    elif os.path.exists(path):
        try:
            existing = _read_json(path)
        except Exception:
            existing = {}
        if isinstance(existing, dict) and existing.get(_VERSION_KEY) == OVERLAY_VERSION:
            data = existing
        else:
            data = _legacy_subset(existing if isinstance(existing, dict) else {})
    else:
        data = {}
    if not in_place:
        data[_VERSION_KEY] = OVERLAY_VERSION
    for keys, value in updates.items():
        _set(data, tuple(keys), value)

    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
