"""
🦌 每日打卡 —— AstrBot QQ 人机插件
====================================
只有一个项目 🦌，每天可多次打卡（30分钟冷却）。
🦌表 输出图片格式日历（每个格子为指定图片背景）。

指令（群聊）：
  🦌                  → 打卡一次
  🦌表 [年-月]        → 查看打卡表（图片）
  🦌统计 [年-月]       → 查看统计
  🦌撤销              → 撤销一次
  🦌背景 [图片/URL]    → 设置自定义格子背景
  🦌背景恢复           → 恢复默认背景

🦌友 / 帮🦌 好友系统（全局好友，不按群）：
  🦌友添加 @用户      → 发送好友请求
  🦌友同意 @用户      → 接受好友请求
  🦌友拒绝 @用户      → 拒绝好友请求
  🦌友删除 @用户      → 删除好友
  🦌友列表            → 查看好友列表
  🦌友请求            → 查看待处理请求
  帮🦌 @好友          → 帮好友打卡（使用好友冷却，打卡给好友）
"""

import json, os, calendar, time, io, re
from datetime import date
from typing import Optional
from astrbot.api.star import Context, Star
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import AstrBotConfig, logger
import astrbot.api.message_components as Comp

__plugin_name__ = "🦌打卡"
__plugin_desc__ = "🦌 每日打卡，自动月表格图片"
__plugin_version__ = "1.3.0"
__plugin_author__ = "🦌"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ── Pillow ──────────────────────────────────────────────────────────
_HAS_PIL = False
try:
    from PIL import Image as PILImage, ImageDraw, ImageFont
    _HAS_PIL = True
except ImportError:
    pass

try:
    from urllib.request import urlopen as _urlopen
    _HAS_URLOPEN = True
except ImportError:
    _HAS_URLOPEN = False

# ── 格子背景图片 ─────────────────────────────────────────────────────
CELL_IMG_DIR = os.path.dirname(os.path.abspath(__file__))
_CELL_IMG = None

# ── 打卡标记图片（替代手画勾） ──────────────────────────────────────
_CHECK_MARK_IMG = None
def _get_check_mark(size):
    global _CHECK_MARK_IMG
    if _CHECK_MARK_IMG is not None:
        return _CHECK_MARK_IMG
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "图层 0.png")
    if os.path.exists(p):
        try:
            _CHECK_MARK_IMG = PILImage.open(p).convert("RGBA").resize((size, size), PILImage.LANCZOS)
            return _CHECK_MARK_IMG
        except:
            pass
    return None# ── 用户自定义背景 ──────────────────────────────────────────────────
USER_BG_DIR = os.path.join(DATA_DIR, "bg")
os.makedirs(USER_BG_DIR, exist_ok=True)

def _user_bg_path(uid, gid="default"):
    """返回用户自定义背景图片的保存路径（优先返回存在的文件，默认 .png）。"""
    d = os.path.join(USER_BG_DIR, gid)
    os.makedirs(d, exist_ok=True)
    # 如果已有文件，返回实际存在的路径
    for ext in ('gif', 'png', 'jpg', 'jpeg', 'bmp'):
        p = os.path.join(d, f"{uid}.{ext}")
        if os.path.exists(p):
            return p
    return os.path.join(d, f"{uid}.png")

def _clear_user_bg(uid, gid):
    """清除用户所有格式的背景图片。"""
    d = os.path.join(USER_BG_DIR, gid)
    base = os.path.join(d, uid)
    for ext in ('gif', 'png', 'jpg', 'jpeg', 'bmp'):
        p = f"{base}.{ext}"
        try:
            if os.path.exists(p):
                os.remove(p)
        except:
            pass

def _get_cell_img(size, uid=None, gid=None):
    """获取格子背景图：优先使用用户自定义 → 全局缓存 → 扫描插件目录。"""
    # 用户自定义背景（支持 GIF）
    if uid and gid:
        bg_path = _find_user_bg(uid, gid)
        if bg_path:
            try:
                return PILImage.open(bg_path).convert("RGB").resize((size, size), PILImage.LANCZOS)
            except:
                pass
    # 全局缓存
    global _CELL_IMG
    if _CELL_IMG is not None:
        return _CELL_IMG
    # 扫描插件目录（含 .gif）
    if not os.path.isdir(CELL_IMG_DIR):
        return None
    for fname in os.listdir(CELL_IMG_DIR):
        if fname.lower().endswith(('.jpg','.jpeg','.png','.bmp','.gif')):
            try:
                path = os.path.join(CELL_IMG_DIR, fname)
                _CELL_IMG = PILImage.open(path).convert("RGB").resize((size, size), PILImage.LANCZOS)
                return _CELL_IMG
            except:
                pass
    return None

# ── GIF 动画背景支持 ──────────────────────────────────────────────────

def _find_user_bg(uid, gid):
    """查找用户自定义背景文件（支持任意扩展名）。"""
    d = os.path.join(USER_BG_DIR, gid)
    base = os.path.join(d, uid)
    for ext in ('gif', 'png', 'jpg', 'jpeg', 'bmp'):
        p = f"{base}.{ext}"
        if os.path.exists(p):
            return p
    return None

