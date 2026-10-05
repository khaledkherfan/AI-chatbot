"""
Draws the CACHING / REDIS core diagram for the JUST Student Assistant.
Redis is shown as the heart of the system, with the fast READ (cache-hit)
path on the left and the background WRITE / LEARN path on the right.

Run:  python docs/draw_caching.py
Output: docs/caching-redis-diagram.png
"""
import re
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Ellipse, Rectangle
from matplotlib.lines import Line2D

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⌀-⏿]"
)
def clean(s):
    return _EMOJI.sub("", s).replace("  ", " ").strip()

fig, ax = plt.subplots(figsize=(17, 11.5))
ax.set_xlim(0, 17)
ax.set_ylim(0, 11.5)
ax.axis("off")

# ----- palette -----
REDIS_RED   = "#d82c20"
REDIS_RED_F = "#fdecea"
C_READ,  C_READ_F  = "#1a73e8", "#e8f0fe"   # fast read / hit  (blue)
C_WRITE, C_WRITE_F = "#f4a142", "#fff4e5"   # learn / write    (orange)
C_SPEED, C_SPEED_F = "#a142f4", "#f3e8fd"   # in-process cache (purple)
C_OK,    C_OK_F    = "#34a853", "#e6f4ea"   # output           (green)
C_EXT,   C_EXT_F   = "#00897b", "#e0f2f1"   # external source

boxes = {}

def box(name, x, y, w, h, label, face, edge, fontsize=10):
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle="round,pad=0.02,rounding_size=0.1",
                       linewidth=2, edgecolor=edge, facecolor=face, zorder=3)
    ax.add_patch(p)
    ax.text(x + w/2, y + h/2, clean(label), ha="center", va="center",
            fontsize=fontsize, fontweight="bold", zorder=4)
    boxes[name] = (x + w/2, y + h/2, w, h)

def cylinder(name, x, y, w, h, edge, face):
    eh = 0.55
    ax.add_patch(Rectangle((x, y), w, h, facecolor=face, edgecolor="none", zorder=2))
    ax.add_line(Line2D([x, x], [y, y + h], color=edge, lw=2.6, zorder=3))
    ax.add_line(Line2D([x + w, x + w], [y, y + h], color=edge, lw=2.6, zorder=3))
    ax.add_patch(Ellipse((x + w/2, y), w, eh, facecolor=face, edgecolor=edge,
                         lw=2.6, zorder=2))
    ax.add_patch(Ellipse((x + w/2, y + h), w, eh, facecolor=face, edgecolor=edge,
                         lw=2.6, zorder=4))
    boxes[name] = (x + w/2, y + h/2, w, h)

def edge_point(cx, cy, w, h, tx, ty):
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    sx = (w/2) / abs(dx) if dx else 1e9
    sy = (h/2) / abs(dy) if dy else 1e9
    s = min(sx, sy)
    return cx + dx*s, cy + dy*s

def connect(a, b, color="#555", lw=2.0, two=False, label=None, rad=0.0,
            lpos=0.5, ldx=0.0, ldy=0.0, style="-|>"):
    ax1, ay1, aw, ah = boxes[a]
    bx1, by1, bw, bh = boxes[b]
    sx, sy = edge_point(ax1, ay1, aw, ah, bx1, by1)
    ex, ey = edge_point(bx1, by1, bw, bh, ax1, ay1)
    arr = FancyArrowPatch((sx, sy), (ex, ey),
                          arrowstyle=("<|-|>" if two else style),
                          mutation_scale=15, linewidth=lw, color=color,
                          connectionstyle=f"arc3,rad={rad}", zorder=2.5)
    ax.add_patch(arr)
    if label:
        mx = sx + (ex - sx) * lpos + ldx
        my = sy + (ey - sy) * lpos + ldy
        ax.text(mx, my, label, ha="center", va="center", fontsize=8.8,
                style="italic", color=color,
                bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none",
                          alpha=0.92), zorder=5)

