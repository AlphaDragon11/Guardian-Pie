import cv2
import mediapipe as mp
import numpy as np
import requests
import time
import threading
import datetime
import sys
import os
import csv
import math
import pyttsx3
import serial

# ============================================================
#  FIX: UTF-8 so emojis don't crash Windows terminal
# ============================================================
sys.stdout.reconfigure(encoding='utf-8')

# ============================================================
#   SHARED PATH — both main.py and dashboard.py read this
#   Set it to the folder that contains both scripts.
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
#         CONFIG — CENTRAL COMMAND
# ============================================================
class Config:
    # ── Arduino ───────────────────────────────────────────────
    ARDUINO_PORT = "COM8"
    ARDUINO_BAUD = 9600

    # ── Telegram ──────────────────────────────────────────────
    TELEGRAM_TOKEN = '7673204531:AAFhHmjJG47YPgNbbAXbQy6FC0K_L_7MmrM'
    CHAT_ID        = '6907906011'

    # ── Files  (always written next to this script) ───────────
    VIDEO_FILENAME = os.path.join(BASE_DIR, "fall_alert.mp4")   # H.264 MP4 — browser playable
    LOG_FILENAME   = os.path.join(BASE_DIR, "incident_log.csv")

    # ── Recording ─────────────────────────────────────────────
    RECORD_SECONDS = 20
    VIDEO_FPS      = 20.0

    # ── Feature flags ─────────────────────────────────────────
    ENABLE_VOICE        = True
    ENABLE_PRIVACY      = True
    ENABLE_NIGHT_VISION = True
    ENABLE_SKELETON     = True     # draw pose skeleton on screen
    ENABLE_HUD          = True     # rich HUD overlay
    ENABLE_HEATMAP      = False    # body-part motion heatmap (CPU heavy)

    # ── Night-vision threshold ────────────────────────────────
    BRIGHTNESS_LIMIT = 60

    # ── Tripwire (fraction of frame width from left) ──────────
    TRIPWIRE_X_LIMIT = 0.25

    # ── Vital-sign motion sensitivity ─────────────────────────
    VITAL_MOTION_SENSITIVITY = 5000

    # ── Fall-detection AI ─────────────────────────────────────
    CONFIDENCE            = 0.65   # raised for fewer false positives
    FALL_FRAMES_TO_CONFIRM = 10    # must be "fallen" this many frames
    FALL_COOLDOWN_SEC     = 5      # ignore new falls for N sec after reset
    ANGLE_FALL_THRESHOLD  = 45     # degrees from vertical
    NOSE_FLOOR_LIMIT      = 0.62   # normalised y (lower = closer to floor)
    ASPECT_RATIO_THRESH   = 1.15   # width/height ratio indicating horizontal body
    VELOCITY_THRESHOLD    = 0.018  # min downward nose velocity to count as fall

    # ── BGR colours ───────────────────────────────────────────
    GREEN  = (0, 210, 90)
    RED    = (0, 40, 220)
    YELLOW = (0, 220, 220)
    BLUE   = (220, 80, 0)
    CYAN   = (220, 200, 0)
    WHITE  = (255, 255, 255)
    BLACK  = (0, 0, 0)
    ORANGE = (0, 140, 255)


# ============================================================
#    VOICE ASSISTANT
# ============================================================
class VoiceAssistant:
    def __init__(self):
        self._lock = threading.Lock()

    def speak(self, text):
        if not Config.ENABLE_VOICE:
            return
        def _talk():
            with self._lock:
                try:
                    engine = pyttsx3.init()
                    engine.setProperty('rate', 155)
                    engine.say(text)
                    engine.runAndWait()
                    engine.stop()
                except Exception:
                    pass
        threading.Thread(target=_talk, daemon=True).start()