def _is_animated_gif(path):
    """检查是否为动图 GIF（>1 帧）。"""
    if not path:
        return False
    try:
        with PILImage.open(path) as img:
            return img.format == 'GIF' and getattr(img, 'n_frames', 1) > 1
    except:
        return False

def _load_gif_frames(path, size):
    """加载动图 GIF 所有帧，缩放到 (size,size)。返回 (frames_rgba列表, durations_ms列表)。"""
    img = PILImage.open(path)
    frames, durations = [], []
    try:
        while True:
            frame = img.copy().convert('RGBA').resize((size, size), PILImage.LANCZOS)
            frames.append(frame)
            dur = img.info.get('duration', 100)
            durations.append(max(dur, 20))
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    return frames, durations

def _get_animated_bg(size, uid=None, gid=None):
    """如果用户背景是动图 GIF，返回 (frames, durations) 否则 (None, None)。"""
    if not uid or not gid:
        return None, None
    bg_path = _find_user_bg(uid, gid)
    if not bg_path or not bg_path.lower().endswith('.gif'):
        return None, None
    if not _is_animated_gif(bg_path):
        return None, None
    try:
        frames, durations = _load_gif_frames(bg_path, size)
        if len(frames) > 1:
            return frames, durations
    except:
        pass
    return None, None

def _is_gif_bytes(data):
    """检查字节数据前 6 字节是否为 GIF 文件头。"""
    return data[:6] in (b'GIF87a', b'GIF89a')

# ── 数据 ─────────────────────────────────────────────────────────────
def _p(uid, gid="default"):
    d = os.path.join(DATA_DIR, gid)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{uid}.json")

def _load(uid, gid="default"):
    p = _p(uid, gid)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"records": {}}

