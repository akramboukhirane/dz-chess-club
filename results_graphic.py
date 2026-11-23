#!/usr/bin/env python3
# DZ Chess Club - Results_Graphic V1.3 (2026-11-23)
# V1.3: Arabic month names are the Algerian ones (confirmed by Akram). Nothing else changed from V1.2.
# V1.1 -> V1.2: command-line interface (for the GitHub Action); season top 3 read from the public Leaderboard CSV;
#   next cup derived from the date (+7 days, 22:00 Algeria time); BOTH squares now show the season top 3 (like the story);
#   optional --tourney (Lichess API) fills the top 3 names and the date; logo can be PNG or WebP;
#   fonts and icons are read from ./node_modules next to this script; fixed output names; long usernames shrink to fit.
#
# Makes 3 images per cup: Story 9:16 Arabic (Facebook story), Square Arabic (Facebook), Square English (Reddit).
#
# SETUP (repo root holds: results_graphic.py, logo.png, node_modules/):
#   npm install --no-save @fontsource/cairo@5.3.0 @fontsource/playfair-display@5.3.0 simple-icons@16.37.0
#   pip install playwright==1.64.0 && playwright install --with-deps chromium
# RUN:
#   python3 results_graphic.py --num 26 --date 2026-11-27 --p1 NAME1 --p2 NAME2 --p3 NAME3 --logo logo.png --out out
#   optional: --pts1 --pts2 --pts3 (default 10/5/3)   --tourney <lichess id or URL> (fills p1-p3 and date; flags override it)
#             --next-num N (default num+1)   --leaderboard <csv url or file> (default: the published Leaderboard CSV)
#             --season "A=46,B=33,C=31" (manual season points, skips the CSV)   --dry-run (print the data, no render)
# OUTPUT: out/cup<N>_story_ar.png, out/cup<N>_square_ar.png, out/cup<N>_square_en.png
# NOTE: update the Log and the Leaderboard tab BEFORE running: the season ranking comes from the published CSV
#       (Google refreshes a published CSV every few minutes).

import argparse, base64, csv, datetime as dt, html, io, json, os, random, re, subprocess, sys, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NM = Path(os.environ.get('DZ_NODE_MODULES', ROOT / 'node_modules'))
LEADERBOARD_CSV = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSr5GihkKzDhKspZA9izIzjlXy_O3AE51xjJx7DnrJbSyDlFslA1SwjDRUeM6lqwpDYskCzAUua_hNE/pub?gid=233985793&single=true&output=csv"
UA = 'DZChessClub-ResultsGraphic/1.3 (club results graphic)'
ALGERIA = dt.timezone(dt.timedelta(hours=1))  # Algeria is UTC+1 all year