# ============================================================
#    HARDWARE MANAGER
# ============================================================
class HardwareManager:
    def __init__(self):
        self.cap     = None
        self.arduino = None
        self._connect_arduino()
        self._connect_camera()

    # ── Arduino ───────────────────────────────────────────────
    def _connect_arduino(self):
        print("[HW] Connecting to Arduino …")
        try:
            self.arduino = serial.Serial(Config.ARDUINO_PORT, Config.ARDUINO_BAUD, timeout=0.1)
            time.sleep(2)
            print(f"[HW] ✅ Arduino on {Config.ARDUINO_PORT}")
        except Exception as exc:
            print(f"[HW] ❌ Arduino not found on {Config.ARDUINO_PORT}: {exc}")
            print("[HW]    Continuing without Arduino (buzzer disabled).")
            self.arduino = None   # graceful degradation instead of hard exit

    # ── Camera ────────────────────────────────────────────────
    def _connect_camera(self):
        print("[HW] Searching for camera …")
        # Try index 1 first (external USB), then 0 (built-in)
        for idx in [1, 0]:
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if cap and cap.isOpened():
                # Force good resolution
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cap.set(cv2.CAP_PROP_FPS, 30)
                cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
                self.cap = cap
                print(f"[HW] ✅ Camera on index {idx} ({int(cap.get(3))}x{int(cap.get(4))})")
                return
        print("[HW] ❌ No camera found. Check USB connection.")
        sys.exit(1)

    def get_frame(self):
        return self.cap.read() if self.cap else (False, None)

    def trigger_buzzer(self, state: bool):
        if self.arduino:
            cmd = b"ALARM_ON\n" if state else b"ALARM_OFF\n"
            try:
                self.arduino.write(cmd)
            except Exception:
                pass

    def release(self):
        if self.cap:
            self.cap.release()
        if self.arduino:
            self.arduino.close()


# ============================================================
#    BLACK-BOX LOGGER + CLOUD
# ============================================================
class BlackBoxSystem:
    def __init__(self):
        if not os.path.exists(Config.LOG_FILENAME):
            with open(Config.LOG_FILENAME, 'w', newline='') as f:
                csv.writer(f).writerow(["Timestamp", "Event", "Details", "File"])

    def log(self, event: str, details: str):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(Config.LOG_FILENAME, 'a', newline='') as f:
                csv.writer(f).writerow([ts, event, details, Config.VIDEO_FILENAME])
            print(f"[LOG]  {event}: {details}")
        except Exception:
            pass

    def send_telegram_video(self, video_path: str, caption: str):
        def _thread():
            try:
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(video_path, 'rb') as f:
                    data = {
                        'chat_id': Config.CHAT_ID,
                        'caption': f"🚨 {caption}\n🕒 {ts}"
                    }
                    url = f'https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendVideo'
                    r = requests.post(url, files={'video': f}, data=data, timeout=60)
                    if r.status_code == 200:
                        print("[CLOUD] ✅ Video sent via Telegram.")
                    else:
                        print(f"[CLOUD] ⚠️  Telegram returned {r.status_code}: {r.text[:120]}")
            except Exception as exc:
                print(f"[CLOUD] ❌ {exc}")
        threading.Thread(target=_thread, daemon=True).start()


# ============================================================
#    VIDEO WRITER HELPER  (the core fix)
# ============================================================
def make_video_writer(path: str, fps: float, frame_w: int, frame_h: int) -> cv2.VideoWriter:
    """
    Step 1: Write raw frames to a temp .avi (XVID — always works with OpenCV on Windows).
    Step 2: After recording finishes, convert_to_h264() re-encodes to browser-playable H.264 MP4.
    The dashboard always reads the .mp4 file.
    """
    avi_path = path.replace(".mp4", "_raw.avi")
    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    vw = cv2.VideoWriter(avi_path, fourcc, fps, (frame_w, frame_h))
    if not vw.isOpened():
        fourcc = cv2.VideoWriter_fourcc(*'MJPG')
        vw = cv2.VideoWriter(avi_path, fourcc, fps, (frame_w, frame_h))
    return vw