def _save(uid, gid, data):
    with open(_p(uid, gid), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _mk(y, m):
    return f"{y}-{m:02d}"

def _days(y, m):
    return calendar.monthrange(y, m)[1]

def _ensure(data, y, m):
    k = _mk(y, m)
    if k not in data["records"]:
        data["records"][k] = {}
    return data["records"][k]

def _parse_ym(s):
    n = date.today()
    if s:
        s = s.strip().replace("年","-").replace("月","").replace("/","-")
        try:
            p = s.split("-")
            if len(p) == 1: return n.year, int(p[0])
            if len(p) >= 2: return int(p[0]), int(p[1])
        except:
            pass
    return n.year, n.month

# ── 群称呼存储 ──────────────────────────────────────────────────────
_GROUP_NAMES_PATH = os.path.join(DATA_DIR, "group_names.json")
def _get_group_name(gid):
    if not os.path.exists(_GROUP_NAMES_PATH):
        return ""
    try:
        with open(_GROUP_NAMES_PATH, encoding="utf-8") as f:
            names = json.load(f)
            return names.get(gid, "")
    except:
        return ""

def _set_group_name(gid, name):
    names = {}
    if os.path.exists(_GROUP_NAMES_PATH):
        try:
            with open(_GROUP_NAMES_PATH, encoding="utf-8") as f:
                names = json.load(f)
        except:
            pass
    names[gid] = name
    with open(_GROUP_NAMES_PATH, "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False, indent=2)

def _calc_streak(rec, cur, td, total):
    streak = 0
    counting = False
    start = td if cur else total
    for d in range(start, 0, -1):
        cnt = rec.get(str(d), {}).get("🦌", 0)
        if cnt > 0:
            if not counting: counting = True
            streak += 1
        else:
            if counting: break
            if d == start: continue
            break
    return streak

# ── 好友系统 ──────────────────────────────────────────────────────────
_FRIENDS_PATH = os.path.join(DATA_DIR, "friends.json")

def _load_friends():
    if os.path.exists(_FRIENDS_PATH):
        try:
            with open(_FRIENDS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"friendships": {}, "pending": {}}

def _save_friends(data):
    with open(_FRIENDS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _are_friends(uid1, uid2):
    data = _load_friends()
    return uid2 in data["friendships"].get(uid1, [])

def _add_friend(u1, u2):
    data = _load_friends()
    for a, b in ((u1, u2), (u2, u1)):
        if a not in data["friendships"]:
            data["friendships"][a] = []
        if b not in data["friendships"][a]:
            data["friendships"][a].append(b)
    _save_friends(data)

def _remove_friend(u1, u2):
    data = _load_friends()
    for a, b in ((u1, u2), (u2, u1)):
        if a in data["friendships"] and b in data["friendships"][a]:
            data["friendships"][a].remove(b)
    _save_friends(data)

def _send_request(from_uid, to_uid):
    data = _load_friends()
    if to_uid not in data["pending"]:
        data["pending"][to_uid] = []
    if from_uid not in data["pending"][to_uid]:
        data["pending"][to_uid].append(from_uid)
    _save_friends(data)

def _get_mentioned_uid(event):
    """从消息中提取第一个 @ 的用户 ID"""
    # 方法1: AstrBot 消息组件
    try:
        for comp in event.message_obj.message:
            # 兼容不同版本的 AstrBot (At / at / AtElem)
            comp_name = type(comp).__name__.lower()
            if 'at' in comp_name:
                # 尝试不同属性名
                for attr in ('qq', 'user_id', 'target', 'uid'):
                    val = getattr(comp, attr, None)
                    if val is not None:
                        return str(val)
    except:
        pass
    # 方法2: CQ 码正则
    try:
        raw = event.message_obj.raw_message
        raw_str = raw if isinstance(raw, str) else str(raw)
        m = re.search(r'\[CQ:at,qq=(\d+)\]', raw_str)
        if m:
            return m.group(1)
    except:
        pass
    # 方法3: 直接从 raw_text 提取纯 @ 后的数字
    try:
        raw = event.message_obj.raw_message
        raw_str = raw if isinstance(raw, str) else str(raw)
        # 匹配 @xxxxx 或 @ 后面跟的数字
        m = re.search(r'@(\d+)', raw_str)
        if m:
            return m.group(1)
    except:
        pass
    return None

# ── 文本表格 ─────────────────────────────────────────────────────────
def _table_text(data, y, m):
    rec = _ensure(data, y, m)
    total = _days(y, m)
    n = date.today()
    cur = (y == n.year and m == n.month)
    td = n.day
    checked = eligible = 0
    for d in range(1, total+1):
        if cur and d > td: continue
        eligible += 1
        if rec.get(str(d), {}).get("🦌", 0) > 0: checked += 1
    pct = round(checked/eligible*100) if eligible else 0
    bar = "█"*(pct//10) + "░"*(10-pct//10)
    lines = [f"🦌 {y}年{m}月", "", f"{'日':>3s}  🦌", "──────"]
    for d in range(1, total+1):
        cnt = rec.get(str(d), {}).get("🦌", 0)
        pfx = "👉" if (cur and d == td) else ("⋯" if (cur and d > td) else f"{d:>2d}")
        cell = "·" if cnt==0 else ("🦌" if cnt==1 else (f"🦌×{cnt}" if cnt<10 else "🦌×N"))
        lines.append(f"{pfx}  {cell}")
    lines.extend(["──────", f"完成  {bar} {pct}%"])
    return "\n".join(lines)

# ── GIF 压缩配置 ──────────────────────────────────────────────────
GIF_MAX_COLORS = 128          # 颜色数越少文件越小（64~192 平衡画质与体积）
GIF_OPTIMIZE = True            # 启用 GIF 优化（去除冗余数据）
GIF_SKIP_FRAME = 0             # 动画帧数过多时跳帧：0=不跳, 1=隔1帧跳1, 2=每3帧保留1
GIF_FRAME_LIMIT = 60           # 最大帧数，超出则跳帧到不超过此值

def _compress_gif_frames(frames, durations, max_colors=GIF_MAX_COLORS):
    """对 PIL 帧列表进行调色板量化压缩，返回压缩后的帧列表。"""
    if not frames:
        return frames, durations

    # 跳帧：减少帧数
    n = len(frames)
    skip = GIF_SKIP_FRAME
    if GIF_FRAME_LIMIT and n > GIF_FRAME_LIMIT:
        skip = max(skip, (n + GIF_FRAME_LIMIT - 1) // GIF_FRAME_LIMIT - 1)
    if skip > 0:
        keep = [frames[i] for i in range(0, n, skip + 1)]
        keep_dur = []
        for i in range(0, n, skip + 1):
            total = sum(durations[j] for j in range(i, min(i + skip + 1, n)))
            keep_dur.append(total)
        frames, durations = keep, keep_dur

    # 统一转 P 模式（调色板），减少颜色数
    try:
        quantized = []
        first = frames[0].quantize(
            colors=min(max_colors, 256),
            method=PILImage.Quantize.MEDIANCUT,
            dither=PILImage.Dither.NONE
        )
        quantized.append(first)
        for f in frames[1:]:
            q = f.quantize(
                colors=min(max_colors, 256),
                palette=first,
                dither=PILImage.Dither.NONE
            )
            quantized.append(q)
        return quantized, durations
    except Exception:
        return frames, durations

# ── 图片表格（支持静态 PNG / 动态 GIF）─────────────────────────────
def _table_image(data, y, m, nickname="", uid=None, gid=None):
    if not _HAS_PIL: return None
    rec = _ensure(data, y, m)
    total = _days(y, m)
    n = date.today()
    cur = (y == n.year and m == n.month)
    td = n.day

    WHITE='#FFFFFF'; TEXT='#1E293B'; TEXT2='#64748B'
    PURPLE='#6366F1'; PURPLE2='#EEF2FF'
    RED='#DC2626'
    GRAY='#F1F5F9'; BORDER='#E2E8F0'

    # ── 字体 ──
    def _font(paths, size):
        for p in paths:
            try: return ImageFont.truetype(p, size)
            except: pass
        return ImageFont.load_default()

    fs_big   = _font(["C:/Windows/Fonts/simsun.ttc","C:/Windows/Fonts/msyh.ttc"], 30)
    fs_nick  = _font(["C:/Windows/Fonts/msyh.ttc","C:/Windows/Fonts/simsun.ttc"], 20)
    fs_date  = _font(["C:/Windows/Fonts/simsun.ttc","C:/Windows/Fonts/msyh.ttc"], 30)
    fs_check = _font(["C:/Windows/Fonts/msyhbd.ttc","C:/Windows/Fonts/msyh.ttc","C:/Windows/Fonts/simsun.ttc"], 34)
    fs_cnt   = _font(["C:/Windows/Fonts/msyhbd.ttc","C:/Windows/Fonts/msyh.ttc","C:/Windows/Fonts/simsun.ttc"], 24)
    fs_small = _font(["C:/Windows/Fonts/msyh.ttc","C:/Windows/Fonts/simsun.ttc"], 11)

    def _bold(dr, pos, text, fill, font):
        x, y = pos
        for ox, oy in ((-1,-1),(-1,1),(1,-1),(1,1),(0,0)):
            dr.text((x+ox, y+oy), text, fill=fill, font=font)

    # ── 布局 ──
    fd = (calendar.weekday(y, m-1, 1) + 1) % 7
    wk = (total + fd + 6) // 7
    cw, ch = 100, 90
    pd = 28
    title_h, nick_h = 72, 34
    footer_h = 56
    gw = 7*cw; gh = wk*ch
    iw = pd*2+gw
    ih = pd + title_h + nick_h + gh + footer_h + pd

    # 格子区域偏移
    xs = pd; yg = pd + title_h + nick_h

    # ── 检测动画背景 ──
    gif_frames, gif_durations = _get_animated_bg(max(cw, ch), uid=uid, gid=gid)
    is_animated = bool(gif_frames and len(gif_frames) > 1)
    cell_img = None if is_animated else _get_cell_img(max(cw, ch), uid=uid, gid=gid)

    # ── 单帧渲染（共用） ──
    def _render_one_frame(bg_frame=None):
        """渲染一帧日历。bg_frame: PIL Image (cw×ch) 或 None（白色格子）。"""
        img = PILImage.new('RGB', (iw, ih), WHITE)
        dr = ImageDraw.Draw(img)

        # 标题
        _bold(dr, (pd, 8), f"{m}月签到", TEXT, fs_big)
        dr.text((pd, 8 + 30), nickname or "", fill=TEXT2, font=fs_nick)

        for d in range(1, total+1):
            dw = (fd+d-1)%7; r = (fd+d-1)//7
            cx = xs+dw*cw; cy = yg+r*ch
            cnt = rec.get(str(d), {}).get("🦌", 0)
            is_tdy = cur and d==td

            if bg_frame:
                img.paste(bg_frame, (cx, cy))
            elif cell_img:
                resized = cell_img.resize((cw, ch), PILImage.LANCZOS)
                img.paste(resized, (cx, cy))
            else:
                dr.rectangle((cx+2, cy+2, cx+cw-2, cy+ch-2), fill=WHITE)

            # 日期
            _bold(dr, (cx+8, cy+ch-32), str(d), PURPLE if is_tdy else TEXT, fs_date)

            # 🦌 打卡标记
            if cnt > 0:
                mark = _get_check_mark(ch - 4)
                if mark:
                    mx = cx + (cw - mark.width) // 2 + 10
                    my = cy + (ch - mark.height) // 2
                    img.paste(mark, (mx, my), mark)
                else:
                    sx, sy = cx + 12, cy + 22
                    dr.line([(sx, sy+20), (sx+36, sy+58)], fill=RED, width=14)
                    dr.line([(sx+36, sy+58), (sx+82, sy-14)], fill=RED, width=14)
                if cnt > 1:
                    label = f"x{cnt}" if cnt < 10 else "xN"
                    dr.text((cx + cw - 30, cy + ch - 30), label, fill=RED, font=fs_cnt, anchor="lt")

        # 底部统计
        ck = el_ = 0
        for d in range(1,total+1):
            if date(y, m, d) > n: continue
            el_ += 1
            if rec.get(str(d),{}).get("🦌",0) > 0: ck += 1
        tc = sum(rec.get(str(d),{}).get("🦌",0) for d in range(1,total+1) if date(y, m, d) <= n)
        pc = round(ck/el_*100) if el_ else 0

        yf = yg+gh+12
        bw_, bh_ = 130, 12
        bax, bay = xs, yf+4
        dr.rounded_rectangle([(bax,bay),(bax+bw_,bay+bh_)], 6, fill=GRAY)
        if pc>0:
            dr.rounded_rectangle([(bax,bay),(bax+max(bh_,int(bw_*pc/100)),bay+bh_)], 6, fill=PURPLE)
        dr.text((bax+bw_+10, bay-2), f"{pc}%", fill=TEXT, font=fs_small)

        sk = _calc_streak(rec,cur,td,total)
        dr.text((bax, bay+bh_+6),
                f"总打卡 {tc} 次 · 打卡 {ck}/{el_} 天 · 连续 {sk} 天",
                fill=TEXT2, font=fs_small)

        return img

    # ── 动画 / 静态 分支输出 ──
    if is_animated:
        pil_frames = []
        for bg_frame in gif_frames:
            frame_cell = bg_frame.resize((cw, ch), PILImage.LANCZOS)
            pil_frames.append(_render_one_frame(frame_cell))

        buf = io.BytesIO()
        # 压缩：帧量化 + 跳帧 + 颜色减少
        quantized, durations = _compress_gif_frames(pil_frames, gif_durations)
        durations = [max(d, 50) for d in durations]
        quantized[0].save(buf, format='GIF', save_all=True, append_images=quantized[1:],
                          loop=0, duration=durations, disposal=2,
                          optimize=GIF_OPTIMIZE)
        buf.seek(0)
        return buf.getvalue()
    else:
        img = _render_one_frame(cell_img.resize((cw, ch), PILImage.LANCZOS) if cell_img else None)
        buf = io.BytesIO(); img.save(buf, format='PNG'); buf.seek(0)
        return buf.getvalue()

# ── 统计文本 ─────────────────────────────────────────────────────────
def _stats_text(data, y, m):
    rec = _ensure(data,y,m); total=_days(y,m)
    n=date.today()
    def _ok(d): return date(y,m,d) <= n
    tc=sum(rec.get(str(d),{}).get("🦌",0) for d in range(1,total+1) if _ok(d))
    dc=sum(1 for d in range(1,total+1) if rec.get(str(d),{}).get("🦌",0)>0 and _ok(d))
    el=sum(1 for d in range(1,total+1) if _ok(d))
    rate=round(dc/el*100) if el else 0
    cur=(y==n.year and m==n.month); td=n.day
    sk=_calc_streak(rec,cur,td,total)
    return f"🦌 {y}年{m}月 统计\n\n总打卡：{tc}次\n打卡天数：{dc}/{el}天\n完成率：{rate}%\n连续打卡：{sk}天"

# ── 发送辅助 ─────────────────────────────────────────────────────────
async def _send(event, text, img_bytes=None):
    try:
        gid = event.get_group_id()
        if not gid:
            return
        if img_bytes:
            tmp_dir = os.path.join(DATA_DIR, str(gid))
            os.makedirs(tmp_dir, exist_ok=True)
            # 检测格式：GIF 或 PNG
            ext = 'gif' if img_bytes[:6] in (b'GIF87a', b'GIF89a') else 'png'
            path = os.path.join(tmp_dir, f"_tmp_{event.get_sender_id()}.{ext}")
            # 删除旧图再生成新图
            try: os.remove(path)
            except: pass
            with open(path, "wb") as f:
                f.write(img_bytes)
            msg = f"{text}[CQ:image,file=file:///{path}]" if text else f"[CQ:image,file=file:///{path}]"
            await event.bot.api.call_action("send_group_msg", group_id=gid, message=msg)
        else:
            await event.bot.api.call_action("send_group_msg", group_id=gid, message=text or " ")
    except Exception as e:
        logger.error(f"🦌 _send 失败: {e}")

# ── 插件 ─────────────────────────────────────────────────────────────
class DeerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def handle(self, event: AstrMessageEvent):
        try:
            raw = event.message_obj.raw_message
            # 从 raw 提取可用的字符串文本（aiocqhttp Event 要用 .get("raw_message")）
            if isinstance(raw, str):
                raw_str = raw
            elif hasattr(raw, 'get'):
                raw_str = raw.get("raw_message") or raw.get("message") or str(raw)
            elif isinstance(raw, list):
                raw_str = str(raw)
            else:
                raw_str = str(raw)
            raw_str = str(raw_str) if not isinstance(raw_str, str) else raw_str
            logger.info(f"🦌 raw type={type(raw).__name__} raw={raw_str[:300]}")

            # 提取 reply_id（引用消息）
            reply_id = None
            # 方法1: CQ:reply 正则
            m_reply = re.search(r'\[CQ:reply,id=(-?\d+)\]', raw_str)
            if m_reply:
                reply_id = m_reply.group(1)
            # 方法2: AstrBot 消息组件
            if not reply_id:
                try:
                    for comp in event.message_obj.message:
                        comp_n = type(comp).__name__.lower()
                        if 'reply' in comp_n:
                            for attr in ('id', 'message_id', 'seq'):
                                val = getattr(comp, attr, None)
                                if val is not None:
                                    reply_id = str(val)
                                    break
                            if reply_id:
                                break
                except:
                    pass
            # 方法3: Event 原始 message 字段（aiocqhttp 数组格式）
            if not reply_id and hasattr(raw, 'get'):
                try:
                    evt_msg = raw.get("message", [])
                    if isinstance(evt_msg, list):
                        for seg in evt_msg:
                            if isinstance(seg, dict) and seg.get("type") == "reply":
                                reply_id = str(seg.get("data", {}).get("id", ""))
                                break
                except:
                    pass

            # 提取纯文本（剥离 CQ:reply 前缀以免干扰 🦌 匹配）
            if isinstance(raw, dict):
                text = re.sub(r'\[CQ:reply,id=-?\d+\]', '', raw.get("raw_message") or raw.get("message") or "").strip()
            elif isinstance(raw, str):
                text = re.sub(r'\[CQ:reply,id=-?\d+\]', '', raw).strip()
            elif isinstance(raw, list):
                # OneBot 数组格式：从 segments 里拼出纯文本
                parts = []
                for seg in raw:
                    if isinstance(seg, dict) and seg.get("type") == "text":
                        parts.append(seg.get("data", {}).get("text", ""))
                text = "".join(parts).strip()
            elif hasattr(raw, 'get'):
                # aiocqhttp Event 等 dict-like 对象
                text = re.sub(r'\[CQ:reply,id=-?\d+\]', '', raw.get("raw_message") or raw.get("message") or "").strip()
            else:
                text = str(raw).strip()
            if not (text.startswith("🦌") or text.startswith("帮🦌")):
                return

            # ── 帮🦌（好友互助打卡）──
            if text.startswith("帮🦌"):
                uid = str(event.get_sender_id())
                gid = str(event.get_group_id() or "")
                target_uid = _get_mentioned_uid(event)
                if not target_uid:
                    await _send(event, "请 @ 要帮助的 🦌友\n例如：帮🦌 @好友")
                    return
                if target_uid == uid:
                    await _send(event, "❌ 不能帮自己 🦌")
                    return
                if not _are_friends(uid, target_uid):
                    await _send(event, "❌ 对方不是你的 🦌友\n先发送 🦌友添加 @用户 添加好友吧")
                    return

                data = _load(target_uid, gid)
                now_ts = time.time()
                if now_ts - data.get("last_checkin", 0) < 30 * 60:
                    await _send(event, "⏳ 对方刚 🦌 过，让 TA 歇会儿吧")
                    return

                y, m, d = date.today().year, date.today().month, date.today().day
                rec = _ensure(data, y, m)
                dk = str(d)
                if dk not in rec: rec[dk] = {}
                rec[dk]["🦌"] = rec[dk].get("🦌", 0) + 1
                cnt = rec[dk]["🦌"]
                data["last_checkin"] = now_ts
                _save(target_uid, gid, data)

                await _send(event, f"✅ 帮 {target_uid} 🦌 成功！TA 今天已 🦌 {cnt} 次 🥵")
                return

            cmd = text[1:].strip()
            uid = str(event.get_sender_id())
            gid = str(event.get_group_id() or "")

            # 取群昵称 / 群自定义称呼
            nickname = ""
            if isinstance(raw, dict):
                sender = raw.get("sender", {})
                nickname = sender.get("card", "") or sender.get("nickname", "") or ""
            group_name = _get_group_name(gid) or ""

            # 🦌称呼 设置群名称
            if cmd.startswith("称呼") or cmd.startswith("name"):
                name = cmd[2:].strip() if len(cmd)>2 else ""
                if not name:
                    await _send(event, f"当前群称呼：{group_name or '未设置'}\n发送 🦌称呼 新名称 来设置")
                    return
                _set_group_name(gid, name)
                await _send(event, f"已设置本群称呼为：{name}")
                return

            # 🦌背景 设置自定义背景图片
            if cmd.startswith("背景") or cmd.startswith("bg"):
                arg = cmd[2:].strip() if len(cmd) > 2 else ""

                # 恢复默认
                if arg in ("恢复","重置","删除","clear","reset","delete"):
                    _clear_user_bg(uid, gid)
                    await _send(event, "✅ 已恢复默认背景")
                    return

                # 从消息组件中提取图片 URL
                img_url = None
                try:
                    for comp in event.message_obj.message:
                        if isinstance(comp, Comp.Image):
                            img_url = comp.url or comp.file
                            break
                except:
                    pass

                # 从原始消息解析 CQ:image
                if not img_url:
                    raw_str = raw if isinstance(raw, str) else str(raw)
                    m = re.search(r'\[CQ:image,[^\]]*url=([^,\]]+)', raw_str)
                    if m:
                        img_url = m.group(1)
                    else:
                        m = re.search(r'\[CQ:image,[^\]]*file=([^,\]]+)', raw_str)
                        if m:
                            img_url = m.group(1)

                # 从回复/引用的消息中提取图片
                if not img_url and reply_id:
                    logger.info(f"🦌 尝试引用取图 reply_id={reply_id}")
                    try:
                        # 方法A: AstrBot 消息对象可能直接挂载了回复内容
                        try:
                            reply_attr = getattr(event.message_obj, 'reply', None) or getattr(event, 'reply_message', None)
                            if reply_attr:
                                logger.info(f"🦌 找到内联回复对象 type={type(reply_attr).__name__}")
                                reply_msg = getattr(reply_attr, 'message', None) or getattr(reply_attr, 'content', None) or str(reply_attr)
                                raw_r = str(reply_msg) if not isinstance(reply_msg, str) else reply_msg
                                m = re.search(r'\[CQ:image,[^\]]*url=([^,\]]+)', raw_r)
                                if m:
                                    img_url = m.group(1)
                                    logger.info(f"🦌 内联回复取图成功")
                        except Exception as e:
                            logger.info(f"🦌 内联回复取图无: {e}")

                        # 方法B: 检查回复组件本身是否携带了原消息内容
                        try:
                            for comp in event.message_obj.message:
                                comp_n = type(comp).__name__.lower()
                                if 'reply' in comp_n:
                                    for field in ('content', 'message', 'data'):
                                        raw_content = getattr(comp, field, None) or (isinstance(getattr(comp, field, None), dict) and getattr(comp, field, None).get('message'))
                                        if raw_content:
                                            raw_str_c = str(raw_content) if not isinstance(raw_content, str) else raw_content
                                            m = re.search(r'\[CQ:image,[^\]]*url=([^,\]]+)', raw_str_c)
                                            if m:
                                                img_url = m.group(1)
                                            break
                                    if img_url:
                                        break
                        except:
                            pass

                        # 方法C: get_msg API
                        if not img_url:
                            logger.info(f"🦌 尝试 get_msg reply_id={reply_id}")
                            try:
                                reply_data = await event.bot.api.call_action("get_msg", message_id=int(reply_id))
                            except Exception:
                                try:
                                    reply_data = await event.bot.api.call_action("get_message", message_id=int(reply_id))
                                except Exception:
                                    reply_data = None
                            if reply_data:
                                if isinstance(reply_data, dict) and "data" in reply_data:
                                    reply_data = reply_data["data"]
                                reply_msg = reply_data.get("message") or reply_data.get("messages") or []
                                if isinstance(reply_msg, str):
                                    reply_msg = [{"type": "text", "data": {"text": reply_msg}}]
                                for c in reply_msg:
                                    if isinstance(c, dict):
                                        if c.get("type") == "image":
                                            img_url = c.get("data", {}).get("url", "") or c.get("data", {}).get("file", "")
                                            if img_url:
                                                break
                                    else:
                                        try:
                                            if hasattr(c, 'url') and c.url:
                                                img_url = c.url
                                                break
                                            if hasattr(c, 'file') and c.file:
                                                img_url = c.file
                                                break
                                        except:
                                            pass
                                if not img_url:
                                    raw_reply = reply_data.get("raw_message", "")
                                    if isinstance(raw_reply, list):
                                        raw_reply = str(raw_reply)
                                    for p in (r'url=([^,\]]+)', r'file=([^,\]]+)'):
                                        m = re.search(rf'\[CQ:image,[^\]]*{p}', raw_reply)
                                        if m:
                                            img_url = m.group(1)
                                            break
                    except Exception as e:
                        logger.warning(f"🦌 引用消息取图失败: {e}")

                # 从参数获取 URL
                if not img_url and (arg.startswith('http://') or arg.startswith('https://')):
                    img_url = arg

                if not img_url:
                    await _send(event, "📷 请附带图片\n用法：🦌背景 [图片] 或 🦌背景 <图片URL>\n🦌背景恢复 恢复默认")
                    return

                # 下载并保存
                try:
                    if img_url.startswith(('http://', 'https://')):
                        if not _HAS_URLOPEN:
                            await _send(event, "❌ 当前环境不支持下载图片")
                            return
                        with _urlopen(img_url, timeout=15) as resp:
                            img_data = resp.read()
                    elif img_url.startswith('file:///'):
                        with open(img_url[8:], 'rb') as f:
                            img_data = f.read()
                    else:
                        await _send(event, "⚠ 不支持的图片格式，请使用图片URL\n发送 🦌背景 并附带图片，或 🦌背景 <图片URL>")
                        return

                    # 验证图片有效
                    PILImage.open(io.BytesIO(img_data))

                    # 检测格式：GIF 用 .gif 后缀，保留动画帧
                    # 先清除旧背景文件
                    _clear_user_bg(uid, gid)
                    dst = _user_bg_path(uid, gid)
                    if _is_gif_bytes(img_data):
                        dst = dst.replace('.png', '.gif')
                    with open(dst, 'wb') as f:
                        f.write(img_data)

                    await _send(event, "✅ 自定义背景设置成功！\n发 🦌表 查看效果")
                except Exception as e:
                    logger.error(f"🦌 背景设置失败: {e}")
                    await _send(event, f"❌ 设置失败：{e}")
                return

            # 🦌友 好友系统
            if cmd.startswith("友"):
                rest = cmd[1:].strip()
                parts = rest.split(maxsplit=1)
                sub = parts[0] if parts else ""
                target_uid = _get_mentioned_uid(event)
                data_f = _load_friends()

                # 🦌友请求 / 🦌友 请求
                if sub in ("请求", "申请", "pending"):
                    pending = data_f["pending"].get(uid, [])
                    if not pending:
                        await _send(event, "📭 没有待处理的好友请求")
                        return
                    lines = [f"待处理的好友请求（{len(pending)} 条）："]
                    for i, puid in enumerate(pending, 1):
                        lines.append(f"  {i}. {puid}")
                    lines.append("发送 🦌友同意 @对方 接受")
                    await _send(event, "\n".join(lines))
                    return

                # 🦌友 添加 @xxx
                if sub in ("添加", "加", "add"):
                    if not target_uid:
                        await _send(event, "请 @ 要添加的好友\n例如：🦌友添加 @用户")
                        return
                    if target_uid == uid:
                        await _send(event, "❌ 不能添加自己为 🦌友")
                        return
                    if _are_friends(uid, target_uid):
                        await _send(event, "你们已经是 🦌友 了 🦌")
                        return
                    _send_request(uid, target_uid)
                    await _send(event, f"✅ 已向 {target_uid} 发送好友请求\n让对方发送 🦌友同意 @你 来确认")
                    return

                # 🦌友 同意 @xxx
                if sub in ("同意", "接受", "accept", "yes"):
                    if not target_uid:
                        await _send(event, "请 @ 要同意的用户\n例如：🦌友同意 @用户")
                        return
                    pending = data_f["pending"].get(uid, [])
                    if target_uid not in pending:
                        await _send(event, "❌ 没有来自该用户的好友请求")
                        return
                    data_f["pending"][uid].remove(target_uid)
                    _save_friends(data_f)
                    _add_friend(uid, target_uid)
                    await _send(event, f"✅ 和 {target_uid} 已成为 🦌友！🦌")
                    return

                # 🦌友 拒绝 @xxx
                if sub in ("拒绝", "reject", "deny", "no"):
                    if not target_uid:
                        await _send(event, "请 @ 要拒绝的用户\n例如：🦌友拒绝 @用户")
                        return
                    pending = data_f["pending"].get(uid, [])
                    if target_uid not in pending:
                        await _send(event, "❌ 没有来自该用户的好友请求")
                        return
                    data_f["pending"][uid].remove(target_uid)
                    _save_friends(data_f)
                    await _send(event, f"已拒绝 {target_uid} 的好友请求")
                    return

                # 🦌友 删除 @xxx
                if sub in ("删除", "删", "del", "remove", "unfriend"):
                    if not target_uid:
                        await _send(event, "请 @ 要删除的好友\n例如：🦌友删除 @用户")
                        return
                    if not _are_friends(uid, target_uid):
                        await _send(event, "❌ 你们还不是 🦌友")
                        return
                    _remove_friend(uid, target_uid)
                    await _send(event, f"已和 {target_uid} 解除 🦌友 关系")
                    return

                # 🦌友 列表
                if sub in ("列表", "全部", "list", "all"):
                    friends = data_f["friendships"].get(uid, [])
                    if not friends:
                        await _send(event, "你还没有 🦌友\n发送 🦌友添加 @用户 添加好友吧")
                        return
                    lines = [f"你的 🦌友（共 {len(friends)} 个）："]
                    for i, fuid in enumerate(friends, 1):
                        lines.append(f"  {i}. {fuid}")
                    await _send(event, "\n".join(lines))
                    return

                # 帮助信息
                await _send(event,
                    "🦌友 命令：\n"
                    "  🦌友添加 @用户   → 发送请求\n"
                    "  🦌友同意 @用户   → 接受请求\n"
                    "  🦌友拒绝 @用户   → 拒绝请求\n"
                    "  🦌友删除 @用户   → 删除好友\n"
                    "  🦌友列表         → 好友列表\n"
                    "  🦌友请求         → 待处理请求\n"
                    "成为 🦌友 后可用 帮🦌 @好友 互相帮助打卡")
                return

            # 🦌表
            if cmd.startswith("表") or cmd.startswith("tab"):
                data = _load(uid, gid)
                y, m = _parse_ym(cmd[1:].strip() if len(cmd)>1 else None)
                if _HAS_PIL:
                    png = _table_image(data, y, m, nickname, uid=uid, gid=gid)
                    if png:
                        await _send(event, "", png)
                        return
                await _send(event, _table_text(data, y, m))
                return

            # 🦌统计
            if cmd.startswith("统计") or cmd.startswith("stats") or cmd.startswith("sta"):
                data = _load(uid, gid)
                y, m = _parse_ym(cmd[2:].strip() if len(cmd)>2 else None)
                await _send(event, _stats_text(data, y, m))
                return

            # 🦌撤销
            if cmd.startswith("撤销") or cmd.startswith("undo"):
                data = _load(uid, gid)
                y, m, d = date.today().year, date.today().month, date.today().day
                rec = _ensure(data, y, m)
                dk = str(d)
                if dk not in rec or rec[dk].get("🦌", 0) == 0:
                    await _send(event, "⚠ 今天还没有 🦌 打卡")
                    return
                rec[dk]["🦌"] -= 1
                if rec[dk]["🦌"] <= 0: del rec[dk]["🦌"]
                if not rec[dk]: del rec[dk]
                _save(uid, gid, data)
                rem = rec.get(dk, {}).get("🦌", 0)
                await _send(event, f"↩ 已撤销，今日 🦌 还剩 {rem} 次" if rem else "↩ 已撤销今日全部 🦌")
                return

            # 🦌打卡（30分钟冷却）
            data = _load(uid, gid)
            now_ts = time.time()
            if now_ts - data.get("last_checkin", 0) < 30 * 60:
                return
            y, m, d = date.today().year, date.today().month, date.today().day
            rec = _ensure(data, y, m)
            dk = str(d)
            if dk not in rec: rec[dk] = {}
            rec[dk]["🦌"] = rec[dk].get("🦌", 0) + 1
            cnt = rec[dk]["🦌"]
            data["last_checkin"] = now_ts
            _save(uid, gid, data)
            # 发打卡成功 + 统计图（同一条消息）
            png = _table_image(data, y, m, nickname, uid=uid, gid=gid) if _HAS_PIL else None
            name = group_name or "魔王"
            await _send(event, f"{name}成功🦌了{cnt}次🥵", png)

        except Exception as e:
            logger.error(f"🦌 handle 异常: {e}")
