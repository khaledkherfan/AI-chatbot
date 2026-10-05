"""
Draws the JUST Student Assistant business-logic component diagram as a PNG.
Run:  python docs/draw_business_logic.py
Output: docs/business-logic-diagram.png
"""
import re
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⌀-⏿]"
)
def clean(s):
    return _EMOJI.sub("", s).replace("  ", " ").strip()

fig, ax = plt.subplots(figsize=(17, 12.5))
ax.set_xlim(0, 17)
ax.set_ylim(0, 12.5)
ax.axis("off")

# ----- colour palette -----
C_USER, C_USER_F   = "#4285f4", "#e8f0fe"
C_FRONT, C_FRONT_F = "#34a853", "#e6f4ea"
C_CORE, C_CORE_F   = "#f4a142", "#fff4e5"
C_HELP, C_HELP_F   = "#a142f4", "#f3e8fd"
C_EXT, C_EXT_F     = "#ea4335", "#fde8e8"
C_DATA, C_DATA_F   = "#00897b", "#e0f2f1"

boxes = {}  # name -> (cx, cy, w, h)

def box(name, x, y, w, h, label, face, edge, fontsize=10):
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle="round,pad=0.02,rounding_size=0.12",
                       linewidth=2, edgecolor=edge, facecolor=face, zorder=2)
    ax.add_patch(p)
    ax.text(x + w/2, y + h/2, clean(label), ha="center", va="center",
            fontsize=fontsize, fontweight="bold", zorder=3)
    boxes[name] = (x + w/2, y + h/2, w, h)

def band(x, y, w, h, label, color):
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle="round,pad=0.02,rounding_size=0.1",
                       linewidth=1.5, edgecolor=color, facecolor="none",
                       linestyle=(0, (6, 4)), zorder=1)
    ax.add_patch(p)
    ax.text(x + 0.2, y + h - 0.27, label, ha="left", va="center",
            fontsize=11, fontweight="bold", color=color, zorder=3)

def edge_point(cx, cy, w, h, tx, ty):
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    sx = (w/2) / abs(dx) if dx else 1e9
    sy = (h/2) / abs(dy) if dy else 1e9
    s = min(sx, sy)
    return cx + dx*s, cy + dy*s

def connect(a, b, color="#555555", lw=2.0, two=False, label=None,
            rad=0.0, lpos=0.5, ldy=0.0, ldx=0.0):
    ax1, ay1, aw, ah = boxes[a]
    bx1, by1, bw, bh = boxes[b]
    sx, sy = edge_point(ax1, ay1, aw, ah, bx1, by1)
    ex, ey = edge_point(bx1, by1, bw, bh, ax1, ay1)
    arrowstyle = "<|-|>" if two else "-|>"
    arr = FancyArrowPatch((sx, sy), (ex, ey),
                          arrowstyle=arrowstyle, mutation_scale=15,
                          linewidth=lw, color=color,
                          connectionstyle=f"arc3,rad={rad}", zorder=1.5)
    ax.add_patch(arr)
    if label:
        mx = sx + (ex - sx) * lpos + ldx
        my = sy + (ey - sy) * lpos + ldy
        ax.text(mx, my, label, ha="center", va="center", fontsize=8.5,
                style="italic", color=color,
                bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none",
                          alpha=0.92), zorder=4)

# ===== Title =====
ax.text(8.5, 12.1, "JUST Student Assistant — Components & How They Interact",
        ha="center", va="center", fontsize=17, fontweight="bold")

# ===== Bands (give each a clear title row at top) =====
band(0.3, 10.4, 16.4, 1.35, "USERS", C_USER)
band(0.3, 8.0,  16.4, 2.0,  "WHAT THE STUDENT SEES  (App / Web pages)", C_FRONT)
band(0.3, 4.6,  9.9,  3.0,  "THE DECISION-MAKER + SPECIALIST WORKERS", C_HELP)
band(10.5, 0.4, 6.2,  7.2,  "RECORDS & ADMIN", C_DATA)
band(0.3, 0.4,  9.9,  2.0,  "OUTSIDE THE SYSTEM", C_EXT)

# ===== USERS =====
box("student", 2.5, 10.5, 4.5, 0.75, "Student", C_USER_F, C_USER, 12)
box("admin",  10.0, 10.5, 4.5, 0.75, "Administrator", C_USER_F, C_USER, 12)

# ===== FRONT (features) =====
box("chat",  0.7, 8.25, 3.5, 1.1, "Chat & Vision\nask in text or\nupload an image", C_FRONT_F, C_FRONT, 9.5)
box("plan",  4.5, 8.25, 3.2, 1.1, "Study Planner\nbuild a semester plan", C_FRONT_F, C_FRONT, 9.5)
box("cal",   8.0, 8.25, 3.2, 1.1, "Calendar &\nReminders", C_FRONT_F, C_FRONT, 9.5)
box("login",11.5, 8.25, 3.2, 1.1, "Login /\nRegister", C_FRONT_F, C_FRONT, 9.5)

