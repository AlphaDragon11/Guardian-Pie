import streamlit as st
import pandas as pd
import numpy as np
import os, time, random, datetime, glob, subprocess, shutil
from datetime import timedelta

import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ─────────────────────────────────────────────────────────────
#  PAGE CONFIG  (must be first)
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="GuardianPi Command",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────
#  PATHS
# ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE_DIR, "incident_log.csv")

def find_video():
    """
    Finds any playable video in BASE_DIR.
    Priority: fall_alert.mp4 > fall_alert.avi > any mp4 > any avi
    Only returns files > 10 KB (avoids corrupt/empty files).
    """
    MIN_SIZE = 10 * 1024  # 10 KB minimum
    priority = [
        os.path.join(BASE_DIR, "fall_alert.mp4"),
        os.path.join(BASE_DIR, "fall_alert.avi"),
    ]
    for p in priority:
        if os.path.exists(p) and os.path.getsize(p) > MIN_SIZE:
            return p
    for ext in ["*.mp4", "*.avi", "*.mkv", "*.mov"]:
        hits = [f for f in glob.glob(os.path.join(BASE_DIR, ext))
                if os.path.getsize(f) > MIN_SIZE]
        if hits:
            return max(hits, key=os.path.getmtime)
    return None

def get_video_mime(path):
    ext = os.path.splitext(path)[1].lower()
    return {".mp4":"video/mp4", ".avi":"video/x-msvideo",
            ".mkv":"video/x-matroska", ".mov":"video/quicktime"}.get(ext, "video/mp4")

