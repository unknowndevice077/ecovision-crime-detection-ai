#!/bin/bash
# Local RTSP rig for development.
#
# WHY THIS EXISTS, and why "OBS for dev, RTSP for deployment" is not quite the
# right split: OBS Virtual Camera and an IP camera are not the same kind of
# source, and the difference is not cosmetic.
#
#   OBS Virtual Camera is a PULL source. cv2.read() hands back the current
#   frame; if the detector is slow, OBS simply waits.
#
#   An RTSP camera is a PUSH source. It transmits at its own frame rate whether
#   or not anyone is consuming. If the detector is slower than the camera, the
#   backlog grows until reads stall.
#
# That difference is exactly where both RTSP bugs in this project lived (the
# 30s-vs-5s open timeout, and the reconnect loop caused by an unread backlog).
# Neither is reachable through OBS. Developing only on OBS and meeting RTSP for
# the first time during deployment testing means those bugs surface at the
# worst possible moment, on someone else's hardware, in front of an audience.
#
# So: keep OBS for what it is genuinely better at -- staging a scene, scripting
# a scenario, iterating in seconds -- but run that same content over RTSP so
# the code path under test is the deployment code path. This script makes that
# one command.
#
# Usage:
#   ./dev_rtsp_rig.sh clip <file.mp4>   # loop a recorded clip as RTSP (deterministic)
#   ./dev_rtsp_rig.sh obs               # relay the OBS Virtual Camera as RTSP
#   ./dev_rtsp_rig.sh stop
#
# Then point the detector at it:
#   CAMERA_SOURCE=rtsp://127.0.0.1:8554/cam1 python maincode/main.py
# or type that URL into the dashboard's SRC box.

set -u

MTX_DIR="/d/EcoVisionImagesTraining/rtsp_test_server"
MTX="$MTX_DIR/mediamtx.exe"
URL="rtsp://127.0.0.1:8554/cam1"
MODE="${1:-}"

stop_rig() {
  # Kill the pusher before the server: a publisher left attached to a dead
  # server keeps the path registered and the next run fails with "path
  # already in use", which looks like a port conflict and is not one.
  pkill -f "rtsp://127.0.0.1:8554/cam1" 2>/dev/null
  pkill -f "mediamtx" 2>/dev/null
  echo "rig stopped"
}

case "$MODE" in
  stop)
    stop_rig
    exit 0
    ;;
  clip|obs) ;;
  *)
    echo "usage: $0 {clip <file.mp4>|obs|stop}"
    exit 2
    ;;
esac

if [ ! -x "$MTX" ]; then
  echo "mediamtx not found at $MTX"
  echo "download: https://github.com/bluenviron/mediamtx/releases (windows_amd64 zip)"
  exit 1
fi

stop_rig
sleep 1

echo "starting RTSP server..."
nohup "$MTX" "$MTX_DIR/mediamtx.yml" > "$MTX_DIR/mtx.log" 2>&1 &
sleep 3

if [ "$MODE" = "clip" ]; then
  CLIP="${2:-}"
  if [ -z "$CLIP" ] || [ ! -f "$CLIP" ]; then
    echo "clip mode needs an existing file: $0 clip <file.mp4>"
    stop_rig
    exit 2
  fi
  echo "publishing $CLIP -> $URL (looping)"
  # -re paces the file at its real frame rate. WITHOUT it ffmpeg pushes as fast
  # as it can decode, which is not a camera -- it is a firehose, and it would
  # make the detector look far more overloaded than it actually is.
  # -stream_loop -1 loops forever so a long soak test needs no babysitting.
  # -an drops audio: nothing downstream uses it and it only costs bandwidth.
  nohup ffmpeg -re -stream_loop -1 -i "$CLIP" -an -c:v copy \
        -f rtsp -rtsp_transport tcp "$URL" \
        > /tmp/dev_rtsp_push.log 2>&1 &
else
  echo "publishing OBS Virtual Camera -> $URL"
  # dshow grabs the OBS Virtual Camera as a Windows capture device. Re-encoding
  # is unavoidable here (the virtual camera emits raw frames, not H.264):
  # ultrafast/zerolatency keeps the added delay small enough that the rig does
  # not itself become the thing you are debugging.
  nohup ffmpeg -f dshow -i video="OBS Virtual Camera" \
        -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
        -f rtsp -rtsp_transport tcp "$URL" \
        > /tmp/dev_rtsp_push.log 2>&1 &
fi

sleep 5
if ffprobe -rtsp_transport tcp -v error -show_entries stream=width,height \
           -of default=nw=1 "$URL" 2>/dev/null | grep -q width; then
  echo "OK  $URL is live"
  echo
  echo "point the detector at it:"
  echo "  CAMERA_SOURCE=$URL python maincode/main.py"
else
  echo "FAILED -- stream did not come up. Check:"
  echo "  server: $MTX_DIR/mtx.log"
  echo "  pusher: /tmp/dev_rtsp_push.log"
  [ "$MODE" = "obs" ] && echo "  (is OBS running with Virtual Camera STARTED?)"
  exit 1
fi