# ===== CORE + HELPERS (band 4.6 - 7.6) =====
box("orch",  2.6, 6.25, 3.6, 0.8, "Query Orchestrator\n(decides how to answer)", C_CORE_F, C_CORE, 10)
box("ai",    6.5, 6.25, 3.0, 0.8, "AI Writer", C_HELP_F, C_HELP, 10.5)
box("match", 0.7, 4.9,  2.5, 0.8, "Smart Matcher", C_HELP_F, C_HELP, 9.5)
box("mem",   3.4, 4.9,  2.6, 0.8, "Fast Memory\n(Cache)", C_HELP_F, C_HELP, 9.5)
box("fetch", 6.5, 4.9,  3.0, 0.8, "Information\nFetcher", C_HELP_F, C_HELP, 9.5)

# ===== RECORDS & ADMIN (right column) =====
box("acc",   10.8, 6.2, 5.6, 0.8, "Accounts &\nChat History", C_DATA_F, C_DATA, 9.5)
box("calsvc",10.8, 4.9, 5.6, 0.8, "Calendar Service", C_DATA_F, C_DATA, 9.5)
box("stats", 10.8, 3.6, 5.6, 0.8, "Usage Analytics", C_DATA_F, C_DATA, 9.5)
box("dash",  10.8, 1.0, 5.6, 1.0, "Admin Dashboard\n(inspect data & metrics)", C_DATA_F, C_DATA, 9.5)

# ===== EXTERNAL =====
box("just",  0.7, 0.7, 4.3, 1.0, "Official JUST\nwebsites & PDFs", C_EXT_F, C_EXT, 10)
box("cloud", 5.3, 0.7, 4.4, 1.0, "AI Provider\n(OpenAI / Groq)", C_EXT_F, C_EXT, 10)

# ===== CONNECTIONS =====
# users -> features
connect("student", "chat",  color=C_USER, lw=2.2, label="asks", lpos=0.62)
connect("student", "plan",  color=C_USER, lw=2.0)
connect("student", "cal",   color=C_USER, lw=2.0)
connect("student", "login", color=C_USER, lw=2.0)
connect("admin",   "dash",  color=C_USER, lw=2.2, label="monitors", lpos=0.12, rad=0.05)

# features -> core
connect("chat",  "orch",   color="#666", lw=2.2, label="question", lpos=0.55)
connect("plan",  "orch",   color="#666", lw=2.0)
connect("login", "acc",    color="#666", lw=2.0, label="identity", lpos=0.5)
connect("cal",   "calsvc", color="#666", lw=2.0)

# orchestrator <-> helpers
connect("orch", "match", color=C_HELP, two=True, label="recognise", lpos=0.5, ldx=-0.1)
connect("orch", "mem",   color=C_HELP, two=True, label="reuse / save", lpos=0.5)
connect("orch", "fetch", color=C_HELP, lw=2.0, label="when new", lpos=0.55)
connect("orch", "ai",    color=C_HELP, lw=2.0, label="write answer", lpos=0.5)
connect("orch", "stats", color="#999", lw=1.6, rad=-0.25, label="records usage",
        lpos=0.35, ldy=0.25)

# helpers -> external
connect("fetch", "just",  color=C_EXT, lw=2.0, label="reads facts", lpos=0.6, ldx=-0.3)
connect("ai",    "cloud", color=C_EXT, two=True, lw=2.0, label="language model",
        lpos=0.7, ldx=0.4)

# calendar service <-> memory
connect("calsvc", "mem", color="#999", two=True, lw=1.6, rad=0.12)
# dashboard internal
connect("dash", "stats", color=C_DATA, lw=1.8)

# AI writer streams the finished answer back to chat
connect("ai", "chat", color="#34a853", lw=2.0, rad=0.32, label="streams answer",
        lpos=0.5, ldy=0.2)

# ===== Legend =====
legend = [
    Line2D([0],[0], color=C_USER, lw=3, label="User action"),
    Line2D([0],[0], color=C_HELP, lw=3, label="Internal decision / work"),
    Line2D([0],[0], color=C_EXT,  lw=3, label="Talks to outside source"),
    Line2D([0],[0], color="#34a853", lw=3, label="Answer back to student"),
]
ax.legend(handles=legend, loc="lower center", bbox_to_anchor=(0.5, 0.0),
          ncol=4, frameon=False, fontsize=10)

plt.subplots_adjust(left=0.02, right=0.98, top=0.96, bottom=0.04)
out = __file__.replace("draw_business_logic.py", "business-logic-diagram.png")
plt.savefig(out, dpi=170, bbox_inches="tight", facecolor="white")
print("Saved:", out)