MON_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
DAY_EN = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
# Arabic month names: Algerian usage (confirmed by Akram 2026-11-23).
MON_AR = ['جانفي', 'فيفري', 'مارس', 'أفريل', 'ماي', 'جوان', 'جويلية', 'أوت', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر']
DAY_AR = ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد']


def die(msg, code=2):
    print('ERROR: ' + msg, file=sys.stderr)
    sys.exit(code)


def warn(msg):
    print('WARNING: ' + msg, file=sys.stderr)


def esc(s):
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------- network
def http_get(url, accept='*/*', timeout=20):
    req = urllib.request.Request(url, headers={'Accept': accept, 'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8-sig')
    except urllib.error.HTTPError as e:
        hint = ' (rate limit: wait a few minutes and run again)' if e.code == 429 else ''
        die(f'HTTP {e.code} while fetching {url.split("?")[0]}{hint}')
    except Exception as e:
        die(f'could not fetch {url.split("?")[0]}: {e}')


def lichess_id(s):
    s = s.strip()
    m = re.search(r'lichess\.org/tournament/([A-Za-z0-9]{8})', s) or re.fullmatch(r'([A-Za-z0-9]{8})', s)
    if not m:
        die('--tourney must be a Lichess tournament id (8 characters) or a lichess.org/tournament/<id> link. Never pass the entry code.')
    return m.group(1)


def lichess_top3(tid):
    txt = http_get(f'https://lichess.org/api/tournament/{tid}/results?nb=3', 'application/x-ndjson')
    rows = [json.loads(l) for l in txt.splitlines() if l.strip()]
    rows.sort(key=lambda r: r.get('rank', 99))
    if len(rows) < 3 or not all(r.get('username') for r in rows[:3]):
        die(f'Lichess returned fewer than 3 players for tournament {tid} (not finished yet?). Pass --p1 --p2 --p3 by hand.')
    return [r['username'] for r in rows[:3]]


def lichess_date(tid):
    info = json.loads(http_get(f'https://lichess.org/api/tournament/{tid}', 'application/json'))
    if info.get('isFinished') is False:
        die(f'Lichess says tournament {tid} is not finished yet.')
    s = info.get('startsAt')
    if s is None:
        die('Lichess did not return the start date (field "startsAt"). Pass --date YYYY-MM-DD.')
    try:
        if isinstance(s, (int, float)):
            t = dt.datetime.fromtimestamp(s / 1000, dt.timezone.utc)
        else:
            t = dt.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
        return t.astimezone(ALGERIA).date()
    except Exception:
        die(f'could not read the Lichess start date "{s}". Pass --date YYYY-MM-DD.')


# ---------------------------------------------------------------- season ranking
def fmt_pts(p):
    return int(p) if float(p).is_integer() else p


def load_leaderboard(src):
    txt = http_get(src, 'text/csv') if re.match(r'https?://', src) else Path(src).read_text(encoding='utf-8-sig')
    rd = csv.DictReader(io.StringIO(txt))
    if not rd.fieldnames or 'Player' not in rd.fieldnames or 'Total Points' not in rd.fieldnames:
        die('the Leaderboard CSV has no "Player" / "Total Points" columns (headers must not be renamed, rule R5).')
    players, typed = [], {}
    for r in rd:
        n = (r.get('Player') or '').strip()
        if not n:
            continue
        try:
            p = float(str(r.get('Total Points')).strip())
        except ValueError:
            continue
        players.append((n, p))
        try:
            typed[n] = int(float(str(r.get('Rank')).strip()))
        except ValueError:
            pass
    if not players:
        die('the Leaderboard CSV has no players.')
    return players, typed


def season_groups(players, typed=None):
    """Competition ranking from points (ties share a rank, next rank skips). Returns [(rank, [names], pts)] for ranks 1-3."""
    by = {}
    for n, p in players:
        rank = 1 + sum(1 for _, q in players if q > p)
        by.setdefault(rank, ([], p))[0].append(n)
    out = [(rk, sorted(ns, key=str.lower), p) for rk, (ns, p) in sorted(by.items()) if rk <= 3]
    if typed:
        for rk, ns, _ in out:
            for n in ns:
                if n in typed and typed[n] != rk:
                    warn(f'typed Rank of {n} is {typed[n]} but its points give rank {rk}: using the points.')
    return [(rk, ns, fmt_pts(p)) for rk, ns, p in out]


def parse_season(s):
    players = []
    for part in s.split(','):
        if not part.strip():
            continue
        if '=' not in part:
            die('--season must look like "NameA=46,NameB=33,NameC=31".')
        n, p = part.rsplit('=', 1)
        try:
            players.append((n.strip(), float(p)))
        except ValueError:
            die(f'--season: "{p}" is not a number.')
    if not players:
        die('--season is empty.')
    return players


# ---------------------------------------------------------------- data
def fdate_en(d):
    return f'{d.day} {MON_EN[d.month - 1].upper()} {d.year}'


def fdate_ar(d):
    return f'{d.day} {MON_AR[d.month - 1]} {d.year}'


def make_data(a):
    for k in ('p1', 'p2', 'p3', 'date', 'tourney'):
        v = getattr(a, k)
        setattr(a, k, (v or '').strip() or None)
    names = [a.p1, a.p2, a.p3]
    date = None
    if a.date:
        try:
            date = dt.date.fromisoformat(a.date)
        except ValueError:
            die('--date must look like 2026-11-27.')
    if a.tourney:
        tid = lichess_id(a.tourney)
        if not all(names):
            fetched = lichess_top3(tid)
            names = [n or f for n, f in zip(names, fetched)]
        if not date:
            date = lichess_date(tid)
    if not all(names):
        die('the top 3 is missing: pass --p1 --p2 --p3 (or --tourney).')
    if not date:
        die('the date is missing: pass --date YYYY-MM-DD (or --tourney).')
    if date.weekday() != 4:
        warn(f'{date} is a {DAY_EN[date.weekday()]}, not a Friday: check --date.')
    pts = [a.pts1 if a.pts1 is not None else 10, a.pts2 if a.pts2 is not None else 5, a.pts3 if a.pts3 is not None else 3]

    if a.season:
        players, typed = parse_season(a.season), None
    else:
        players, typed = load_leaderboard(a.leaderboard)
    season = season_groups(players, typed)
    known = {n.lower() for n, _ in players}
    for n in names:
        if n.lower() not in known:
            warn(f'{n} is not on the Leaderboard: the CSV may not be refreshed yet (or the winner is not eligible, rule R4).')

    nd = date + dt.timedelta(days=7)
    nxt = {'num': a.next_num or a.num + 1,
           'day': f'{DAY_EN[nd.weekday()]} {nd.day} {MON_EN[nd.month - 1]}',
           'day_ar': f'{DAY_AR[nd.weekday()]} {nd.day} {MON_AR[nd.month - 1]}',
           'time': '22:00'}
    return {'num': a.num, 'date': fdate_en(date), 'date_ar': fdate_ar(date),
            'top3': list(zip(names, pts)), 'season': season, 'next': nxt}


# ---------------------------------------------------------------- assets
def b64(p):
    return base64.b64encode(Path(p).read_bytes()).decode()


def logo_uri(path):
    p = Path(path)
    if not p.exists() and (ROOT / path).exists():
        p = ROOT / path
    if not p.exists():
        die(f'logo not found: {path}')
    raw = p.read_bytes()
    if raw[:8] == b'\x89PNG\r\n\x1a\n':
        mime = 'image/png'
    elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        mime = 'image/webp'
    elif raw[:3] == b'\xff\xd8\xff':
        mime = 'image/jpeg'
    else:
        die('the logo must be a PNG or WebP file.')
    return f'data:{mime};base64,' + base64.b64encode(raw).decode()


_ASSETS = {}


def assets():
    if _ASSETS:
        return _ASSETS
    if not NM.exists():
        die(f'node_modules not found at {NM}. Run: npm install --no-save @fontsource/cairo@5.3.0 @fontsource/playfair-display@5.3.0 simple-icons@16.37.0')

    def font(fam, w, file, style='normal'):
        f = NM / '@fontsource' / file
        if not f.exists():
            die(f'font file missing: {f}')
        return f"@font-face{{font-family:'{fam}';font-weight:{w};font-style:{style};src:url(data:font/woff2;base64,{b64(f)}) format('woff2');}}"

    fonts = ''.join([
        font('CairoA', 800, 'cairo/files/cairo-arabic-800-normal.woff2'),
        font('CairoL', 600, 'cairo/files/cairo-latin-600-normal.woff2'),
        font('CairoL', 800, 'cairo/files/cairo-latin-800-normal.woff2'),
        font('PF', 700, 'playfair-display/files/playfair-display-latin-700-normal.woff2'),
        font('PF', 800, 'playfair-display/files/playfair-display-latin-800-normal.woff2'),
        font('PF', 700, 'playfair-display/files/playfair-display-latin-700-italic.woff2', 'italic')])
    js = "const s=require('simple-icons');console.log(JSON.stringify({fb:s.siFacebook.path,wa:s.siWhatsapp.path,rd:s.siReddit.path}))"
    try:
        ic = json.loads(subprocess.check_output(['node', '-e', js], cwd=str(NM.parent)))
    except Exception as e:
        die(f'could not read the social icons from simple-icons: {e}')
    soc = [('Facebook', '#0866FF', f'<path fill="#fff" d="{ic["fb"]}"/>'),
           ('WhatsApp', '#25D366', f'<path fill="#fff" d="{ic["wa"]}"/>'),
           ('Reddit', '#FF4500', f'<path fill="#fff" d="{ic["rd"]}"/>')]
    _ASSETS.update(fonts=fonts, soc=soc)
    return _ASSETS


# ---------------------------------------------------------------- drawing
CROWN = '''<svg viewBox="0 0 120 80" width="{w}" class="crown"><defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F6DC9A"/><stop offset="1" stop-color="#B98A38"/></linearGradient></defs>
<path d="M10 66 L2 18 L34 40 L60 6 L86 40 L118 18 L110 66 Z" fill="url(#cg)"/><rect x="10" y="68" width="100" height="10" rx="3" fill="url(#cg)"/>
<circle cx="2" cy="16" r="6" fill="#F6DC9A"/><circle cx="60" cy="6" r="7" fill="#F6DC9A"/><circle cx="118" cy="16" r="6" fill="#F6DC9A"/></svg>'''


def snow(W, H, n, seed):
    r = random.Random(seed)
    out = []
    for _ in range(n):
        x = r.uniform(0, W)
        y = r.uniform(0, H)
        s = r.choice([3, 3, 4, 5, 6, 8, 11])
        o = r.uniform(.25, .8) if s < 8 else r.uniform(.12, .3)
        bl = 0 if s < 6 else (1.5 if s < 9 else 4)
        out.append(f'<i style="left:{x:.0f}px;top:{y:.0f}px;width:{s}px;height:{s}px;opacity:{o:.2f};filter:blur({bl}px)"></i>')
    return ''.join(out)


TXT = {
    'ar': dict(h='النتائج النهائية', pts=lambda n: f"{n} {'نقاط' if n <= 10 else 'نقطة'}", ranking='الترتيب العام',
               nextc='البطولة القادمة', tz='بتوقيت الجزائر · Lichess'),
    'en': dict(h='Final Results', pts=lambda n: f'{n} pts', ranking='Season ranking',
               nextc='Next cup', tz='Algeria time · Lichess')}


def fit(names, width, base, floor=18):
    """Font size that keeps the longest name inside `width` (Playfair Display bold is about 0.62em per character)."""
    longest = max(len(n) for n in names)
    return max(floor, min(base, int(width / (0.62 * longest))))


def socials(story):
    sz = 56 if story else 60
    cells = ''.join(
        f'<div class="soc"><div class="ico" style="background:{c};width:{sz}px;height:{sz}px"><svg viewBox="0 0 24 24" width="{int(sz * .54)}" height="{int(sz * .54)}">{g}</svg></div><span>{n}</span></div>'
        for n, c, g in assets()['soc'])
    return f'<div class="socs">{cells}</div>'


def build(fmt, d, logo, lang='en'):
    AR = lang == 'ar'
    T = TXT[lang]
    S = fmt == 'story'
    W, H = 1080, (1920 if S else 1080)
    (n1, p1), (n2, p2), (n3, p3) = d['top3']
    if S:
        L = dict(logo=140, logo_y=256, wm_y=406, h_y=436, h_s=104, s_y=560, s_s=46, date_y=620, pod_b=1102, b1=190, b2=140, b3=105, nm1=60, nm2=44,
                 card1_y=1140, card1_h=196, card2_y=1350, card2_h=124, soc_y=1492, floor_top=820, floor_h=520, side=66)
    else:
        L = dict(logo=136, logo_y=40, pod_b=630, b1=190, b2=140, b3=105, nm1=50, nm2=38,
                 card1_y=656, card1_h=162, card2_y=830, card2_h=92, soc_y=946, floor_top=420, floor_h=300, side=52)
    colw = 292 if S else 290

    def col(rank, name, pts, bh, ns, cls, w):
        ns = fit([name], w - 16, ns, 22)
        crown = CROWN.format(w=84 if S else 62) if rank == 1 else ''
        return (f'<div class="col {cls}" style="width:{w}px">{crown}<div class="name" style="font-size:{ns}px">{esc(name)}</div>'
                f'<div class="pts">{T["pts"](pts)}</div><div class="block" style="height:{bh}px"><span class="cap"></span><b>{rank}</b></div></div>')

    pod = (col(2, n2, p2, L['b2'], L['nm2'], 'silver', colw) + col(1, n1, p1, L['b1'], L['nm1'], 'gold', colw + 34) + col(3, n3, p3, L['b3'], L['nm2'], 'bronze', colw))
    nx = d['next']
    dt_txt = d['date_ar'] if AR else d['date']
    day_txt = nx['day_ar'] if AR else nx['day']

    def logo_img(pos):
        return f'<img class="logo" src="{logo}" style="width:{L["logo"]}px;top:{L["logo_y"]}px;{pos}">'

    def next_card(st):
        return (f'<div class="card green" style="{st}"><div class="nx"><div class="lab">{T["nextc"]} #{nx["num"]}</div><b>{day_txt}</b></div>'
                f'<div class="nx2">{nx["time"]}<small>{T["tz"]}</small></div></div>')

    HF = '' if AR else 'font-family:CairoL;'  # English headline uses Cairo Latin
    if S:
        head = logo_img(f'left:{(W - L["logo"]) // 2}px') + f"""
<div class="wmc" style="top:{L['wm_y']}px">DZ CHESS CLUB</div>
<div class="h" style="{HF}top:{L['h_y']}px;font-size:{L['h_s']}px;left:0;right:0;text-align:center">{T['h']}</div>
<div class="sub" style="top:{L['s_y']}px;font-size:{L['s_s']}px;left:0;right:0;text-align:center">Weekly Rapid Cup #{d['num']}</div>
<div class="dt" style="top:{L['date_y']}px;left:0;right:0;text-align:center"><bdi class="dd">{dt_txt}</bdi></div>"""
    else:
        side = 'right' if AR else 'left'  # text side; logo goes on the opposite side
        oth = 'left' if AR else 'right'
        head = logo_img(f'{oth}:52px') + f"""
<div class="h" style="{HF}top:{26 if AR else 40}px;{side}:52px;font-size:{92 if AR else 84}px;text-align:{side}">{T['h']}</div>
<div class="sub" style="top:146px;{side}:52px;font-size:40px;text-align:{side}">Weekly Rapid Cup #{d['num']}</div>
<div class="dt" style="top:198px;{side}:52px;text-align:{side}"><bdi>DZ CHESS CLUB</bdi> · <bdi class="dd">{dt_txt}</bdi></div>"""

    colwidth = (W - 2 * L['side'] - 52 - 24) // 3
    base = 30 if S else 28
    cols = ''
    for r, ns, p in d['season']:
        shown = ns if len(ns) <= 3 else ns[:2] + [f'+{len(ns) - 2}']
        fs = fit(shown, colwidth, base, 16)
        cols += (f'<div class="sc"><em>{r}</em><div style="font-size:{fs}px">{"<br>".join(esc(n) for n in shown)}</div><u>{T["pts"](p)}</u></div>')
    cards = (f'<div class="card dark" style="top:{L["card1_y"]}px;height:{L["card1_h"]}px;left:{L["side"]}px;right:{L["side"]}px">'
             f'<div class="lab">{T["ranking"]}</div><div class="scs">{cols}</div></div>'
             + next_card(f'top:{L["card2_y"]}px;height:{L["card2_h"]}px;left:{L["side"]}px;right:{L["side"]}px'))
    soc_lr = 150 if S else 190
    soc = f'<div style="position:absolute;top:{L["soc_y"]}px;left:{soc_lr}px;right:{soc_lr}px">{socials(S)}</div>'

    css = f'''{assets()['fonts']}
*{{box-sizing:border-box;margin:0;padding:0}}
html{{overflow:hidden;width:{W}px;height:{H}px}}
body{{width:{W}px;height:{H}px;overflow:hidden;background:#0b121b;position:relative;font-family:'CairoL','CairoA',sans-serif;color:#EFE6D8;font-variant-numeric:lining-nums;direction:{'rtl' if AR else 'ltr'}}}
.dd{{letter-spacing:{'0' if AR else '.22em'};font-family:'CairoL','CairoA'}}
.bg{{position:absolute;inset:0;background:radial-gradient(900px 520px at 50% 0%,rgba(140,190,225,.22),transparent 70%),radial-gradient(700px 500px at 50% 100%,rgba(15,74,50,.30),transparent 70%),linear-gradient(180deg,#121d2c 0%,#0c141f 55%,#0a0f17 100%)}}
.floor{{position:absolute;left:-40%;right:-40%;top:{L['floor_top']}px;height:{L['floor_h']}px;perspective:900px;perspective-origin:50% 0;-webkit-mask-image:linear-gradient(180deg,transparent 0%,#000 30%,#000 60%,transparent 100%)}}
.floor div{{position:absolute;inset:0;transform:rotateX(62deg);transform-origin:50% 0;background:conic-gradient(#243a54 25%,#0e1825 0 50%,#243a54 0 75%,#0e1825 0);background-size:160px 160px;opacity:.9}}
.snow i{{position:absolute;border-radius:50%;background:#fff}}
.logo{{position:absolute;filter:drop-shadow(0 8px 22px rgba(0,0,0,.55))}}
.wmc{{position:absolute;left:0;right:0;text-align:center;font-weight:800;font-size:28px;letter-spacing:.34em;color:#D4AF63;padding-left:.34em}}
.h{{position:absolute;font-family:'CairoA';font-weight:800;line-height:1.25;background:linear-gradient(180deg,#FFF3CF,#E2BD6E 60%,#B98A38);-webkit-background-clip:text;color:transparent}}
.sub{{position:absolute;font-family:'PF';font-style:italic;font-weight:700;color:#EFE6D8}}
.dt{{position:absolute;font-weight:800;font-size:{26 if S else 21}px;letter-spacing:.22em;color:#D4AF63}}
.pod{{direction:ltr;position:absolute;left:0;right:0;top:{L['pod_b']}px;height:0;display:flex;justify-content:center;align-items:flex-end;gap:22px}}
.col{{display:flex;flex-direction:column;align-items:center;text-align:center}}
.crown{{margin-bottom:8px;filter:drop-shadow(0 6px 14px rgba(212,175,99,.45))}}
.name{{font-family:'PF';font-weight:700;color:#fff;white-space:nowrap;text-shadow:0 2px 14px rgba(0,0,0,.5);margin-bottom:10px}}
.pts{{direction:{'rtl' if AR else 'ltr'};font-weight:800;font-size:{30 if S else 25}px;padding:3px 22px 5px;border-radius:40px;margin-bottom:16px;border:2px solid currentColor;letter-spacing:.04em;background:rgba(10,15,23,.55)}}
.block{{width:100%;position:relative;border-radius:10px 10px 0 0;display:flex;align-items:center;justify-content:center;box-shadow:0 24px 50px rgba(0,0,0,.5)}}
.block:after{{content:'';position:absolute;inset:0;border-radius:inherit;background:linear-gradient(90deg,rgba(255,255,255,.22),rgba(255,255,255,0) 30%,rgba(0,0,0,0) 70%,rgba(0,0,0,.28))}}
.block b{{position:relative;z-index:1;font-family:'PF';font-weight:800;font-size:{120 if S else 110}px;line-height:1;opacity:.92}}
.cap{{position:absolute;left:-8px;right:-8px;top:-16px;height:34px;border-radius:50%;background:radial-gradient(ellipse at 50% 40%,#fff,#DCE9F4 60%,rgba(220,233,244,0) 100%);filter:blur(1px);z-index:2}}
.gold .block{{background:linear-gradient(160deg,#F6DC9A,#D4AF63 45%,#9B7027);color:#6b4a14}} .gold .pts{{color:#F0D08A}}
.silver .block{{background:linear-gradient(160deg,#F1F4F8,#BFC8D4 45%,#7C8899);color:#4a5566}} .silver .pts{{color:#D5DCE6}}
.bronze .block{{background:linear-gradient(160deg,#EDB988,#C8854F 45%,#82491F);color:#5e3412}} .bronze .pts{{color:#E7AC78}}
.card{{position:absolute;border-radius:18px;padding:{'16px 26px' if S else '10px 26px'};display:flex;align-items:center;justify-content:space-between;border:1.5px solid rgba(212,175,99,.55);box-shadow:0 14px 34px rgba(0,0,0,.4)}}
.card.dark{{background:linear-gradient(160deg,rgba(30,44,62,.92),rgba(14,22,33,.95));flex-direction:column;align-items:stretch;justify-content:center}}
.card.green{{background:linear-gradient(160deg,#145a3d,#0b3624)}}
.lab{{font-weight:800;font-size:{24 if S else 19}px;letter-spacing:{'0' if AR else '.14em'};color:#D4AF63;margin-bottom:{8 if S else 2}px;white-space:nowrap}}
.scs{{display:flex;justify-content:space-between;gap:12px}}
.sc{{display:flex;flex-direction:column;align-items:center;text-align:center;flex:1;line-height:1.15}}
.sc em{{font-style:normal;font-weight:800;color:#D4AF63;font-size:{22 if S else 20}px}} .sc div{{font-family:'PF';font-weight:700;color:#fff;min-height:{68 if S else 64}px;display:flex;flex-direction:column;justify-content:center;white-space:nowrap}}
.sc u{{text-decoration:none;font-weight:800;font-size:{22 if S else 20}px;color:#F0D08A}}
.nx b{{display:block;font-weight:800;font-size:{36 if S else 31}px;color:#fff;white-space:nowrap}}
.nx2{{font-weight:800;font-size:{44 if S else 38}px;color:#F0D08A;text-align:end;line-height:1}}
.nx2 small{{display:block;font-weight:600;font-size:{19 if S else 16}px;color:#CFE3D8;margin-top:4px;white-space:nowrap}}
.socs{{display:flex;justify-content:space-between}}
.soc{{display:flex;flex-direction:column;align-items:center;gap:8px;flex:1}}
.ico{{border-radius:50%;display:flex;align-items:center;justify-content:center;border:2px solid rgba(239,230,216,.85);box-shadow:0 8px 18px rgba(0,0,0,.4)}}
.soc span{{font-weight:600;font-size:{18 if S else 20}px;color:#EFE6D8;white-space:nowrap}}'''
    page = (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>{css}</style></head><body><div class="bg"></div>'
            f'<div class="floor"><div></div></div><div class="snow">{snow(W, H, 150 if S else 100, 7 if S else 3)}</div>{head}<div class="pod">{pod}</div>{cards}{soc}</body></html>')
    return page, W, H


JOBS = (('story', 'ar'), ('square', 'ar'), ('square', 'en'))


def render(data, logo_path, out_dir, jobs=JOBS):
    from playwright.sync_api import sync_playwright
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    logo = logo_uri(logo_path)
    files = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=['--no-sandbox'])
        for fmt, lang in jobs:
            h, W, H = build(fmt, data, logo, lang)
            pg = b.new_page(viewport={'width': W, 'height': H})
            pg.set_content(h)
            pg.evaluate('document.fonts.ready')
            pg.wait_for_timeout(700)
            f = out / f'cup{data["num"]}_{fmt}_{lang}.png'
            pg.screenshot(path=str(f))
            pg.close()
            files.append(f)
        b.close()
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        die('render failed, missing: ' + ', '.join(missing), 3)
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description='DZ Chess Club results graphics (V1.3)')
    ap.add_argument('--num', type=int, required=True, help='cup number')
    ap.add_argument('--date', help='cup date YYYY-MM-DD (Friday, Algeria time)')
    ap.add_argument('--p1'); ap.add_argument('--p2'); ap.add_argument('--p3')
    ap.add_argument('--pts1', type=int); ap.add_argument('--pts2', type=int); ap.add_argument('--pts3', type=int)
    ap.add_argument('--logo', default='logo.png')
    ap.add_argument('--out', default='out')
    ap.add_argument('--tourney', help='Lichess tournament id or link (fills p1-p3 and date; never pass the entry code)')
    ap.add_argument('--next-num', dest='next_num', type=int)
    ap.add_argument('--leaderboard', default=LEADERBOARD_CSV, help='Leaderboard CSV url or file')
    ap.add_argument('--season', help='manual season points "A=46,B=33,C=31" (skips the CSV)')
    ap.add_argument('--dry-run', action='store_true', help='print the data and stop')
    a = ap.parse_args(argv)
    data = make_data(a)
    print(json.dumps(data, ensure_ascii=False, indent=1))
    if a.dry_run:
        return
    files = render(data, a.logo, a.out)
    print('OK: ' + ', '.join(str(f) for f in files))


if __name__ == '__main__':
    main()