# ===== Title =====
ax.text(8.5, 11.0, "Caching & Redis — the Core of the System",
        ha="center", va="center", fontsize=18, fontweight="bold")
ax.text(8.5, 10.5,
        "Known questions are answered instantly from memory; new ones are learned and stored for next time.",
        ha="center", va="center", fontsize=10.5, style="italic", color="#555")

# ===== Column headers =====
ax.text(2.7, 9.7, "FAST  READ  PATH", ha="center", fontsize=12,
        fontweight="bold", color=C_READ)
ax.text(2.7, 9.35, "(cache HIT — no lookup)", ha="center", fontsize=9,
        style="italic", color=C_READ)
ax.text(14.2, 9.7, "LEARN  /  WRITE  PATH", ha="center", fontsize=12,
        fontweight="bold", color=C_WRITE)
ax.text(14.2, 9.35, "(cache MISS — runs in background)", ha="center",
        fontsize=9, style="italic", color=C_WRITE)

# ===== CENTER: Redis cylinder =====
cyl_x, cyl_y, cyl_w, cyl_h = 5.9, 2.6, 5.2, 5.9
cylinder("redis", cyl_x, cyl_y, cyl_w, cyl_h, REDIS_RED, REDIS_RED_F)
ax.text(cyl_x + cyl_w/2, cyl_y + cyl_h - 0.15, "REDIS",
        ha="center", va="center", fontsize=15, fontweight="bold", color=REDIS_RED,
        zorder=5)
ax.text(cyl_x + cyl_w/2, cyl_y + cyl_h - 0.62, "in-memory cache (very fast)",
        ha="center", va="center", fontsize=8.5, style="italic", color=REDIS_RED,
        zorder=5)

# four key types stored inside Redis
keytexts = [
    ("data:<topic>", "the saved answer (JSON: fees, dates, ...)"),
    ("alias:<phrase>", "phrase  ->  topic   (AR + EN wording)"),
    ("emb:<phrase>", "meaning vector for smart matching"),
    ("canonical:<topic>:aliases", "all known phrasings of a topic"),
]
ky = cyl_y + cyl_h - 1.35
for k, desc in keytexts:
    ax.add_patch(FancyBboxPatch((cyl_x + 0.35, ky - 0.34), cyl_w - 0.7, 0.7,
                 boxstyle="round,pad=0.02,rounding_size=0.06",
                 linewidth=1.2, edgecolor=REDIS_RED, facecolor="white", zorder=5))
    ax.text(cyl_x + cyl_w/2, ky + 0.12, k, ha="center", va="center",
            fontsize=9.5, fontweight="bold", color=REDIS_RED, zorder=6,
            family="monospace")
    ax.text(cyl_x + cyl_w/2, ky - 0.16, desc, ha="center", va="center",
            fontsize=7.6, color="#444", zorder=6)
    ky -= 0.92

ax.text(cyl_x + cyl_w/2, cyl_y - 0.05,
        "every key auto-expires (TTL)  ->  data stays fresh",
        ha="center", va="center", fontsize=8.3, style="italic", color=REDIS_RED,
        zorder=6)

# ===== TOP: in-process speed layer =====
box("inproc", 5.9, 8.95, 5.2, 0.8,
    "In-process Embedding Cache  (speed layer)\nkeeps vectors in RAM to avoid scanning Redis every time",
    C_SPEED_F, C_SPEED, 8.6)

# ===== LEFT: fast read path =====
box("q",    0.5, 8.4, 4.4, 0.8, "Student question", C_READ_F, C_READ, 10)
box("emb",  0.5, 7.0, 4.4, 0.8, "Turn question into\na meaning vector", C_READ_F, C_READ, 9.5)
box("sim",  0.5, 5.6, 4.4, 0.8, "Compare vs known phrases\n(cosine similarity)", C_READ_F, C_READ, 9.5)
box("key",  0.5, 4.2, 4.4, 0.8, "Best match  ->  topic key", C_READ_F, C_READ, 9.5)
box("hit",  0.5, 2.7, 4.4, 0.85, "Instant answer\nto the student", C_OK_F, C_OK, 10.5)