# ─────────────────────────────────────────────────────────────
#  CSS
# ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
.stApp{background:#04070c;color:#c8d4e0;font-family:'Courier New',Courier,monospace}
h1,h2,h3,h4{color:#00ff88!important;font-weight:900!important}
div[data-testid="metric-container"]{background:#080f1a;border:1px solid #12243a;
  border-left:3px solid #00ff88;padding:14px 18px;border-radius:4px}
div[data-testid="stMetricValue"]{font-size:1.9rem!important;font-weight:900;color:#fff}
div[data-testid="stMetricLabel"]{font-size:.72rem!important;color:#00ff88;
  text-transform:uppercase;letter-spacing:1.5px}
.stTabs [data-baseweb="tab-list"]{background:#08111e;border-bottom:1px solid #1a2e47;gap:4px}
.stTabs [data-baseweb="tab"]{background:transparent;color:#4a6080;border-radius:4px 4px 0 0;
  padding:8px 18px;font-family:'Courier New',monospace;font-size:12px;letter-spacing:1px}
.stTabs [aria-selected="true"]{background:#08120e!important;color:#00ff88!important;
  border-top:2px solid #00ff88!important}
section[data-testid="stSidebar"]{background:#060d18!important;border-right:1px solid #12243a}
section[data-testid="stSidebar"] *{color:#8fa0b5}
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3{color:#00ff88!important}
.stButton>button{background:#08120e;border:1px solid #00ff88;color:#00ff88;
  font-family:'Courier New',monospace;letter-spacing:1px;font-size:12px;border-radius:3px}
.stButton>button:hover{background:#00ff88;color:#04070c}
hr{border-color:#1a2e47!important}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-track{background:#04070c}
::-webkit-scrollbar-thumb{background:#1a2e47;border-radius:3px}
.secure-box{background:#08120e;border:2px dashed #00ff88;border-radius:8px;
  padding:56px 20px;text-align:center}
.alert-box{background:#1a0007;border:2px solid #ff003c;border-radius:6px;
  padding:16px 20px;color:#ff6080}
.insight-box{background:#14110a;border:1px solid #cc9900;border-radius:6px;
  padding:14px 18px;color:#ffcc55;font-size:.85rem;line-height:1.8}
.insight-box b{color:#ffd700}
.stat-card{background:#080f1a;border:1px solid #12243a;border-radius:6px;padding:16px}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
#  SEED  — runs SYNCHRONOUSLY before any UI renders.
#  Uses a .seed_done marker so it NEVER runs twice.
#  New real falls from main.py always append on top.
# ─────────────────────────────────────────────────────────────
SEED_MARKER = os.path.join(BASE_DIR, ".seed_done")

def seed_one_week():
    """Write 7 days of realistic baseline. Called at import time."""
    if os.path.exists(SEED_MARKER):
        return   # already done — fast exit

    # If real data already exists, just mark as done
    if os.path.exists(CSV_FILE) and os.path.getsize(CSV_FILE) > 500:
        open(SEED_MARKER, "w").write("existing\n")
        return

    now = datetime.datetime.now()

    # Per-hour probability weights (elderly fall patterns)
    hw = {0:2,1:4,2:9,3:10,4:8,5:5,6:3,7:7,8:6,9:3,10:2,11:2,
          12:3,13:7,14:8,15:6,16:3,17:2,18:2,19:3,20:4,21:6,22:5,23:3}
    hk, wt = list(hw.keys()), list(hw.values())

    # Falls per day  Mon Tue Wed Thu Fri Sat Sun  → 25 total
    day_counts = [3, 2, 5, 3, 4, 6, 2]
    rows = []

    for day_off, n in enumerate(day_counts):
        base = now - timedelta(days=7 - day_off)
        for _ in range(n):
            hr = random.choices(hk, weights=wt)[0]
            ts = base.replace(hour=hr, minute=random.randint(0,59),
                              second=random.randint(0,59), microsecond=0)
            angle = int(np.clip(np.random.normal(68, 10), 45, 89))
            conf  = int(np.clip(np.random.normal(84,  8), 65, 99))
            vital = ("CRITICAL: NO MOTION DETECTED"
                     if random.random() < 0.20
                     else "SUBJECT MOVING (CONSCIOUS)")

            if random.random() < 0.30:   # tripwire before fall
                rows.append({
                    "Timestamp": (ts - timedelta(seconds=random.randint(5,30)))
                                 .strftime("%Y-%m-%d %H:%M:%S"),
                    "Event":"TRIPWIRE", "Details":"Ankle crossed danger zone",
                    "Vital":"", "File":"",
                })
            rows.append({
                "Timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "Event":"FALL_EVENT",
                "Details":f"Angle:{angle} Conf:{conf}%",
                "Vital":vital, "File":"fall_alert.mp4",
            })
            rows.append({
                "Timestamp": (ts + timedelta(seconds=3)).strftime("%Y-%m-%d %H:%M:%S"),
                "Event":"VITAL_CHECK", "Details":vital,
                "Vital":vital, "File":"",
            })

    pd.DataFrame(rows).sort_values("Timestamp").to_csv(CSV_FILE, index=False)
    open(SEED_MARKER, "w").write(f"seeded {datetime.datetime.now()}\n")

# ← This runs BEFORE st.title() so data is ready when first tab renders
seed_one_week()

# ─────────────────────────────────────────────────────────────
#  DATA LOADER
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=3)
def load_data():
    if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) < 10:
        return pd.DataFrame()
    try:
        df = pd.read_csv(CSV_FILE)
        if "Timestamp" in df.columns:
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
        return df.dropna(subset=["Timestamp"])
    except Exception:
        return pd.DataFrame()

def get_num(s, key):
    try:
        return int(str(s).split(key)[1].split("%")[0].split()[0])
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────
#  PLOTLY HELPERS
# ─────────────────────────────────────────────────────────────
PL = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
          font=dict(color="#8fa0b5", family="Courier New"),
          margin=dict(l=44, r=20, t=32, b=40))
def ax(fig):
    fig.update_xaxes(gridcolor="#0d1a2d", zerolinecolor="#0d1a2d", color="#4a6080")
    fig.update_yaxes(gridcolor="#0d1a2d", zerolinecolor="#0d1a2d", color="#4a6080")
    return fig

# ─────────────────────────────────────────────────────────────
#  SIDEBAR
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🛡️ GuardianPi OS")
    st.caption("ElderCare Sentinel v3.0")
    st.divider()
    auto_refresh = st.toggle("🔄 Live Auto-Sync (3s)", value=False)
    st.divider()
    st.markdown("### 🎛️ Controls")

    if st.button("➕ Add 5 Demo Falls (appends only)", use_container_width=True):
        hw2 = {0:2,1:4,2:9,3:10,4:8,5:5,6:3,7:7,8:6,9:3,10:2,11:2,
               12:3,13:7,14:8,15:6,16:3,17:2,18:2,19:3,20:4,21:6,22:5,23:3}
        now2 = datetime.datetime.now()
        new_rows = []
        for _ in range(5):
            hr2 = random.choices(list(hw2), weights=list(hw2.values()))[0]
            ts2 = now2.replace(hour=hr2, minute=random.randint(0,59),
                               second=random.randint(0,59), microsecond=0)
            a2 = int(np.clip(np.random.normal(68,10),45,89))
            c2 = int(np.clip(np.random.normal(84, 8),65,99))
            v2 = ("CRITICAL: NO MOTION DETECTED"
                  if random.random()<0.20 else "SUBJECT MOVING (CONSCIOUS)")
            new_rows.append({"Timestamp":ts2.strftime("%Y-%m-%d %H:%M:%S"),
                             "Event":"FALL_EVENT","Details":f"Angle:{a2} Conf:{c2}%",
                             "Vital":v2,"File":"fall_alert.mp4"})
        pd.DataFrame(new_rows).to_csv(CSV_FILE, mode="a",
            header=not os.path.exists(CSV_FILE), index=False)
        st.cache_data.clear()
        st.toast("5 falls appended!", icon="📈"); time.sleep(0.3); st.rerun()

    if st.button("🔄 Re-seed Fresh 1-Week Baseline", use_container_width=True):
        for f in [SEED_MARKER, CSV_FILE]:
            if os.path.exists(f): os.remove(f)
        seed_one_week()
        st.cache_data.clear()
        st.toast("Fresh 1-week baseline ready!", icon="🗓️"); time.sleep(0.3); st.rerun()

    if st.button("🗑️ Full Reset (CSV + Video)", use_container_width=True):
        vp2 = find_video()
        for f in [SEED_MARKER, CSV_FILE] + ([vp2] if vp2 else []):
            if f and os.path.exists(f): os.remove(f)
        seed_one_week()
        st.cache_data.clear(); time.sleep(0.3); st.rerun()

    st.divider()
    st.markdown("### 📂 Paths")
    vp_s = find_video()
    st.code(f"CSV:\n{CSV_FILE}\n\nVIDEO:\n{vp_s or 'not found yet'}", language="text")
    st.divider()
    st.caption("main.py shortcuts:\nS=skeleton  P=privacy  N=night  Q=quit")

# ─────────────────────────────────────────────────────────────
#  LOAD & ENRICH
# ─────────────────────────────────────────────────────────────
df       = load_data()
has_data = not df.empty
video_path = find_video()
has_video  = video_path is not None

if has_data:
    df["Hour"]    = df["Timestamp"].dt.hour
    df["Date"]    = df["Timestamp"].dt.date
    df["DayName"] = df["Timestamp"].dt.day_name()
    df["Angle"]   = df["Details"].apply(lambda x: get_num(x,"Angle:"))
    df["Conf"]    = df["Details"].apply(lambda x: get_num(x,"Conf:"))

    falls_df    = df[df["Event"]=="FALL_EVENT"].copy()
    n_falls     = len(falls_df)
    total_ev    = len(df)
    last_ts     = df["Timestamp"].max()
    last_time   = last_ts.strftime("%H:%M:%S")
    last_date   = last_ts.strftime("%b %d")
    days_span   = max(1,(df["Timestamp"].max()-df["Timestamp"].min()).days+1)
    risk_score  = min(100, int(n_falls*3.5+8))
    avg_angle   = round(falls_df["Angle"].dropna().mean(),1)
    avg_conf    = round(falls_df["Conf"].dropna().mean(),1)
    rate_day    = n_falls/days_span
    prob_24h    = round((1-np.exp(-rate_day))*100,1)
    unconscious = int(df["Vital"].astype(str).str.contains("CRITICAL",na=False).sum()) \
                  if "Vital" in df.columns else 0
else:
    falls_df = pd.DataFrame()
    n_falls=total_ev=risk_score=unconscious=0
    last_time=last_date="N/A"
    days_span=1; avg_angle=avg_conf=prob_24h=rate_day=0.0

# ─────────────────────────────────────────────────────────────
#  HEADER + KPIs
# ─────────────────────────────────────────────────────────────
icon = "🔴" if n_falls>0 else "🟢"
st.markdown(
    f"<h2 style='margin-bottom:0'>{icon} GUARDIANPI — GLOBAL SECURITY NEXUS</h2>",
    unsafe_allow_html=True)
st.caption(
    f"ElderCare Sentinel v3.0  ·  {days_span} days tracked  ·  "
    f"Last refresh {datetime.datetime.now().strftime('%H:%M:%S')}")
st.divider()

c1,c2,c3,c4,c5,c6 = st.columns(6)
c1.metric("TOTAL FALLS",   n_falls,   delta=f"+{n_falls}", delta_color="inverse")
c2.metric("ALL EVENTS",    total_ev)
c3.metric("LAST ALERT",    last_time, delta=last_date)
c4.metric("UNCONSCIOUS",   unconscious,
          delta="CRITICAL" if unconscious else "OK",
          delta_color="inverse" if unconscious else "normal")
c5.metric("RISK INDEX",    f"{risk_score}%",
          delta="HIGH" if risk_score>65 else ("MED" if risk_score>35 else "LOW"),
          delta_color="inverse" if risk_score>65 else "normal")
c6.metric("DAYS TRACKED",  days_span)
st.divider()

# ─────────────────────────────────────────────────────────────
#  TABS
# ─────────────────────────────────────────────────────────────
tab1,tab2,tab3,tab4,tab5 = st.tabs([
    "📹  RECENT CAPTURE",
    "🧠  PREDICTIVE AI",
    "📊  DATA SCIENCE",
    "🗺️  PATTERN INTEL",
    "📋  TELEMETRY DB",
])

# ══════════════════════════════════════════════════════════════
#  TAB 1 — RECENT CAPTURE
# ══════════════════════════════════════════════════════════════
with tab1:
    vc, ic = st.columns([2.4,1], gap="large")

    with vc:
        st.markdown("### 📹 Most Recent Fall Clip")

        if has_video:
            mime = get_video_mime(video_path)
            sz   = os.path.getsize(video_path)/1024
            mtime = datetime.datetime.fromtimestamp(
                os.path.getmtime(video_path)).strftime("%Y-%m-%d %H:%M:%S")

            # ── Determine if re-encoding is needed ──────────────
            # AVI files can't play in browser — offer conversion
            ext = os.path.splitext(video_path)[1].lower()
            if ext == ".avi":
                st.warning(
                    "⚠️ Video is `.avi` format — browsers may not play it. "
                    "Click below to convert to browser-friendly MP4.",
                    icon="🎬"
                )
                if st.button("🔄 Convert to MP4 (H.264) now", use_container_width=True):
                    mp4_out = video_path.replace(".avi","_browser.mp4")
                    with st.spinner("Converting… (takes ~5 seconds)"):
                        try:
                            result = subprocess.run(
                                ["ffmpeg","-y","-i",video_path,
                                 "-c:v","libx264","-preset","fast",
                                 "-crf","23","-pix_fmt","yuv420p",
                                 "-movflags","+faststart","-an", mp4_out],
                                capture_output=True, timeout=120
                            )
                            if result.returncode==0:
                                # rename so find_video picks it up first
                                mp4_final = video_path.replace(".avi",".mp4")
                                shutil.move(mp4_out, mp4_final)
                                os.remove(video_path)
                                st.success("✅ Converted! Reloading…")
                                time.sleep(1); st.rerun()
                            else:
                                st.error(f"ffmpeg failed: {result.stderr[:300]}")
                        except FileNotFoundError:
                            st.error("ffmpeg not found. Install from https://ffmpeg.org/download.html")
                        except Exception as e:
                            st.error(f"Conversion error: {e}")

            # ── Play the video ──────────────────────────────────
            try:
                with open(video_path,"rb") as vf:
                    vbytes = vf.read()
                st.video(vbytes, format=mime)
                st.caption(
                    f"📁 `{os.path.basename(video_path)}`  ·  "
                    f"{sz:.0f} KB  ·  Saved: {mtime}  ·  Format: `{mime}`")
            except Exception as e:
                st.error(f"Playback error: {e}")
                st.info(f"File path: `{video_path}`\nSize: {sz:.0f} KB\nMIME: {mime}")
        else:
            st.markdown("""
            <div class="secure-box">
                <div style="font-size:2.5rem;color:#00ff88;font-weight:900">
                    STATUS: SECURE</div>
                <div style="color:#4a6080;margin-top:10px">
                    No fall clip recorded yet.<br>
                    Run <b>main.py</b> and trigger a fall — clip saves automatically.
                </div>
            </div>""", unsafe_allow_html=True)
            st.info(
                f"📂 Dashboard scans for video in:\n`{BASE_DIR}`\n\n"
                "Expected: `fall_alert.mp4` or `fall_alert.avi`\n\n"
                "✅ Both **main.py** and **dashboard.py** must be in the **same folder**.")

    with ic:
        st.markdown("### 📋 Capture Intel")
        if has_data and n_falls>0:
            lf = falls_df.sort_values("Timestamp").iloc[-1]
            vital_color = "#ff003c" if "CRITICAL" in str(lf.get("Vital","")) else "#00ff88"
            st.markdown(f"""
            <div class="alert-box">
                <b style="color:#ff003c;font-size:1rem">⚠️ LAST FALL ALERT</b><br><br>
                <span style="color:#4a6080">TIME :</span>&nbsp;{lf['Timestamp']}<br>
                <span style="color:#4a6080">ANGLE:</span>&nbsp;{lf['Angle'] or 'N/A'}°<br>
                <span style="color:#4a6080">AI CONF:</span>&nbsp;{lf['Conf'] or 'N/A'}%<br>
                <span style="color:#4a6080">VITAL :</span>&nbsp;
                <span style="color:{vital_color}">{lf.get('Vital','N/A') or 'N/A'}</span><br>
                <span style="color:#4a6080">TELEGRAM:</span>&nbsp;
                <span style="color:#00ff88">SENT ✓</span>
            </div>""", unsafe_allow_html=True)
        else:
            st.success("🟢 No fall alerts yet.")

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("### 📈 1-Week Summary")
        st.markdown(f"""
        <div class="stat-card">
        <table style="width:100%;font-size:.82rem;color:#8fa0b5;line-height:2.2">
        <tr><td>Total Falls</td>
            <td style="color:#ff6080;text-align:right"><b>{n_falls}</b></td></tr>
        <tr><td>Avg Body Angle</td>
            <td style="color:#ffcc00;text-align:right"><b>{avg_angle}°</b></td></tr>
        <tr><td>Avg AI Confidence</td>
            <td style="color:#00ff88;text-align:right"><b>{avg_conf}%</b></td></tr>
        <tr><td>Unconscious Events</td>
            <td style="color:#ff003c;text-align:right"><b>{unconscious}</b></td></tr>
        <tr><td>Falls / Day (avg)</td>
            <td style="color:#00aaff;text-align:right"><b>{rate_day:.1f}</b></td></tr>
        <tr><td>Days Tracked</td>
            <td style="color:#8fa0b5;text-align:right"><b>{days_span}</b></td></tr>
        </table>
        </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
#  TAB 2 — PREDICTIVE AI
# ══════════════════════════════════════════════════════════════
with tab2:
    if not has_data or n_falls<3:
        st.info("Need ≥ 3 fall events.")
    else:
        g_col, i_col = st.columns([1,1.6], gap="large")
        with g_col:
            st.markdown("### 🎯 Live Risk Gauge")
            gc = "#ff003c" if risk_score>70 else ("#ffcc00" if risk_score>40 else "#00ff88")
            fig_g = go.Figure(go.Indicator(
                mode="gauge+number+delta", value=risk_score,
                delta={"reference":50,"valueformat":".0f",
                       "increasing":{"color":"#ff003c"},"decreasing":{"color":"#00ff88"}},
                title={"text":"RISK SCORE","font":{"size":13,"color":"#8fa0b5"}},
                gauge={"axis":{"range":[0,100],"tickwidth":1,
                               "tickcolor":"#1a2e47","tickfont":{"color":"#4a6080"}},
                       "bar":{"color":gc,"thickness":0.25},
                       "bgcolor":"#06101a","bordercolor":"#1a2e47",
                       "steps":[{"range":[0,40],"color":"#021a0a"},
                                 {"range":[40,70],"color":"#1a1200"},
                                 {"range":[70,100],"color":"#1a0005"}],
                       "threshold":{"line":{"color":"#ff003c","width":3},
                                    "thickness":0.8,"value":75}},
            ))
            fig_g.update_layout(**PL, height=270)
            st.plotly_chart(fig_g, use_container_width=True)
            rl = "🔴 HIGH" if risk_score>70 else ("🟡 MEDIUM" if risk_score>40 else "🟢 LOW")
            st.markdown(f"<div style='text-align:center;padding:10px;background:#080f1a;"
                        f"border:1px solid #1a2e47;border-radius:4px;"
                        f"font-size:1.1rem;font-weight:900;letter-spacing:2px'>{rl} RISK</div>",
                        unsafe_allow_html=True)

        with i_col:
            st.markdown("### 💡 AI Predictive Insight")
            hc_all = falls_df["Hour"].value_counts()
            peak_h = int(hc_all.idxmax()) if not hc_all.empty else 3
            night_n = len(falls_df[falls_df["Hour"].between(0,5)])
            night_pct = int(night_n/max(1,n_falls)*100)
            st.markdown(f"""
            <div class="insight-box">
                <b>📈 {days_span}-Day Pattern Analysis</b><br><br>
                • <b>{night_pct}%</b> of falls in <b>00:00–05:00 AM</b> window<br>
                • Peak risk hour: <b>{peak_h:02d}:00</b>
                  ({hc_all.max() if not hc_all.empty else 0} incidents)<br>
                • Fall probability next 24h:
                  <b style="color:#ff6080">{prob_24h}%</b>
                  (rate={rate_day:.2f}/day)<br>
                • Avg severity angle: <b>{avg_angle}°</b>
                  ({'Severe ⚠️' if avg_angle>70 else 'Moderate'})<br>
                • AI confidence avg: <b>{avg_conf}%</b><br>
                • Unconscious events: <b style="color:#ff003c">{unconscious}</b>
                  ({int(unconscious/max(1,n_falls)*100)}%)<br><br>
                <hr style="border-color:#cc990044;margin:6px 0">
                <b>🔧 RECOMMENDATIONS</b><br>
                • Caregiver check-in daily at <b>{peak_h:02d}:00</b><br>
                • Motion-activated lighting <b>01:00–05:00 AM</b><br>
                • Consider bed/chair pressure sensor<br>
                • Review medication side-effects for dizziness
            </div>""", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(f"**Fall probability next 24h: {prob_24h}%**")
            st.progress(min(int(prob_24h),100))

        st.divider()
        st.markdown("### ⏰ Fall Frequency by Hour")
        all_h = pd.DataFrame({"Hour":range(24)})
        hc2 = falls_df["Hour"].value_counts().reset_index()
        hc2.columns = ["Hour","Falls"]
        hc2 = all_h.merge(hc2, on="Hour", how="left").fillna(0)
        hc2["Zone"] = hc2["Hour"].apply(
            lambda h: "Night 00-05 (Very High)"   if 0<=h<=5 else
                     ("Morning 06-08 (High)"       if 6<=h<=8 else
                     ("Afternoon 13-15 (Moderate)" if 13<=h<=15 else
                     ("Evening 20-22 (Moderate)"   if 20<=h<=22 else "Daytime (Low)"))))
        cmap = {"Night 00-05 (Very High)":"#ff003c","Morning 06-08 (High)":"#ff6600",
                "Afternoon 13-15 (Moderate)":"#ffcc00","Evening 20-22 (Moderate)":"#ffaa00",
                "Daytime (Low)":"#00cc66"}
        fig_h = px.bar(hc2,x="Hour",y="Falls",color="Zone",
                       color_discrete_map=cmap,template="plotly_dark")
        fig_h.update_layout(**PL,height=290,
            xaxis=dict(tickmode="linear",tick0=0,dtick=1),
            legend=dict(bgcolor="rgba(0,0,0,0)",font=dict(color="#8fa0b5")))
        fig_h.update_traces(marker_line_width=0)
        ax(fig_h); st.plotly_chart(fig_h, use_container_width=True)

# ══════════════════════════════════════════════════════════════
#  TAB 3 — DATA SCIENCE
# ══════════════════════════════════════════════════════════════
with tab3:
    if not has_data or n_falls<3:
        st.info("Need ≥ 3 fall events.")
    else:
        r1a,r1b = st.columns(2,gap="medium")
        with r1a:
            st.markdown("### 📈 Daily Falls + Rolling Average")
            daily = falls_df.groupby("Date").size().reset_index(name="Falls")
            daily["Date"] = pd.to_datetime(daily["Date"])
            full = pd.date_range(daily["Date"].min(),daily["Date"].max())
            daily = (daily.set_index("Date").reindex(full,fill_value=0)
                     .reset_index().rename(columns={"index":"Date"}))
            daily["MA3"] = daily["Falls"].rolling(3,min_periods=1).mean()
            fig_d = go.Figure()
            fig_d.add_trace(go.Bar(x=daily["Date"],y=daily["Falls"],
                name="Falls",marker_color="rgba(255,0,60,0.5)",marker_line_width=0))
            fig_d.add_trace(go.Scatter(x=daily["Date"],y=daily["MA3"],
                mode="lines",name="3-day avg",
                line=dict(color="#ffcc00",width=2,dash="dot")))
            fig_d.update_layout(**PL,height=270,
                legend=dict(bgcolor="rgba(0,0,0,0)",font=dict(color="#8fa0b5")))
            ax(fig_d); st.plotly_chart(fig_d,use_container_width=True)

        with r1b:
            st.markdown("### 📐 Fall Angle Distribution")
            angles = falls_df["Angle"].dropna()
            if len(angles)>0:
                fig_hist = go.Figure()
                fig_hist.add_trace(go.Histogram(x=angles,nbinsx=15,
                    marker_color="#ff6040",marker_line_color="#ff2000",
                    marker_line_width=0.5,opacity=0.85))
                fig_hist.add_vline(x=float(angles.mean()),line_color="#ffcc00",
                    line_dash="dash",annotation_text=f"Mean {angles.mean():.1f}°",
                    annotation_font_color="#ffcc00")
                fig_hist.add_vline(x=45,line_color="#00ff88",line_dash="dot",
                    annotation_text="Threshold 45°",annotation_font_color="#00ff88")
                fig_hist.update_layout(**PL,height=270,
                    xaxis_title="Body Angle (°)",yaxis_title="Count")
                ax(fig_hist); st.plotly_chart(fig_hist,use_container_width=True)

        r2a,r2b = st.columns(2,gap="medium")
        with r2a:
            st.markdown("### 🗓️ Hour × Day-of-Week Heatmap")
            day_ord = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
            pivot = (falls_df.groupby(["DayName","Hour"]).size()
                     .unstack(fill_value=0).reindex(day_ord,fill_value=0))
            for h in range(24):
                if h not in pivot.columns: pivot[h]=0
            pivot = pivot[sorted(pivot.columns)]
            fig_hm = go.Figure(go.Heatmap(
                z=pivot.values,x=[f"{h:02d}h" for h in pivot.columns],
                y=list(pivot.index),
                colorscale=[[0,"#04070c"],[0.3,"#1a0005"],[0.6,"#990020"],[1,"#ff003c"]],
                showscale=True,colorbar=dict(tickfont=dict(color="#4a6080"),thickness=12),
                hovertemplate="%{y} %{x}<br>Falls: %{z}<extra></extra>"))
            fig_hm.update_layout(**PL,height=270)
            ax(fig_hm); st.plotly_chart(fig_hm,use_container_width=True)

        with r2b:
            st.markdown("### 🎯 AI Confidence vs Severity")
            sc_df = falls_df.dropna(subset=["Angle","Conf"])
            if not sc_df.empty:
                fig_sc = px.scatter(sc_df,x="Angle",y="Conf",color="Hour",
                    color_continuous_scale="RdYlGn_r",template="plotly_dark",
                    labels={"Angle":"Body Angle (°)","Conf":"AI Confidence (%)"},
                    hover_data=["Timestamp"] if "Timestamp" in sc_df.columns else [])
                fig_sc.update_traces(marker=dict(size=10,line=dict(width=0.5,color="#000")))
                fig_sc.update_layout(**PL,height=270,
                    coloraxis_colorbar=dict(tickfont=dict(color="#4a6080"),thickness=12))
                ax(fig_sc); st.plotly_chart(fig_sc,use_container_width=True)

        st.markdown("### 📊 Cumulative Falls + 7-Day Rolling Rate")
        if len(daily)>1:
            daily["Cumulative"] = daily["Falls"].cumsum()
            daily["MA7"] = daily["Falls"].rolling(7,min_periods=1).mean()
            fig_cum = make_subplots(specs=[[{"secondary_y":True}]])
            fig_cum.add_trace(go.Bar(x=daily["Date"],y=daily["Falls"],name="Daily",
                marker_color="rgba(255,0,60,0.35)",marker_line_width=0),secondary_y=False)
            fig_cum.add_trace(go.Scatter(x=daily["Date"],y=daily["Cumulative"],
                name="Cumulative",line=dict(color="#00ff88",width=2.5),mode="lines"),
                secondary_y=True)
            fig_cum.add_trace(go.Scatter(x=daily["Date"],y=daily["MA7"],name="7-day avg",
                line=dict(color="#ffcc00",width=1.5,dash="dot")),secondary_y=False)
            fig_cum.update_layout(**PL,height=290,
                legend=dict(bgcolor="rgba(0,0,0,0)",font=dict(color="#8fa0b5")))
            fig_cum.update_yaxes(gridcolor="#0d1a2d",zerolinecolor="#0d1a2d",color="#4a6080")
            fig_cum.update_xaxes(gridcolor="#0d1a2d",color="#4a6080")
            st.plotly_chart(fig_cum,use_container_width=True)

        if "Vital" in df.columns:
            st.markdown("### 🫀 Post-Fall Vital Outcomes")
            vc1,vc2 = st.columns([1,2],gap="medium")
            vc_data = df[df["Vital"].notna()&(df["Vital"]!="")]["Vital"].value_counts()
            if not vc_data.empty:
                con_n = vc_data.get("SUBJECT MOVING (CONSCIOUS)",0)
                unc_n = vc_data.get("CRITICAL: NO MOTION DETECTED",0)
                tot_v = con_n+unc_n
                with vc1:
                    fig_pie = go.Figure(go.Pie(
                        labels=["Conscious","Unconscious"],values=[con_n,unc_n],
                        hole=0.55,marker_colors=["#00cc66","#ff003c"],
                        textfont=dict(color="#fff")))
                    fig_pie.update_layout(**PL,height=260,
                        legend=dict(bgcolor="rgba(0,0,0,0)",font=dict(color="#8fa0b5")))
                    st.plotly_chart(fig_pie,use_container_width=True)
                with vc2:
                    st.markdown(f"""
                    <div class="stat-card" style="margin-top:24px">
                    <table style="width:100%;font-size:.9rem;color:#8fa0b5;line-height:2.4">
                    <tr><td>✅ Conscious</td>
                        <td style="color:#00cc66;text-align:right">
                        <b>{con_n} ({int(con_n/max(1,tot_v)*100)}%)</b></td></tr>
                    <tr><td>🚨 Unconscious</td>
                        <td style="color:#ff003c;text-align:right">
                        <b>{unc_n} ({int(unc_n/max(1,tot_v)*100)}%)</b></td></tr>
                    <tr><td>Total checks</td>
                        <td style="text-align:right"><b>{tot_v}</b></td></tr>
                    </table>
                    </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
#  TAB 4 — PATTERN INTEL
# ══════════════════════════════════════════════════════════════
with tab4:
    if not has_data or n_falls<3:
        st.info("Need ≥ 3 fall events.")
    else:
        p1,p2 = st.columns(2,gap="medium")
        with p1:
            st.markdown("### 🧭 24-Hour Fall Rose Chart")
            hcp = falls_df["Hour"].value_counts().reset_index()
            hcp.columns = ["Hour","Falls"]
            hcp = hcp.sort_values("Hour")
            fig_pol = go.Figure(go.Barpolar(
                r=hcp["Falls"],theta=hcp["Hour"]*15,width=[15]*len(hcp),
                marker_color=hcp["Hour"].apply(
                    lambda h: "#ff003c" if 0<=h<=5 else
                              ("#ff6600" if 6<=h<=8 else
                              ("#ffcc00" if 13<=h<=15 or 20<=h<=22 else "#00cc66"))),
                marker_line_color="#04070c",marker_line_width=0.5,opacity=0.9,
                hovertemplate="<b>%{customdata}</b><br>Falls: %{r}<extra></extra>",
                customdata=hcp["Hour"].apply(lambda h: f"{h:02d}:00")))
            fig_pol.update_layout(**PL,height=360,
                polar=dict(bgcolor="#06101a",
                    radialaxis=dict(showticklabels=True,tickfont=dict(color="#4a6080"),
                                    gridcolor="#0d1a2d",linecolor="#0d1a2d"),
                    angularaxis=dict(tickmode="array",tickvals=list(range(0,360,30)),
                        ticktext=["00h","02h","04h","06h","08h","10h",
                                  "12h","14h","16h","18h","20h","22h"],
                        direction="clockwise",rotation=90,
                        color="#4a6080",gridcolor="#0d1a2d")))
            st.plotly_chart(fig_pol,use_container_width=True)

        with p2:
            st.markdown("### 📅 Falls by Day of Week")
            dow_ord = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
            dow = (falls_df["DayName"].value_counts()
                   .reindex(dow_ord,fill_value=0).reset_index())
            dow.columns = ["Day","Falls"]
            dow["Short"] = dow["Day"].str[:3]
            mx = dow["Falls"].max()
            dow["Color"] = dow["Falls"].apply(
                lambda v: "#ff003c" if v==mx else ("#ffcc00" if v>=mx*0.6 else "#00cc66"))
            fig_dow = go.Figure(go.Bar(x=dow["Short"],y=dow["Falls"],
                marker_color=dow["Color"],marker_line_width=0,
                text=dow["Falls"],textposition="outside",textfont=dict(color="#8fa0b5")))
            fig_dow.update_layout(**PL,height=360,yaxis_title="Falls")
            ax(fig_dow); st.plotly_chart(fig_dow,use_container_width=True)

        st.markdown("### 📉 Severity Timeline")
        sv = falls_df.dropna(subset=["Angle"]).sort_values("Timestamp")
        if not sv.empty:
            fig_sv = go.Figure()
            fig_sv.add_trace(go.Scatter(
                x=sv["Timestamp"],y=sv["Angle"],mode="lines+markers",
                line=dict(color="#ff6040",width=1.5),
                marker=dict(size=sv["Angle"].apply(lambda a: max(7,int(a/7))),
                    color=sv["Angle"],colorscale="Reds",showscale=True,
                    colorbar=dict(title="Angle",tickfont=dict(color="#4a6080"),thickness=12),
                    line=dict(width=0.5,color="#000")),
                hovertemplate="<b>%{x}</b><br>Angle: %{y}°<extra></extra>"))
            fig_sv.add_hline(y=45,line_color="#ffcc00",line_dash="dash",
                annotation_text="Alert threshold 45°",annotation_font_color="#ffcc00")
            fig_sv.update_layout(**PL,height=270,yaxis_title="Body Angle (°)")
            ax(fig_sv); st.plotly_chart(fig_sv,use_container_width=True)

        st.markdown("### 🚨 Top 3 Risk Hours")
        top3 = falls_df["Hour"].value_counts().head(3)
        cols3 = st.columns(3)
        c3colors = ["#ff003c","#ff6600","#ffcc00"]
        for i,(hour,count) in enumerate(top3.items()):
            pct = int(count/max(1,n_falls)*100)
            cols3[i].markdown(f"""
            <div style="background:#080f1a;border:1px solid {c3colors[i]};
                border-left:4px solid {c3colors[i]};border-radius:4px;
                padding:16px;text-align:center">
                <div style="font-size:2rem;font-weight:900;color:{c3colors[i]}">{hour:02d}:00</div>
                <div style="color:#8fa0b5;font-size:.8rem;margin-top:4px">
                    {count} falls · {pct}% of total</div>
            </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
#  TAB 5 — TELEMETRY DB
# ══════════════════════════════════════════════════════════════
with tab5:
    st.markdown("### 📂 Secure Encrypted Ledger")
    if has_data:
        fc1,fc2,fc3 = st.columns([1,1,2])
        with fc1: ef = st.selectbox("Event filter",["ALL"]+sorted(df["Event"].unique().tolist()))
        with fc2: so = st.selectbox("Sort",["Newest first","Oldest first"])
        with fc3: srch = st.text_input("Search details",placeholder="CRITICAL, Angle:70 …")

        disp = df.copy()
        if ef!="ALL": disp=disp[disp["Event"]==ef]
        if srch: disp=disp[disp["Details"].astype(str).str.contains(srch,case=False,na=False)]
        disp = disp.sort_values("Timestamp",ascending=(so=="Oldest first"))
        show_cols = [c for c in ["Timestamp","Event","Details","Vital","File"] if c in disp.columns]
        show = disp[show_cols].copy()
        show["Timestamp"] = show["Timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
        st.dataframe(show,use_container_width=True,hide_index=True,height=420)

        d1,d2 = st.columns(2)
        d1.download_button("💾 Full CSV Archive",df.to_csv(index=False).encode("utf-8"),
            "GuardianPi_BlackBox.csv","text/csv",use_container_width=True)
        d2.download_button("📥 Filtered CSV",disp.to_csv(index=False).encode("utf-8"),
            "GuardianPi_Filtered.csv","text/csv",use_container_width=True)

        st.divider()
        st.markdown("### 📊 Quick Stats")
        s1,s2,s3,s4 = st.columns(4)
        s1.metric("Rows in DB",    len(df))
        s2.metric("Unique Events", df["Event"].nunique())
        s3.metric("Days Covered",  days_span)
        s4.metric("Falls / Day",   f"{rate_day:.1f}")
    else:
        st.markdown('<div style="height:280px;display:flex;align-items:center;'
                    'justify-content:center;border:1px dashed #1a2e47;border-radius:8px;'
                    'color:#4a6080;font-size:1.1rem">[ NO INCIDENTS IN DATABASE ]</div>',
                    unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
#  FOOTER + AUTO-REFRESH
# ─────────────────────────────────────────────────────────────
st.divider()
st.markdown(
    f"<div style='text-align:center;color:#1a2e47;font-size:.72rem;letter-spacing:2px'>"
    f"GUARDIANPI ELDERCARE SENTINEL v3.0  ·  SECURE MONITORING SYSTEM  ·  "
    f"{datetime.datetime.now().year}</div>", unsafe_allow_html=True)

if auto_refresh:
    time.sleep(3); st.cache_data.clear(); st.rerun()

# python -m streamlit run dashboard.py