def convert_to_h264(mp4_path: str):
    """
    Re-encode _raw.avi → H.264 .mp4 so browsers can play it inline.
    Runs in background thread so it does not block the main loop.
    Requires ffmpeg installed (comes with most systems; on Windows install via
    https://ffmpeg.org/download.html and add to PATH).
    """
    import subprocess
    avi_path = mp4_path.replace(".mp4", "_raw.avi")

    def _run():
        try:
            cmd = [
                "ffmpeg", "-y",
                "-i", avi_path,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-an",
                mp4_path,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode == 0:
                os.remove(avi_path)          # clean up temp file
                print(f"[VIDEO] H.264 MP4 ready → {mp4_path}")
            else:
                # ffmpeg failed — rename avi so dashboard still finds something
                os.replace(avi_path, mp4_path.replace(".mp4", ".avi"))
                print(f"[VIDEO] ffmpeg failed, keeping raw AVI. Error: {result.stderr[:200]}")
        except FileNotFoundError:
            # ffmpeg not installed — just rename avi, dashboard will still find it
            os.replace(avi_path, mp4_path.replace(".mp4", ".avi"))
            print("[VIDEO] ffmpeg not found. Saved as .avi — install ffmpeg for browser playback.")
        except Exception as exc:
            print(f"[VIDEO] Conversion error: {exc}")

    threading.Thread(target=_run, daemon=True).start()


# ============================================================
#    HUD RENDERER
# ============================================================
class HUD:
    """Draws all on-screen overlays onto a frame in-place."""

    # semi-transparent panel background
    @staticmethod
    def _panel(frame, x, y, w, h, alpha=0.55, color=(5, 10, 20)):
        overlay = frame.copy()
        cv2.rectangle(overlay, (x, y), (x+w, y+h), color, -1)
        cv2.addWeighted(overlay, alpha, frame, 1-alpha, 0, frame)

    @staticmethod
    def _text(frame, txt, x, y, color=Config.GREEN, scale=0.55, thick=1):
        # black shadow for readability on any background
        cv2.putText(frame, txt, (x+1, y+1), cv2.FONT_HERSHEY_SIMPLEX, scale, Config.BLACK, thick+1, cv2.LINE_AA)
        cv2.putText(frame, txt, (x,   y  ), cv2.FONT_HERSHEY_SIMPLEX, scale, color,        thick,   cv2.LINE_AA)

    @classmethod
    def draw_status_bar(cls, frame, status_text, is_recording, vital_status,
                        fall_score, system_awake, fps_val, night_mode):
        fh, fw = frame.shape[:2]

        # ── Top bar ───────────────────────────────────────────
        cls._panel(frame, 0, 0, fw, 34)
        title = "GUARDIANPI  |  ELDERCARE SENTINEL  v3.0"
        cls._text(frame, title, 10, 22, Config.GREEN, 0.6, 1)
        ts = datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        cls._text(frame, ts, fw-220, 22, Config.CYAN, 0.55)

        # ── Status badge ──────────────────────────────────────
        badge_color = Config.RED if "FALL" in status_text else \
                      Config.ORANGE if "TRIPWIRE" in status_text else Config.GREEN
        cls._panel(frame, 10, 44, 320, 30, color=(10,10,10))
        cv2.rectangle(frame, (10, 44), (330, 74), badge_color, 1)
        cls._text(frame, f"  {status_text}", 14, 64, badge_color, 0.6, 2 if "FALL" in status_text else 1)

        # ── Fall confidence bar ───────────────────────────────
        bar_x, bar_y, bar_w = 10, 84, 200
        cls._panel(frame, bar_x-2, bar_y-2, bar_w+4+60, 20)
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x+bar_w, bar_y+14), (30,30,30), -1)
        filled = int(bar_w * min(fall_score, 1.0))
        bar_col = Config.RED if fall_score > 0.7 else Config.YELLOW if fall_score > 0.4 else Config.GREEN
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x+filled, bar_y+14), bar_col, -1)
        cls._text(frame, f"FALL RISK: {int(fall_score*100)}%", bar_x+bar_w+8, bar_y+12, bar_col, 0.45)

        # ── Right panel: system info ───────────────────────────
        rx = fw - 195
        cls._panel(frame, rx, 44, 185, 120)
        lines = [
            (f"FPS: {fps_val:.1f}",       Config.CYAN),
            (f"NIGHT VIS: {'ON' if night_mode else 'OFF'}", Config.GREEN if night_mode else Config.YELLOW),
            (f"PRIVACY: {'ON' if Config.ENABLE_PRIVACY else 'OFF'}", Config.YELLOW),
            (f"SKELETON: {'ON' if Config.ENABLE_SKELETON else 'OFF'}", Config.GREEN),
            (f"AWAKE: {'YES' if system_awake else 'STANDBY'}", Config.GREEN if system_awake else (80,80,80)),
        ]
        for i, (txt, col) in enumerate(lines):
            cls._text(frame, txt, rx+8, 62 + i*20, col, 0.48)

        # ── Recording indicator ───────────────────────────────
        if is_recording:
            pulse = int(time.time() * 3) % 2
            if pulse:
                cv2.circle(frame, (fw-30, 20), 10, Config.RED, -1)
            cls._text(frame, "REC", fw-62, 25, Config.RED, 0.55, 2)
            if vital_status:
                cls._panel(frame, 0, fh-36, fw, 36, color=(20,0,0))
                cls._text(frame, f"VITALS: {vital_status}", 10, fh-14,
                          Config.RED if "CRITICAL" in vital_status else Config.YELLOW, 0.55, 1)

    @classmethod
    def draw_tripwire(cls, frame, x_frac):
        fh, fw = frame.shape[:2]
        tx = int(fw * x_frac)
        cv2.line(frame, (tx, 0), (tx, fh), Config.RED, 1, cv2.LINE_AA)
        cls._text(frame, "< DANGER ZONE", 4, fh-40, Config.RED, 0.45)

    @classmethod
    def draw_person_box(cls, frame, lm, mp_pose):
        """Draw a tight bounding box around the detected person."""
        fh, fw = frame.shape[:2]
        xs = [l.x * fw for l in lm]
        ys = [l.y * fh for l in lm]
        x1, y1 = int(min(xs))-10, int(min(ys))-10
        x2, y2 = int(max(xs))+10, int(max(ys))+10
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(fw, x2), min(fh, y2)
        cv2.rectangle(frame, (x1, y1), (x2, y2), Config.CYAN, 1, cv2.LINE_AA)
        cls._text(frame, "PERSON DETECTED", x1, max(12, y1-6), Config.CYAN, 0.45)

    @classmethod
    def draw_fall_alert_overlay(cls, frame):
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (frame.shape[1], frame.shape[0]), (0, 0, 180), -1)
        cv2.addWeighted(overlay, 0.22, frame, 0.78, 0, frame)
        fh, fw = frame.shape[:2]
        cls._text(frame, "!! FALL DETECTED !!", fw//2 - 160, fh//2,
                  Config.WHITE, 1.2, 3)