connect("q", "emb", color=C_READ)
connect("emb", "sim", color=C_READ)
connect("sim", "key", color=C_READ)
connect("inproc", "sim", color=C_SPEED, lw=1.8, rad=-0.2, label="reads vectors",
        lpos=0.55, ldy=0.0)
connect("key", "redis", color=C_READ, lw=2.4, label="look up  data:<topic>",
        lpos=0.5, ldy=0.28)
connect("redis", "hit", color=C_OK, lw=2.4, label="HIT  ->  return saved answer",
        lpos=0.5, ldy=-0.3, rad=0.05)

# ===== RIGHT: learn / write path =====
box("miss",  12.1, 8.4, 4.4, 0.8, "No good match\n(new question)", C_WRITE_F, C_WRITE, 9.5)
box("fetch", 12.1, 7.0, 4.4, 0.8, "Read official JUST\nwebsites & PDFs", C_EXT_F, C_EXT, 9.5)
box("alias", 12.1, 5.6, 4.4, 0.8, "AI writes the phrasings\n(aliases: AR + EN)", C_WRITE_F, C_WRITE, 9.5)
box("embw",  12.1, 4.2, 4.4, 0.8, "Make meaning vectors\nfor each phrasing", C_WRITE_F, C_WRITE, 9.5)
box("save",  12.1, 2.7, 4.4, 0.85, "Save answer + phrasings\n+ vectors  (with TTL)", C_WRITE_F, C_WRITE, 9.5)

connect("miss", "fetch", color=C_WRITE)
connect("fetch", "alias", color=C_WRITE)
connect("alias", "embw", color=C_WRITE)
connect("embw", "save", color=C_WRITE)
connect("save", "redis", color=C_WRITE, lw=2.4, label="store all 4 key types",
        lpos=0.5, ldy=-0.32, rad=-0.05)
# refresh the speed layer after a write
connect("save", "inproc", color=C_SPEED, lw=1.6, rad=-0.42, style="-|>",
        label="refresh speed layer", lpos=0.45, ldx=0.65, ldy=-0.1)

# ===== bottom LEARN-LOOP note =====
ax.annotate("", xy=(2.7, 2.55), xytext=(14.2, 2.55),
            arrowprops=dict(arrowstyle="-|>", color="#888", lw=1.6,
                            connectionstyle="arc3,rad=0.18"), zorder=1)
ax.text(8.5, 1.35,
        "LEARN LOOP:  a question answered the slow way once is stored in Redis,\n"
        "so the very next time it is asked it returns instantly from the FAST READ PATH.",
        ha="center", va="center", fontsize=9.5, color="#555",
        bbox=dict(boxstyle="round,pad=0.4", fc="#f7f7f7", ec="#bbb"))

# ===== Legend =====
legend = [
    Line2D([0],[0], color=C_READ,  lw=3, label="Fast read (cache hit)"),
    Line2D([0],[0], color=C_WRITE, lw=3, label="Learn / write (cache miss)"),
    Line2D([0],[0], color=C_SPEED, lw=3, label="In-RAM speed layer"),
    Line2D([0],[0], color=C_OK,    lw=3, label="Answer to student"),
    Line2D([0],[0], color=REDIS_RED, lw=3, label="Redis store"),
]
ax.legend(handles=legend, loc="lower center", bbox_to_anchor=(0.5, -0.02),
          ncol=5, frameon=False, fontsize=9.5)

plt.subplots_adjust(left=0.02, right=0.98, top=0.96, bottom=0.05)
out = __file__.replace("draw_caching.py", "caching-redis-diagram.png")
plt.savefig(out, dpi=170, bbox_inches="tight", facecolor="white")
print("Saved:", out)
