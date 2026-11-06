"""ONVIF PTZ control for the dashboard's camera panel.

SCOPE, stated up front: this drives a real camera over ONVIF, the industry
standard that most Tapo PTZ models expose once a "camera account" is created
in the Tapo app. It cannot control the public YouTube CCTV streams used for
model validation -- those are other people's cameras, read-only by nature.

Capabilities are QUERIED from the device, never assumed. A camera without
zoom motors reports zoom unsupported and the dashboard greys the control out,
rather than showing a button that silently does nothing. Same for presets.

Two-way audio is deliberately NOT implemented here. It is not part of the
ONVIF PTZ service; Tapo carries it over a proprietary channel with no public
specification, so anything offered here would be guesswork. It is listed as
unsupported by get_capabilities() so the UI can say so plainly instead of
presenting a dead control.

Configuration (2026-11-03): per registered camera. The host, username and
password are taken from the camera's own RTSP URL -- ONVIF uses the same
account on effectively every vendor -- and only the ONVIF port is stored
separately (cameras.onvif_port). The URL never leaves the backend for anyone
without manage_cameras, so this adds no new place credentials are exposed.
Before this, PTZ read only PTZ_CAMERA_HOST/PORT/USER/PASS from the
environment, which an installed app gives no way to set, so PTZ could never
be configured outside a developer machine. The environment variables still
work as a fallback when no camera is named.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlparse

try:
    from onvif import ONVIFCamera  # type: ignore
    _ONVIF_AVAILABLE = True
except Exception:  # pragma: no cover - import guard only
    ONVIFCamera = None  # type: ignore
    _ONVIF_AVAILABLE = False


class PTZNotConfigured(RuntimeError):
    """Raised when PTZ is requested but no camera credentials are set."""


class PTZUnsupported(RuntimeError):
    """Raised when the connected camera does not support the requested axis."""


@dataclass
class PTZConfig:
    host: str
    port: int
    user: str
    password: str

    @classmethod
    def from_env(cls) -> Optional["PTZConfig"]:
        host = os.environ.get("PTZ_CAMERA_HOST", "").strip()
        user = os.environ.get("PTZ_CAMERA_USER", "").strip()
        password = os.environ.get("PTZ_CAMERA_PASS", "").strip()
        if not (host and user and password):
            return None
        try:
            port = int(os.environ.get("PTZ_CAMERA_PORT", "2020"))
        except ValueError:
            port = 2020
        return cls(host=host, port=port, user=user, password=password)

    @classmethod
    def from_camera(cls, url: str, onvif_port) -> "PTZConfig":
        """From a registered camera: host and login out of its RTSP URL,
        port from cameras.onvif_port. Raises PTZNotConfigured with the exact
        thing that is missing, which the dashboard shows as-is."""
        if not onvif_port:
            raise PTZNotConfigured("set this camera's ONVIF port on the Cameras page "
                                   "(80 on most Hikvision/Dahua, 2020 on Tapo)")
        u = urlparse((url or "").strip())
        if not u.hostname:
            raise PTZNotConfigured("this camera's stream URL has no host to control")
        if not u.username:
            raise PTZNotConfigured("this camera's stream URL has no login -- "
                                   "use rtsp://user:password@host/... so PTZ can sign in")
        return cls(host=u.hostname, port=int(onvif_port),
                   user=unquote(u.username), password=unquote(u.password or ""))

    def key(self) -> tuple:
        return (self.host, self.port, self.user, self.password)


class PTZController:
    """Thin, lazily-connected wrapper around one ONVIF camera.

    Connection is established on first use rather than at import, so the
    backend still starts normally when the camera is unplugged, on another
    network, or simply not configured yet -- a dashboard that refuses to
    boot because a camera is offline would be worse than one that reports
    the camera as offline.
    """

    def __init__(self, config: Optional[PTZConfig] = None):
        self._config = config or PTZConfig.from_env()
        self._lock = threading.Lock()
        self._cam = None
        self._ptz = None
        self._profile_token = None
        self._caps: Optional[dict] = None
        self._last_error: Optional[str] = None

    # ── connection ────────────────────────────────────────────────────
    @property
    def configured(self) -> bool:
        return self._config is not None and _ONVIF_AVAILABLE

    def _connect(self):
        """Idempotent. Holds the lock so two dashboard clicks arriving at
        once cannot build two ONVIF sessions against the same device."""
        if self._cam is not None:
            return
        if self._config is None:
            raise PTZNotConfigured("no PTZ camera selected")
        if not _ONVIF_AVAILABLE:
            raise PTZNotConfigured("onvif-zeep is not installed (pip install onvif-zeep)")

        # A short timeout: these calls run on a request thread, and an
        # unreachable camera must not hold it for zeep's default minutes.
        # adjust_time: many cameras reject the signed login when their clock
        # and ours differ by more than a few seconds -- common on CCTV that
        # has never been given an NTP server.
        try:
            from zeep.transports import Transport  # type: ignore
            transport = Transport(timeout=5, operation_timeout=5)
        except Exception:
            transport = None
        cam = ONVIFCamera(self._config.host, self._config.port,
                          self._config.user, self._config.password,
                          adjust_time=True, transport=transport)
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        if not profiles:
            raise PTZUnsupported("camera exposes no media profiles")
        self._profile_token = profiles[0].token
        self._ptz = cam.create_ptz_service()
        self._cam = cam

    def _ensure(self):
        with self._lock:
            self._connect()

    # ── capability discovery ──────────────────────────────────────────
    def get_capabilities(self) -> dict:
        """What this specific camera can actually do. Queried, not assumed.

        Never raises: the dashboard calls this on page load and a camera
        being unreachable is an expected state, not an error condition.
        """
        if not self.configured:
            return {
                "configured": False,
                "reason": ("no PTZ camera selected"
                           if _ONVIF_AVAILABLE else "onvif-zeep not installed"),
                "pan_tilt": False, "zoom": False, "presets": False,
                "two_way_audio": False,
            }
        try:
            self._ensure()
            cfgs = self._ptz.GetConfigurations()
            if not cfgs:
                raise PTZUnsupported("no PTZ configurations on device")
            cfg = cfgs[0]
            opts = self._ptz.GetConfigurationOptions({"ConfigurationToken": cfg.token})
            spaces = opts.Spaces
            pan_tilt = bool(getattr(spaces, "ContinuousPanTiltVelocitySpace", None))
            zoom = bool(getattr(spaces, "ContinuousZoomVelocitySpace", None))
            try:
                presets = self._ptz.GetPresets({"ProfileToken": self._profile_token})
                presets_ok = presets is not None
            except Exception:
                presets_ok = False
            self._caps = {
                "configured": True,
                "host": self._config.host,
                "pan_tilt": pan_tilt,
                "zoom": zoom,
                "presets": presets_ok,
                # Not an ONVIF PTZ capability; see module docstring.
                "two_way_audio": False,
                "two_way_audio_note": (
                    "Not exposed over ONVIF; Tapo uses an undocumented "
                    "proprietary channel. Not implemented rather than faked."
                ),
            }
            self._last_error = None
            return self._caps
        except Exception as e:
            self._last_error = str(e)
            return {
                "configured": True, "reachable": False, "error": str(e),
                "pan_tilt": False, "zoom": False, "presets": False,
                "two_way_audio": False,
            }

    # ── movement ──────────────────────────────────────────────────────
    def move(self, pan: float = 0.0, tilt: float = 0.0, zoom: float = 0.0,
             duration: Optional[float] = None) -> dict:
        """Continuous move at the given normalised velocities (-1.0..1.0).

        Continuous rather than absolute because that is what a held-down
        arrow button on a dashboard means. `duration` issues an automatic
        Stop after N seconds so a dropped websocket or a closed browser tab
        cannot leave the camera panning forever -- the failure mode that
        makes a PTZ camera useless until someone power-cycles it.
        """
        self._ensure()
        pan, tilt, zoom = (max(-1.0, min(1.0, v)) for v in (pan, tilt, zoom))
        req = self._ptz.create_type("ContinuousMove")
        req.ProfileToken = self._profile_token
        req.Velocity = {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}}
        self._ptz.ContinuousMove(req)
        if duration:
            threading.Timer(duration, self._safe_stop).start()
        return {"status": "moving", "pan": pan, "tilt": tilt, "zoom": zoom}

    def stop(self) -> dict:
        self._ensure()
        self._ptz.Stop({"ProfileToken": self._profile_token,
                        "PanTilt": True, "Zoom": True})
        return {"status": "stopped"}

    def _safe_stop(self):
        try:
            self.stop()
        except Exception:
            pass  # timer thread: a failed auto-stop must not raise into nothing

    # ── presets ───────────────────────────────────────────────────────
    def list_presets(self) -> list:
        self._ensure()
        presets = self._ptz.GetPresets({"ProfileToken": self._profile_token}) or []
        return [{"token": p.token, "name": getattr(p, "Name", None) or p.token}
                for p in presets]

    def goto_preset(self, token: str) -> dict:
        self._ensure()
        self._ptz.GotoPreset({"ProfileToken": self._profile_token,
                              "PresetToken": token})
        return {"status": "moving_to_preset", "token": token}

    def save_preset(self, name: str) -> dict:
        self._ensure()
        res = self._ptz.SetPreset({"ProfileToken": self._profile_token,
                                   "PresetName": name})
        return {"status": "saved", "name": name,
                "token": res if isinstance(res, str) else getattr(res, "PresetToken", None)}


# One controller (one ONVIF session) per camera, reused across requests.
# Keyed by host/port/login, so changing a camera's URL or port gets a fresh
# session instead of reusing one signed in with the old details.
_controllers: dict = {}
_controller_lock = threading.Lock()


def get_controller(config: Optional[PTZConfig] = None) -> PTZController:
    """config=None: the environment-configured camera (the old behaviour)."""
    config = config or PTZConfig.from_env()
    key = config.key() if config else None
    with _controller_lock:
        if key not in _controllers:
            _controllers[key] = PTZController(config)
        return _controllers[key]