# ============================================================
#    SAFETY BRAIN  (AI core)
# ============================================================
class SafetyBrain:
    def __init__(self):
        self.mp_pose = mp.solutions.pose
        self.pose = self.mp_pose.Pose(
            static_image_mode=False,
            model_complexity=2,                 # best accuracy (0/1/2)
            smooth_landmarks=True,
            enable_segmentation=False,
            min_detection_confidence=Config.CONFIDENCE,
            min_tracking_confidence=Config.CONFIDENCE,
        )
        self.drawer = mp.solutions.drawing_utils
        self.draw_spec_lm  = self.drawer.DrawingSpec(color=(0,230,80),  thickness=2, circle_radius=3)
        self.draw_spec_con = self.drawer.DrawingSpec(color=(0,180,255), thickness=2)

        # fall state
        self.fall_counter  = 0
        self.last_angle    = 0.0
        self.last_fall_ts  = 0.0       # time of last confirmed fall

        # velocity tracking for nose position
        self._prev_nose_y  = None
        self._nose_vels    = []        # rolling buffer of nose y-velocities

        # motion heatmap
        self.heatmap       = None
        self.prev_gray     = None

        # night-vision state
        self._night_active = False

    # ── Night vision ──────────────────────────────────────────
    def apply_night_vision(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        brightness = float(np.mean(hsv[:, :, 2]))
        self._night_active = brightness < Config.BRIGHTNESS_LIMIT
        if self._night_active:
            # gamma correction  (invGamma < 1 → brighter output)
            gamma    = 0.45
            inv      = 1.0 / gamma
            table    = (np.arange(256) / 255.0) ** inv * 255
            table    = np.clip(table, 0, 255).astype(np.uint8)
            frame    = cv2.LUT(frame, table)
            # green tint so it looks like actual night-vision
            b, g, r  = cv2.split(frame)
            g        = cv2.add(g, 20)
            frame    = cv2.merge([b, g, r])
        return frame

    # ── Privacy mask ──────────────────────────────────────────
    def apply_privacy_mask(self, frame, active_alert):
        if Config.ENABLE_PRIVACY and not active_alert:
            frame = cv2.GaussianBlur(frame, (61, 61), 0)
            HUD._text(frame, "[ PRIVACY MODE ACTIVE ]",
                      frame.shape[1]//2 - 175, frame.shape[0]//2,
                      Config.WHITE, 0.9, 2)
        return frame

    # ── Vital check (motion after fall) ───────────────────────
    def check_vitals(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self.prev_gray is None or self.prev_gray.shape != gray.shape:
            self.prev_gray = gray
            return "ANALYZING..."
        diff         = cv2.absdiff(self.prev_gray, gray)
        motion_score = float(np.sum(diff))
        self.prev_gray = gray
        if motion_score > Config.VITAL_MOTION_SENSITIVITY:
            return "SUBJECT MOVING (CONSCIOUS)"
        return "CRITICAL: NO MOTION DETECTED"

    # ── Gesture: crossed wrists above shoulders → cancel alarm ─
    @staticmethod
    def _detect_cancel_gesture(lm, mp_pose):
        wl = lm[mp_pose.PoseLandmark.LEFT_WRIST]
        wr = lm[mp_pose.PoseLandmark.RIGHT_WRIST]
        sl = lm[mp_pose.PoseLandmark.LEFT_SHOULDER]
        sr = lm[mp_pose.PoseLandmark.RIGHT_SHOULDER]
        wrists_crossed = abs(wl.x - wr.x) < 0.12
        wrists_high    = wl.y < sl.y + 0.15 and wr.y < sr.y + 0.15
        return wrists_crossed and wrists_high

    # ── Core fall detection ───────────────────────────────────
    def _compute_fall_signals(self, lm, mp_pose):
        """
        Returns (is_fallen: bool, confidence: float 0..1)
        Uses 4 independent signals and votes them.
        """
        nose      = lm[mp_pose.PoseLandmark.NOSE]
        ankle_l   = lm[mp_pose.PoseLandmark.LEFT_ANKLE]
        ankle_r   = lm[mp_pose.PoseLandmark.RIGHT_ANKLE]
        shoulder_l = lm[mp_pose.PoseLandmark.LEFT_SHOULDER]
        shoulder_r = lm[mp_pose.PoseLandmark.RIGHT_SHOULDER]
        hip_l     = lm[mp_pose.PoseLandmark.LEFT_HIP]
        hip_r     = lm[mp_pose.PoseLandmark.RIGHT_HIP]

        signals = []

        # 1. Nose close to floor
        sig1 = nose.y > Config.NOSE_FLOOR_LIMIT
        signals.append(sig1)

        # 2. Bounding-box aspect ratio (wide body = horizontal)
        box_h = abs(nose.y - (ankle_l.y + ankle_r.y) / 2)
        box_w = abs(shoulder_l.x - shoulder_r.x) * 2.2
        ratio = box_w / box_h if box_h > 0.01 else 0.0
        sig2  = ratio > Config.ASPECT_RATIO_THRESH
        signals.append(sig2)

        # 3. Torso angle from vertical
        mid_sh_x = (shoulder_l.x + shoulder_r.x) / 2
        mid_sh_y = (shoulder_l.y + shoulder_r.y) / 2
        mid_hi_x = (hip_l.x + hip_r.x) / 2
        mid_hi_y = (hip_l.y + hip_r.y) / 2
        angle    = abs(math.degrees(math.atan2(mid_hi_y - mid_sh_y, mid_hi_x - mid_sh_x))) - 90
        self.last_angle = abs(angle)
        sig3  = self.last_angle > Config.ANGLE_FALL_THRESHOLD
        signals.append(sig3)

        # 4. Downward nose velocity (rapid descent)
        if self._prev_nose_y is not None:
            vel = nose.y - self._prev_nose_y
            self._nose_vels.append(vel)
            if len(self._nose_vels) > 8:
                self._nose_vels.pop(0)
            avg_vel = sum(self._nose_vels) / len(self._nose_vels)
            sig4 = avg_vel > Config.VELOCITY_THRESHOLD
        else:
            sig4 = False
        signals.append(sig4)
        self._prev_nose_y = nose.y

        score = sum(signals) / len(signals)
        # require at least 3/4 signals to fire
        is_fallen = sum(signals) >= 3
        return is_fallen, score

    # ── Main per-frame AI call ────────────────────────────────
    def process_ai(self, frame):
        fh, fw = frame.shape[:2]
        rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.pose.process(rgb)

        status            = "SECURE"
        alert_state       = False
        tripwire_triggered = False
        cancel_gesture    = False
        fall_confidence   = 0.0

        # draw tripwire line
        HUD.draw_tripwire(frame, Config.TRIPWIRE_X_LIMIT)

        if results.pose_landmarks:
            lm = results.pose_landmarks.landmark

            # person bounding box
            HUD.draw_person_box(frame, lm, self.mp_pose)

            # skeleton
            if Config.ENABLE_SKELETON:
                self.drawer.draw_landmarks(
                    frame,
                    results.pose_landmarks,
                    self.mp_pose.POSE_CONNECTIONS,
                    landmark_drawing_spec=self.draw_spec_lm,
                    connection_drawing_spec=self.draw_spec_con,
                )

            # cancel gesture check
            cancel_gesture = self._detect_cancel_gesture(lm, self.mp_pose)
            if cancel_gesture:
                HUD._text(frame, "GESTURE: CANCEL ALARM", fw-290, 52, Config.BLUE, 0.65, 2)

            # tripwire
            ankle_l = lm[self.mp_pose.PoseLandmark.LEFT_ANKLE]
            ankle_r = lm[self.mp_pose.PoseLandmark.RIGHT_ANKLE]
            if ankle_l.x < Config.TRIPWIRE_X_LIMIT or ankle_r.x < Config.TRIPWIRE_X_LIMIT:
                tripwire_triggered = True
                status = "TRIPWIRE BREACH!"

            # fall detection
            is_fallen, fall_confidence = self._compute_fall_signals(lm, self.mp_pose)

            if is_fallen:
                self.fall_counter += 1
            else:
                self.fall_counter = max(0, self.fall_counter - 2)

            if (self.fall_counter >= Config.FALL_FRAMES_TO_CONFIRM and
                    time.time() - self.last_fall_ts > Config.FALL_COOLDOWN_SEC):
                status      = "FALL DETECTED"
                alert_state = True
                HUD.draw_fall_alert_overlay(frame)

        else:
            # no person visible — reset velocity buffer
            self._prev_nose_y = None
            self._nose_vels   = []

        return frame, alert_state, tripwire_triggered, cancel_gesture, status, fall_confidence


# ============================================================
#    FPS COUNTER
# ============================================================
class FPSCounter:
    def __init__(self, window=30):
        self._times  = []
        self._window = window

    def tick(self):
        now = time.perf_counter()
        self._times.append(now)
        if len(self._times) > self._window:
            self._times.pop(0)

    @property
    def fps(self):
        if len(self._times) < 2:
            return 0.0
        return (len(self._times) - 1) / (self._times[-1] - self._times[0])


# ============================================================
#    MAIN
# ============================================================
def main():
    print("\n" + "="*50)
    print("       GUARDIANPI  —  ELDERCARE SENTINEL")
    print("          Fall Detection & Alert System")
    print("="*50)

    # user choices
    blur_choice = input("\nEnable Privacy Blur? (y/n) [default y]: ").strip().lower()
    Config.ENABLE_PRIVACY = (blur_choice != 'n')
    print(f"  Privacy Blur : {'ENABLED' if Config.ENABLE_PRIVACY else 'DISABLED'}")

    skel_choice = input("Show Skeleton Overlay? (y/n) [default y]: ").strip().lower()
    Config.ENABLE_SKELETON = (skel_choice != 'n')
    print(f"  Skeleton     : {'ENABLED' if Config.ENABLE_SKELETON else 'DISABLED'}")

    voice_choice = input("Enable Voice Alerts? (y/n) [default y]: ").strip().lower()
    Config.ENABLE_VOICE = (voice_choice != 'n')
    print(f"  Voice Alerts : {'ENABLED' if Config.ENABLE_VOICE else 'DISABLED'}")
    print()

    # initialise systems
    hw     = HardwareManager()
    logger = BlackBoxSystem()
    brain  = SafetyBrain()
    voice  = VoiceAssistant()
    fps_c  = FPSCounter()

    win = "GuardianPi — ElderCare Sentinel v3.0"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, 1280, 720)

    # state variables
    is_recording        = False
    is_alert_active     = False
    video_writer        = None
    frames_recorded     = 0
    record_target       = 0
    last_tripwire_time  = 0.0
    post_fall_frames    = 0
    vital_status        = ""
    system_awake        = False
    night_mode_on       = False
    current_fall_conf   = 0.0

    voice.speak("Guardian Pi online. Awaiting PIR sensor trigger.")
    print("[SYS] Online. Waiting for Arduino motion signal …\n")
    print("      Press  Q  to quit")
    print("      Press  S  to toggle skeleton")
    print("      Press  P  to toggle privacy blur")
    print("      Press  N  to toggle night vision\n")

    while True:
        ret, frame = hw.get_frame()
        if not ret or frame is None:
            time.sleep(0.01)
            continue

        fps_c.tick()

        # ── Arduino serial read ───────────────────────────────
        if hw.arduino and hw.arduino.in_waiting > 0:
            try:
                line = hw.arduino.readline().decode('utf-8').strip()
                if line == "MOTION_DETECTED" and not system_awake:
                    system_awake = True
                    print("[PIR] Motion → AI active")
                    voice.speak("Motion detected. Vision engaged.")
                elif line == "MOTION_ENDED":
                    if not is_alert_active and not is_recording:
                        system_awake = False
                        print("[PIR] No motion → standby")
            except Exception:
                pass

        # ── Keyboard shortcuts ────────────────────────────────
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            Config.ENABLE_SKELETON = not Config.ENABLE_SKELETON
        elif key == ord('p'):
            Config.ENABLE_PRIVACY = not Config.ENABLE_PRIVACY
        elif key == ord('n'):
            Config.ENABLE_NIGHT_VISION = not Config.ENABLE_NIGHT_VISION

        # ── Standby screen ────────────────────────────────────
        if not system_awake:
            sb = np.zeros_like(frame)
            fh, fw = sb.shape[:2]
            # animated border
            t = int(time.time() * 2) % 2
            border_col = Config.GREEN if t else (0, 120, 50)
            cv2.rectangle(sb, (8, 8), (fw-8, fh-8), border_col, 2)
            # text
            HUD._text(sb, "SYSTEM STANDBY",   fw//2-155, fh//2-20, Config.YELLOW, 1.2, 2)
            HUD._text(sb, "Waiting for PIR sensor motion ...",
                      fw//2-210, fh//2+30, Config.WHITE, 0.7)
            HUD._text(sb, datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S"),
                      fw//2-110, fh-20, Config.CYAN, 0.55)
            cv2.imshow(win, sb)
            continue

        # ── Night vision ──────────────────────────────────────
        if Config.ENABLE_NIGHT_VISION:
            frame = brain.apply_night_vision(frame)
            night_mode_on = brain._night_active

        # ── AI processing ─────────────────────────────────────
        processed, is_fall, tripwire, cancel_cmd, status_txt, fall_conf = brain.process_ai(frame)
        current_fall_conf = fall_conf

        # ── Cancel gesture ────────────────────────────────────
        if cancel_cmd and is_alert_active:
            print("[CMD] Alarm cancelled by gesture.")
            voice.speak("Alarm cancelled.")
            is_alert_active = False
            is_recording    = False
            hw.trigger_buzzer(False)
            brain.fall_counter = 0
            if video_writer:
                video_writer.release()
                video_writer = None
            post_fall_frames = 0

        # ── Tripwire warning ──────────────────────────────────
        if tripwire and not is_alert_active:
            if time.time() - last_tripwire_time > 5:
                print("[WARN] Tripwire breach!")
                voice.speak("Warning! Danger zone entered.")
                logger.log("TRIPWIRE", "Ankle crossed tripwire")
                last_tripwire_time = time.time()

        # ── Fall detected → start recording ───────────────────
        if is_fall and not is_alert_active:
            print("[ALERT] FALL CONFIRMED!")
            is_alert_active     = True
            brain.last_fall_ts  = time.time()
            voice.speak("Fall detected. Initiating emergency protocols.")
            hw.trigger_buzzer(True)
            logger.log("FALL_EVENT", f"Angle:{int(brain.last_angle)} Conf:{int(fall_conf*100)}%")

            is_recording     = True
            frames_recorded  = 0
            post_fall_frames = 0
            vital_status     = ""

            fh, fw = processed.shape[:2]
            record_target = int(Config.RECORD_SECONDS * Config.VIDEO_FPS)
            video_writer  = make_video_writer(Config.VIDEO_FILENAME,
                                              Config.VIDEO_FPS, fw, fh)
            if not video_writer.isOpened():
                print("[VIDEO] ❌ Could not create video writer!")
            else:
                print(f"[VIDEO] Recording → {Config.VIDEO_FILENAME}")

        # ── Active recording ──────────────────────────────────
        if is_recording:
            # check vitals for first 3 seconds (60 frames at 20fps)
            if post_fall_frames < 60:
                vital_status = brain.check_vitals(processed)
                post_fall_frames += 1
            elif post_fall_frames == 60:
                logger.log("VITAL_CHECK", vital_status)
                if "CRITICAL" in vital_status:
                    voice.speak("Subject appears unconscious. Sending alert.")
                post_fall_frames += 1

            # write frame
            if video_writer and video_writer.isOpened():
                video_writer.write(processed)
            frames_recorded += 1

            # clip complete
            if frames_recorded >= record_target:
                print("[VIDEO] Clip saved.")
                is_recording    = False
                is_alert_active = False
                brain.fall_counter = 0
                post_fall_frames   = 0
                if video_writer:
                    video_writer.release()
                    video_writer = None
                convert_to_h264(Config.VIDEO_FILENAME)   # re-encode to H.264 for browser playback
                hw.trigger_buzzer(False)
                logger.send_telegram_video(Config.VIDEO_FILENAME,
                    f"FALL DETECTED | Vitals: {vital_status} | Angle: {int(brain.last_angle)}°")
                voice.speak("Emergency clip sent. System resetting.")

        # ── HUD overlay ───────────────────────────────────────
        HUD.draw_status_bar(
            processed, status_txt, is_recording, vital_status,
            current_fall_conf, system_awake, fps_c.fps, night_mode_on
        )

        # ── Privacy mask (applied AFTER HUD so blur covers skeleton) ─
        display = brain.apply_privacy_mask(processed, is_alert_active)

        cv2.imshow(win, display)

    # ── Cleanup ───────────────────────────────────────────────
    print("\n[SYS] Shutting down …")
    if video_writer:
        video_writer.release()
    hw.trigger_buzzer(False)
    hw.release()
    cv2.destroyAllWindows()
    print("[SYS] Goodbye.")


if __name__ == "__main__":
    main()