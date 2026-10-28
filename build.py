#!/usr/bin/env python3
"""Builds Dan's Formula 1 calendar.

Writes two files into ./site, which GitHub Pages publishes:
  f1.ics   the calendar Apple Calendar and Home Assistant subscribe to
           (Grands Prix, Sprints and pre-season testing only; no alerts; shown as Free;
           each event in the circuit's own time zone)
  f1.json  the rich details the Home Assistant pop-up draws from
           (flags, round progress, last year's podium with headshots, standings, title watch, changes)

Where the data comes from (all free, no keys):
  Jolpica (api.jolpi.ca)  the official schedule, results and standings (the successor to Ergast)
  OpenF1 (api.openf1.org) driver headshots and team colours, and testing days once F1 publishes them
  testing.json            testing days typed in by hand, used until OpenF1 has them
  flagcdn.com             flag images (linked, not copied)

Cancelled rounds simply drop out, because they drop out of the official schedule; round numbers
follow it. If the schedule can't be fetched, or looks badly wrong, the build stops and the
previous calendar stays live.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from timezonefinder import TimezoneFinder
    TF = TimezoneFinder()
except Exception:  # pragma: no cover - only if the package is missing
    TF = None

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
DATA = ROOT / "data"
JOLPICA = os.environ.get("JOLPICA_URL", "https://api.jolpi.ca/ergast/f1")
OPENF1 = os.environ.get("OPENF1_URL", "https://api.openf1.org/v1")
PAGES_URL = os.environ.get("PAGES_URL", "https://ketteringham.github.io/f1-calendar")
UID_DOMAIN = "f1-calendar.ketteringham.github.io"
UK = ZoneInfo("Europe/London")
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()

RACE_HOURS = 2
SPRINT_HOURS = 1
RACE_POINTS = 25
SPRINT_POINTS = 8
CHANGE_DAYS = 21  # how long a "start time changed" banner stays

# ------------------------------------------------------------------ fetching

AGENT = "f1-calendar (github.com/ketteringham/f1-calendar)"
PAUSE = float(os.environ.get("REQUEST_PAUSE", "0.35"))


def get(url: str, params: dict | None = None, tries: int = 4):
    """GET JSON politely (Jolpica allows a few requests a second). None on 404."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
            time.sleep(PAUSE)
            return json.loads(body)
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if err.code == 429 or err.code >= 500:
                time.sleep(8 * (attempt + 1))
                continue
            raise RuntimeError(f"Couldn't fetch {url}: HTTP {err.code}") from err
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            if attempt == tries - 1:
                raise RuntimeError(f"Couldn't fetch {url}: {err}") from err
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"Gave up on {url}")


def soft(fn, *args, default=None):
    """For the extras (headshots, podiums): a failure leaves a gap, never stops the build."""
    try:
        return fn(*args)
    except Exception as err:  # noqa: BLE001
        print(f"  note: {fn.__name__}{args} failed: {err}", file=sys.stderr)
        return default


def load(name: str, default):
    p = DATA / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return default


def save(name: str, value) -> None:
    DATA.mkdir(exist_ok=True)
    (DATA / name).write_text(json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


CACHE = load("cache.json", {})  # past results, first races, headshots: things that don't change

# ------------------------------------------------------------------ countries, flags, colours

COUNTRY = {
    "argentina": "ar", "australia": "au", "austria": "at", "azerbaijan": "az", "bahrain": "bh", "belgium": "be",
    "brazil": "br", "canada": "ca", "china": "cn", "france": "fr", "germany": "de", "hungary": "hu", "india": "in",
    "italy": "it", "japan": "jp", "korea": "kr", "south korea": "kr", "malaysia": "my", "mexico": "mx",
    "monaco": "mc", "morocco": "ma", "netherlands": "nl", "portugal": "pt", "qatar": "qa", "russia": "ru",
    "rwanda": "rw", "saudi arabia": "sa", "singapore": "sg", "south africa": "za", "spain": "es", "sweden": "se",
    "switzerland": "ch", "thailand": "th", "turkey": "tr", "türkiye": "tr", "turkiye": "tr", "uae": "ae",
    "united arab emirates": "ae", "uk": "gb", "united kingdom": "gb", "great britain": "gb", "usa": "us",
    "united states": "us", "vietnam": "vn",
}
OPENF1_COUNTRY = {"BRN": "bh", "ESP": "es", "UAE": "ae", "ARE": "ae", "GBR": "gb", "USA": "us", "QAT": "qa", "SAU": "sa"}

TEAM_COLOUR = {  # used only when OpenF1 hasn't given a colour
    "mercedes": "#00D7B6", "ferrari": "#ED1131", "mclaren": "#F47600", "red_bull": "#4781D7",
    "aston_martin": "#229971", "alpine": "#00A1E8", "williams": "#1868DB", "rb": "#6C98FF",
    "sauber": "#01C00E", "audi": "#F50537", "haas": "#9C9FA2", "cadillac": "#909090",
}

# fallback time zones for circuits, used only if the coordinates lookup isn't available
TZ_FALLBACK = {
    "bahrain": "Asia/Bahrain", "jeddah": "Asia/Riyadh", "albert_park": "Australia/Melbourne",
    "suzuka": "Asia/Tokyo", "shanghai": "Asia/Shanghai", "miami": "America/New_York",
    "villeneuve": "America/Toronto", "monaco": "Europe/Monaco", "catalunya": "Europe/Madrid",
    "madring": "Europe/Madrid", "red_bull_ring": "Europe/Vienna", "silverstone": "Europe/London",
    "spa": "Europe/Brussels", "hungaroring": "Europe/Budapest", "zandvoort": "Europe/Amsterdam",
    "monza": "Europe/Rome", "imola": "Europe/Rome", "baku": "Asia/Baku", "marina_bay": "Asia/Singapore",
    "americas": "America/Chicago", "rodriguez": "America/Mexico_City", "interlagos": "America/Sao_Paulo",
    "vegas": "America/Los_Angeles", "losail": "Asia/Qatar", "yas_marina": "Asia/Dubai",
    "portimao": "Europe/Lisbon", "istanbul": "Europe/Istanbul",
}


TEST_VENUES = {  # where testing usually happens, for years when no Grand Prix there gives us the details
    "bh": {"id": "bahrain", "name": "Bahrain International Circuit", "locality": "Sakhir", "country": "Bahrain",
           "country_code": "bh", "lat": 26.0325, "long": 50.5106, "tz": "Asia/Bahrain"},
    "es": {"id": "catalunya", "name": "Circuit de Barcelona-Catalunya", "locality": "Montmeló", "country": "Spain",
           "country_code": "es", "lat": 41.57, "long": 2.26111, "tz": "Europe/Madrid"},
}


def iso2(country: str) -> str:
    return COUNTRY.get((country or "").strip().lower(), "")


def flag_emoji(cc: str) -> str:
    return "".join(chr(0x1F1E6 + ord(c) - 97) for c in cc) if len(cc) == 2 else ""


COUNTRY_TZ = {  # countries with one time zone
    "ae": "Asia/Dubai", "at": "Europe/Vienna", "az": "Asia/Baku", "be": "Europe/Brussels", "bh": "Asia/Bahrain",
    "cn": "Asia/Shanghai", "de": "Europe/Berlin", "es": "Europe/Madrid", "fr": "Europe/Paris", "gb": "Europe/London",
    "hu": "Europe/Budapest", "in": "Asia/Kolkata", "it": "Europe/Rome", "jp": "Asia/Tokyo", "kr": "Asia/Seoul",
    "ma": "Africa/Casablanca", "mc": "Europe/Monaco", "my": "Asia/Kuala_Lumpur", "nl": "Europe/Amsterdam",
    "pt": "Europe/Lisbon", "qa": "Asia/Qatar", "rw": "Africa/Kigali", "sa": "Asia/Riyadh", "sg": "Asia/Singapore",
    "th": "Asia/Bangkok", "tr": "Europe/Istanbul", "vn": "Asia/Ho_Chi_Minh", "za": "Africa/Johannesburg",
    "ar": "America/Argentina/Buenos_Aires",
}


def tz_for(lat: float, lon: float, circuit_id: str = "", cc: str = "") -> str:
    if TF is not None:
        try:
            name = TF.timezone_at(lat=lat, lng=lon)
            if name:
                return name
        except Exception:  # noqa: BLE001
            pass
    name = TZ_FALLBACK.get(circuit_id) or COUNTRY_TZ.get(cc)
    if not name:
        print(f"  note: no time zone known for {circuit_id or cc}; using UTC", file=sys.stderr)
    return name or "UTC"


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ------------------------------------------------------------------ times


def parse_when(date: str, time_: str | None):
    """Jolpica gives UTC date + time; time is missing while F1 hasn't confirmed it."""
    if time_:
        return dt.datetime.fromisoformat(f"{date}T{time_.replace('Z', '+00:00')}"), True
    return dt.date.fromisoformat(date), False


def uk_clock(d: dt.datetime) -> str:
    """'8pm', '12:30pm' in UK time."""
    t = d.astimezone(UK)
    h = t.hour % 12 or 12
    return f"{h}{':%02d' % t.minute if t.minute else ''}{'am' if t.hour < 12 else 'pm'}"


def local_clock(d: dt.datetime, tz: str) -> str:
    t = d.astimezone(ZoneInfo(tz))
    return f"{t.hour:02d}:{t.minute:02d}"


def uk_day(d) -> str:
    if isinstance(d, dt.datetime):
        d = d.astimezone(UK)
    return f"{d:%a} {d.day} {d:%b}"


# ------------------------------------------------------------------ headshots


def remember_drivers(rows: list[dict]) -> None:
    people = CACHE.setdefault("drivers", {})
    for d in rows or []:
        last = (d.get("last_name") or "").strip()
        first = (d.get("first_name") or "").strip()
        if not last:
            continue
        key = f"{first[:1]}.{last}".lower()
        entry = people.get(key, {})
        if d.get("headshot_url"):
            entry["headshot"] = d["headshot_url"]
        if d.get("team_colour"):
            entry["colour"] = "#" + d["team_colour"].lstrip("#").upper()
        if d.get("team_name"):
            entry["team"] = d["team_name"]
        entry["number"] = d.get("driver_number")
        people[key] = entry


def refresh_headshots() -> None:
    remember_drivers(get(f"{OPENF1}/drivers", {"session_key": "latest"}))
    seeded = CACHE.setdefault("drivers_seeded", [])
    for year in (TODAY.year - 1, TODAY.year):
        if year in seeded:
            continue
        sessions = get(f"{OPENF1}/sessions", {"year": year, "session_name": "Race"}) or []
        if sessions:
            remember_drivers(get(f"{OPENF1}/drivers", {"session_key": sessions[-1]["session_key"]}))
            if year < TODAY.year:
                seeded.append(year)


# ------------------------------------------------------------------ images, kept in the repository

IMG = ROOT / "img"
HEADSHOT_PX = 480  # longest side; sharp on a phone's podium tile, small to store


def fetch_bytes(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status != 200:
                return None
            return r.read()
    except Exception:  # noqa: BLE001
        return None


TEAM_SLUGS = {  # how F1's image library spells team names
    "red bull racing": "redbullracing", "red bull": "redbullracing", "racing bulls": "racingbulls", "rb": "racingbulls",
    "aston martin": "astonmartin", "kick sauber": "kicksauber", "sauber": "kicksauber", "haas f1 team": "haasf1team",
    "haas": "haasf1team",
}


def bigger_versions(url: str, team: str = "") -> list[str]:
    """F1's headshot links ask for a small size; try the larger ones first, then F1's newer image library
    (where some drivers only exist), then the original link."""
    out = []
    if ".transform/" in url:
        out.append(url.split(".transform/")[0])  # the original upload
        out += [re.sub(r"/\d+col/", f"/{n}col/", url) for n in (4, 3, 2)]
    if re.search(r"\bw_\d+", url):
        out.append(re.sub(r"\bw_\d+", "w_960", url))
    code = re.search(r"/([a-z]{6}\d{2})\.png", url)  # e.g. carsai01
    if code and team:
        t = TEAM_SLUGS.get(team.strip().lower(), re.sub(r"[^a-z0-9]", "", team.lower()))
        for year in (TODAY.year, TODAY.year - 1):
            out.append(f"https://media.formula1.com/image/upload/c_lfill,w_960/q_auto/v1740000000/common/f1/{year}/"
                       f"{t}/{code.group(1)}/{year}{t}{code.group(1)}right.webp")
    out.append(url)
    seen = []
    for u in out:
        if u not in seen:
            seen.append(u)
    return seen


def is_placeholder(im) -> bool:
    """F1 answers a missing photo with a plain grey figure. Real headshots are full of colour (faces, race suits)."""
    from PIL import ImageStat
    rgba = im.convert("RGBA")
    alpha = rgba.getchannel("A").point(lambda a: 255 if a > 200 else 0)
    if not alpha.getbbox():
        return True
    sat = rgba.convert("RGB").convert("HSV").getchannel("S")
    return ImageStat.Stat(sat, alpha).mean[0] < 14


def save_headshot(key: str, src: str, team: str = "") -> bool:
    """Download the best version of a headshot and store it as img/drivers/<key>.webp. If F1 only has its
    grey placeholder, store nothing (the card then shows its own team-coloured outline), and look again in a week."""
    dest = IMG / "drivers" / f"{slug(key)}.webp"
    meta = CACHE.setdefault("images", {})
    m = meta.get(key)
    if isinstance(m, str):  # older cache entries were just the source link
        m = {"src": m}
    if m and m.get("src") == src:
        if dest.exists() and not is_placeholder_file(dest):
            return True
        if m.get("missing") and (NOW - dt.datetime.fromisoformat(m["missing"])).days < 7:
            return False
    for url in bigger_versions(src, team):
        data = fetch_bytes(url)
        if not data or len(data) < 1000:
            continue
        try:
            from io import BytesIO

            from PIL import Image
            im = Image.open(BytesIO(data))
            im.load()
            if im.width < 120 or is_placeholder(im):  # tiny, or F1's grey stand-in: try the next one
                continue
            im = im.convert("RGBA")
            im.thumbnail((HEADSHOT_PX, HEADSHOT_PX), Image.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, "WEBP", quality=86, method=6)
        except Exception as err:  # noqa: BLE001
            print(f"  note: couldn't process headshot for {key}: {err}", file=sys.stderr)
            continue
        meta[key] = {"src": src, "from": url}
        return True
    if dest.exists() and is_placeholder_file(dest):
        dest.unlink()  # a placeholder stored by an earlier build
    meta[key] = {"src": src, "missing": NOW.isoformat()}
    print(f"  note: no real headshot for {key} yet", file=sys.stderr)
    return False


def is_placeholder_file(path) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
            return is_placeholder(im)
    except Exception:  # noqa: BLE001
        return False


def save_flag(cc: str) -> bool:
    dest = IMG / "flags" / f"{cc}.svg"
    if dest.exists():
        return True
    data = fetch_bytes(f"https://flagcdn.com/{cc}.svg")
    if not data or b"<svg" not in data[:2000]:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def headshot_url(key: str, fallback: str | None) -> str | None:
    if key and (IMG / "drivers" / f"{slug(key)}.webp").exists():
        return f"{PAGES_URL}/img/drivers/{slug(key)}.webp"
    return fallback


def known_driver(given: str, last: str) -> dict:
    """Headshot details for a driver. Data sources disagree on given names ("Andrea Kimi" vs "Kimi"),
    so try each given name, then the surname alone when only one driver has it."""
    people = CACHE.get("drivers", {})
    for tok in (given or "").split() or [""]:
        k = f"{tok[:1]}.{last}".lower()
        if k in people:
            return dict(people[k], headshot=headshot_url(k, people[k].get("headshot")))
    same = [k for k in people if k.split(".", 1)[-1] == (last or "").lower()]
    if len(same) == 1:
        k = same[0]
        return dict(people[k], headshot=headshot_url(k, people[k].get("headshot")))
    return {}


def person(driver: dict, constructor: dict | None) -> dict:
    first = driver.get("givenName", "")
    last = driver.get("familyName", "")
    known = known_driver(first, last)
    cid = (constructor or {}).get("constructorId", "")
    return {
        "name": f"{first} {last}".strip(),
        "short": last,
        "code": driver.get("code"),
        "team": (constructor or {}).get("name"),
        "colour": TEAM_COLOUR.get(cid) or known.get("colour") or "#8E8E93",
        "headshot": known.get("headshot"),
    }


# ------------------------------------------------------------------ results, records


def podium_at(year: int, circuit: str, kind: str):
    """Top three at a circuit in a given year. kind: 'results' (Grand Prix) or 'sprint'."""
    key = f"{kind}:{year}:{circuit}"
    if key in CACHE.get("results", {}):
        return CACHE["results"][key]
    j = get(f"{JOLPICA}/{year}/circuits/{circuit}/{kind}.json", {"limit": 3})
    races = (j or {}).get("MRData", {}).get("RaceTable", {}).get("Races", [])
    out = None
    if races:
        r = races[0]
        rows = r.get("Results") or r.get("SprintResults") or []
        if len(rows) >= 3:
            out = {
                "season": int(r["season"]),
                "race": r["raceName"],
                "laps": int(rows[0].get("laps") or 0) or None,
                "from_pole": rows[0].get("grid") == "1",
                "podium": [dict(person(x["Driver"], x.get("Constructor")), pos=int(x["position"]),
                                driver_id=x["Driver"]["driverId"]) for x in rows[:3]],
            }
    # past seasons never change; this season's are kept once the result is in
    if out is not None or year < TODAY.year:
        CACHE.setdefault("results", {})[key] = out
    return out


def faces(res):
    """Results are cached for good, but headshots arrive later: fill them in fresh each build."""
    if not res:
        return res
    out = dict(res, podium=[])
    for p in res["podium"]:
        last = p.get("short") or p["name"].rsplit(" ", 1)[-1]
        given = p["name"][: -len(last)].strip() if p["name"].endswith(last) else p["name"].split(" ")[0]
        known = known_driver(given, last)
        out["podium"].append(dict(p, headshot=known.get("headshot") or p.get("headshot")))
    return out


def last_time_here(season: int, circuit: str, kind: str):
    for year in range(season - 1, season - 8, -1):
        if year > TODAY.year:
            continue
        p = podium_at(year, circuit, kind)
        if p:
            return p
    return None


def first_gp(circuit: str):
    key = f"first:{circuit}"
    if key in CACHE.get("results", {}):
        return CACHE["results"][key]
    j = get(f"{JOLPICA}/circuits/{circuit}/races.json", {"limit": 1})
    races = (j or {}).get("MRData", {}).get("RaceTable", {}).get("Races", [])
    out = int(races[0]["season"]) if races else None
    CACHE.setdefault("results", {})[key] = out
    return out


def standings():
    j = get(f"{JOLPICA}/current/driverStandings.json")
    lists = (j or {}).get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
    if not lists:
        return None
    s = lists[0]
    rows = s["DriverStandings"]
    lead = float(rows[0]["points"]) if rows else 0
    drivers = []
    for r in rows[:5]:
        pts = float(r["points"])
        p = person(r["Driver"], (r.get("Constructors") or [None])[-1])
        drivers.append(dict(p, pos=int(r["position"]), points=pts, gap=lead - pts, wins=int(r["wins"])))
    cj = get(f"{JOLPICA}/current/constructorStandings.json")
    clists = (cj or {}).get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
    teams = []
    if clists:
        rows = clists[0]["ConstructorStandings"]
        clead = float(rows[0]["points"]) if rows else 0
        for r in rows[:3]:
            c = r["Constructor"]
            teams.append({"pos": int(r["position"]), "team": c["name"], "points": float(r["points"]),
                          "gap": clead - float(r["points"]),
                          "colour": TEAM_COLOUR.get(c["constructorId"], "#8E8E93")})
    return {"season": int(s["season"]), "round": int(s["round"]), "drivers": drivers, "constructors": teams}


# ------------------------------------------------------------------ the schedule


def season_schedule(year: int):
    j = get(f"{JOLPICA}/{year}.json", {"limit": 100})
    if j is None:
        return None
    return j["MRData"]["RaceTable"]["Races"]


def race_events(races: list[dict]) -> list[dict]:
    events = []
    total = len(races)
    sprint_total = sum(1 for r in races if r.get("Sprint"))
    sprint_no = 0
    # numbered by position, so a cancelled round can never leave a gap or a "Round 24 of 22"
    for rnd, r in enumerate(sorted(races, key=lambda x: (x["date"], int(x["round"]))), 1):
        c = r["Circuit"]
        loc = c["Location"]
        lat, lon = float(loc["lat"]), float(loc["long"])
        cc = iso2(loc["country"])
        tz = tz_for(lat, lon, c["circuitId"], cc)
        season = int(r["season"])
        circuit = {
            "id": c["circuitId"], "name": c["circuitName"], "locality": loc["locality"],
            "country": loc["country"], "country_code": cc, "lat": lat, "long": lon, "tz": tz,
        }
        weekend = []
        for key, label in (("FirstPractice", "Practice 1"), ("SprintQualifying", "Sprint Qualifying"),
                           ("SprintShootout", "Sprint Shootout"), ("Sprint", "Sprint"),
                           ("Qualifying", "Qualifying")):
            if r.get(key):
                w, timed = parse_when(r[key]["date"], r[key].get("time"))
                weekend.append({"name": label, "start": w.isoformat(), "timed": timed})
        w, timed = parse_when(r["date"], r.get("time"))
        weekend.append({"name": "Grand Prix", "start": w.isoformat(), "timed": timed})

        base = {"season": season, "round": rnd, "total_rounds": total, "circuit": circuit,
                "race_name": r["raceName"], "wiki": r.get("url"), "weekend": weekend}
        if r.get("Sprint"):
            sprint_no += 1
            s, s_timed = parse_when(r["Sprint"]["date"], r["Sprint"].get("time"))
            events.append(dict(base, type="sprint", start=s, timed=s_timed, hours=SPRINT_HOURS,
                               title=r["raceName"].replace("Grand Prix", "Sprint").strip(),
                               sprint_index=sprint_no, sprint_total=sprint_total,
                               uid=f"{season}-{slug(r['raceName'])}-sprint@{UID_DOMAIN}"))
        events.append(dict(base, type="race", start=w, timed=timed, hours=RACE_HOURS, title=r["raceName"],
                           sprint_weekend=bool(r.get("Sprint")),
                           uid=f"{season}-{slug(r['raceName'])}-race@{UID_DOMAIN}"))
    return events


def testing_events(season: int, known_circuits: dict) -> list[dict]:
    """Testing days: from OpenF1 once F1 publishes them, otherwise from testing.json."""
    days = []
    meetings = soft(get, f"{OPENF1}/meetings", {"year": season}, default=None) or []
    for m in meetings:
        if "test" not in (m.get("meeting_name") or "").lower():
            continue
        sessions = soft(get, f"{OPENF1}/sessions", {"meeting_key": m["meeting_key"]}, default=None) or []
        cc = OPENF1_COUNTRY.get(m.get("country_code", ""), "") or iso2(m.get("country_name", ""))
        here = known_circuits.get(cc) or TEST_VENUES.get(cc)
        for s in sessions:
            if not s.get("date_start"):
                continue
            days.append({
                "name": m.get("meeting_name") or "Pre-Season Testing",
                "start": dt.datetime.fromisoformat(s["date_start"]),
                "end": dt.datetime.fromisoformat(s["date_end"]) if s.get("date_end") else None,
                "circuit": here or {"name": m.get("circuit_short_name"), "locality": m.get("location"),
                                    "country": m.get("country_name"), "country_code": cc, "lat": None,
                                    "long": None, "tz": COUNTRY_TZ.get(cc, "UTC"),
                                    "id": slug(m.get("circuit_short_name") or "")},
                "provisional": False,
            })
    if not days:
        for t in json.loads((ROOT / "testing.json").read_text(encoding="utf-8")):
            if int(t["season"]) != season:
                continue
            cc = iso2(t["country"])
            lat, lon = float(t["lat"]), float(t["long"])
            tz = tz_for(lat, lon, t.get("circuit_id", ""), cc)
            circuit = known_circuits.get(cc) or {
                "id": t.get("circuit_id", slug(t["circuit"])), "name": t["circuit"], "locality": t["locality"],
                "country": t["country"], "country_code": cc, "lat": lat, "long": lon, "tz": tz}
            zone = ZoneInfo(circuit["tz"])
            for d in t["days"]:
                day = dt.date.fromisoformat(d)
                sh, sm = map(int, t["start_local"].split(":"))
                eh, em = map(int, t["end_local"].split(":"))
                days.append({
                    "name": t.get("name", "Pre-Season Testing"),
                    "start": dt.datetime(day.year, day.month, day.day, sh, sm, tzinfo=zone),
                    "end": dt.datetime(day.year, day.month, day.day, eh, em, tzinfo=zone),
                    "circuit": circuit, "provisional": bool(t.get("provisional", True)),
                })
    days.sort(key=lambda x: x["start"])
    out = []
    for i, d in enumerate(days, 1):
        start = d["start"].astimezone(dt.timezone.utc)
        end = (d["end"] or d["start"] + dt.timedelta(hours=9)).astimezone(dt.timezone.utc)
        out.append({
            "type": "testing", "season": season, "title": f"{d['name']} · Day {i}", "race_name": d["name"],
            "start": start, "end": end, "timed": True, "hours": (end - start).total_seconds() / 3600,
            "testing_day": i, "testing_days": len(days), "circuit": d["circuit"], "provisional": d["provisional"],
            "uid": f"{season}-testing-day-{start:%m%d}@{UID_DOMAIN}", "weekend": [], "wiki": None,
        })
    return out


# ------------------------------------------------------------------ title watch


def title_watch(events: list[dict], stand: dict | None):
    """Can the leader clinch the drivers' title at the next championship event?"""
    if not stand or len(stand["drivers"]) < 2:
        return None
    season = stand["season"]
    pending = sorted((e for e in events if e["season"] == season and e["type"] in ("race", "sprint")
                      and e["round"] > stand["round"]), key=lambda e: e["start_utc"])
    if not pending:
        return None
    worth = lambda e: SPRINT_POINTS if e["type"] == "sprint" else RACE_POINTS  # noqa: E731
    left = sum(worth(e) for e in pending)
    leader, rival = stand["drivers"][0], stand["drivers"][1]
    lead = leader["points"] - rival["points"]
    if lead > left:
        return {"champion": True, "leader": leader["name"], "text": f"{leader['name']} is the {season} champion."}
    nxt = pending[0]
    after = left - worth(nxt)
    if lead + worth(nxt) <= after:
        return None
    margin = int(lead - after)
    return {
        "event_uid": nxt["uid"], "leader": leader["name"], "rival": rival["name"], "margin": margin,
        "text": f"{leader['short']} can clinch the title here. He's champion unless {rival['short']} "
                f"outscores him by {margin} point{'s' if margin != 1 else ''} or more.",
    }


# ------------------------------------------------------------------ building


def enrich(e: dict) -> dict:
    c = e["circuit"]
    cc = c.get("country_code", "")
    start = e["start"]
    if isinstance(start, dt.datetime):
        start_utc = start.astimezone(dt.timezone.utc)
        end_utc = e.get("end") or start_utc + dt.timedelta(hours=e["hours"])
    else:  # date only: time not confirmed yet
        start_utc = dt.datetime(start.year, start.month, start.day, tzinfo=dt.timezone.utc)
        end_utc = start_utc + dt.timedelta(days=1)
    e.update(start_utc=start_utc, end_utc=end_utc, flag=flag_emoji(cc),
             provisional=e.get("provisional", False) or not e["timed"])
    if cc and (IMG / "flags" / f"{cc}.svg").exists():
        e["flag_images"] = {"svg": f"{PAGES_URL}/img/flags/{cc}.svg"}
    elif cc:
        e["flag_images"] = {"svg": f"https://flagcdn.com/{cc}.svg"}
    else:
        e["flag_images"] = None
    if e["type"] in ("race", "sprint") and c.get("id"):
        kind = "sprint" if e["type"] == "sprint" else "results"
        e["last_time"] = faces(soft(last_time_here, e["season"], c["id"], kind))
        if e["type"] == "sprint" and not e["last_time"]:
            e["first_sprint_here"] = True
            e["last_race_here"] = faces(soft(last_time_here, e["season"], c["id"], "results"))
        c["first_gp"] = soft(first_gp, c["id"])
        if e["end_utc"] < NOW:
            e["result"] = faces(soft(podium_at, e["season"], c["id"], kind))
    return e


def notes(e: dict, nxt: dict | None, watch: dict | None, stand: dict | None, changes: list) -> str:
    """The plain-text Notes Apple Calendar shows."""
    c = e["circuit"]
    lines = []
    for ch in changes:
        lines.append(f"⚠️ {ch['text']}")
    if lines:
        lines.append("")
    if e["type"] == "testing":
        lines += [f"PRE-SEASON TESTING · DAY {e['testing_day']} OF {e['testing_days']}",
                  "●" * e["testing_day"] + "○" * (e["testing_days"] - e["testing_day"]), ""]
    else:
        head = f"ROUND {e['round']} OF {e['total_rounds']}"
        lines.append(f"SPRINT · {head}" if e["type"] == "sprint" else head)
        lines.append("▰" * e["round"] + "▱" * (e["total_rounds"] - e["round"]))
        left = e["total_rounds"] - e["round"]
        if e["type"] == "sprint":
            lines.append(f"Sprint {e['sprint_index']} of {e['sprint_total']}")
        else:
            lines.append("Season finale" if left == 0 else f"{left} race{'s' if left != 1 else ''} to go after this one")
        lines.append("")
    if e.get("first_sprint_here"):
        lines.append("✨ First Sprint at this circuit")
    if e["timed"]:
        local = local_clock(e["start_utc"], c["tz"])
        lines.append(f"🏁 {'Running' if e['type'] == 'testing' else 'Lights out'} {local} local · {uk_clock(e['start_utc'])} UK"
                     + (" (provisional)" if e["provisional"] else ""))
    else:
        lines.append("🏁 Start time to be confirmed")
    last = e.get("last_time")
    if last and last.get("laps"):
        lines.append(f"🔁 {last['laps']} laps")
    if c.get("first_gp") and e["type"] == "race":
        lines.append(f"📅 First championship Grand Prix here: {c['first_gp']}")
    res = e.get("result")
    if res:
        lines += ["", f"RESULT", *[f"{m} {p['name']} ({p['team']})" for m, p in zip("🥇🥈🥉", res["podium"])]]
    shown = last or e.get("last_race_here")
    if shown and not res:
        if not last:
            label = "LAST GRAND PRIX HERE"
        else:
            label = "LAST YEAR" if shown["season"] == e["season"] - 1 else "LAST TIME HERE"
        lines += ["", f"{label} — {shown['season']}"]
        lines += [f"{m} {p['name']}" + (" (from pole)" if i == 0 and shown.get("from_pole") else "")
                  for i, (m, p) in enumerate(zip("🥇🥈🥉", shown["podium"]))]
    if watch and watch.get("event_uid") == e["uid"] and stand:
        lines += ["", "🏆 TITLE WATCH", watch["text"]]
        lines += [f"{d['pos']}. {d['name']} — {d['points']:g}" + (f"  (−{d['gap']:g})" if d["gap"] else "")
                  for d in stand["drivers"][:3]]
    if nxt:
        when = f"{uk_day(nxt['start_utc'])}" + (f", {uk_clock(nxt['start_utc'])}" if nxt["timed"] else "")
        lines += ["", "NEXT UP", f"{nxt['flag']} {nxt['title']} · {when}"]
    return "\n".join(lines).strip()


def location_text(c: dict) -> str:
    second = ", ".join(x for x in (c.get("locality"), c.get("country")) if x)
    return f"{c['name']}\n{second}" if second else c["name"]


def track_changes(events: list[dict]) -> dict:
    """Compare with the last build: note start times and venues that moved."""
    old = load("state.json", {})
    log = load("changes.json", {})
    for e in events:
        prev = old.get(e["uid"])
        key = e["uid"]
        now_start = e["start_utc"].isoformat()
        now_place = e["circuit"]["name"]
        if prev:
            if prev["start"] != now_start and e["timed"] and prev.get("timed", True):
                was = dt.datetime.fromisoformat(prev["start"])
                same_day = was.astimezone(UK).date() == e["start_utc"].astimezone(UK).date()
                text = (f"Start time changed on {uk_day(NOW)}: now {uk_clock(e['start_utc'])}, was {uk_clock(was)} UK"
                        if same_day else f"Date changed on {uk_day(NOW)}: now {uk_day(e['start_utc'])}, was {uk_day(was)}")
                log.setdefault(key, []).append({"kind": "time", "at": NOW.isoformat(), "was": prev["start"],
                                                "now": now_start, "text": text})
            if prev["place"] != now_place:
                log.setdefault(key, []).append({"kind": "venue", "at": NOW.isoformat(), "was": prev["place"],
                                                "now": now_place,
                                                "text": f"Moved on {uk_day(NOW)}: now at {now_place}, was {prev['place']}"})
    cutoff = NOW - dt.timedelta(days=CHANGE_DAYS)
    live = {e["uid"]: e for e in events}
    log = {k: [x for x in v if dt.datetime.fromisoformat(x["at"]) > cutoff]
           for k, v in log.items() if k in live and live[k]["end_utc"] > NOW}
    log = {k: v for k, v in log.items() if v}
    save("changes.json", log)
    save("state.json", {e["uid"]: {"start": e["start_utc"].isoformat(), "place": e["circuit"]["name"],
                                   "timed": e["timed"]} for e in events})
    return log


# ------------------------------------------------------------------ writing the .ics (RFC 5545, no libraries)


def ics_text(s: str) -> str:
    return (s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\n", "\\n"))


def ics_param(s: str) -> str:
    s = s.replace('"', "'")
    return f'"{s}"' if re.search(r"[:;,]", s) else s


def fold(line: str) -> str:
    """Lines longer than 75 bytes continue on the next line after a space (never splitting a character)."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def ics_local(d: dt.datetime) -> str:
    return d.strftime("%Y%m%dT%H%M%S")


def ics_utc(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def offset(td: dt.timedelta) -> str:
    mins = int(td.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    mins = abs(mins)
    return f"{sign}{mins // 60:02d}{mins % 60:02d}"


def vtimezone(name: str, first_year: int, last_year: int) -> list[str]:
    """A VTIMEZONE listing every real clock change in the years used, worked out from Python's own tz data."""
    zone = ZoneInfo(name)
    utc = dt.timezone.utc

    def at(t):
        return t.astimezone(zone)

    t = dt.datetime(first_year, 1, 1, tzinfo=utc)
    end = dt.datetime(last_year + 1, 1, 1, tzinfo=utc)
    start_off = at(t).utcoffset()
    first = "DAYLIGHT" if at(t).dst() else "STANDARD"
    lines = ["BEGIN:VTIMEZONE", f"TZID:{name}",
             f"BEGIN:{first}", "DTSTART:19700101T000000", f"TZOFFSETFROM:{offset(start_off)}",
             f"TZOFFSETTO:{offset(start_off)}", f"TZNAME:{at(t).tzname()}", f"END:{first}"]
    prev = start_off
    step = dt.timedelta(hours=6)
    while t < end:
        nxt = t + step
        off = at(nxt).utcoffset()
        if off != prev:
            lo, hi = t, nxt  # narrow down to the minute
            while hi - lo > dt.timedelta(minutes=1):
                mid = lo + (hi - lo) / 2
                if at(mid).utcoffset() == prev:
                    lo = mid
                else:
                    hi = mid
            change = hi.replace(second=0, microsecond=0)
            kind = "DAYLIGHT" if at(change).dst() else "STANDARD"
            wall = (change + prev).replace(tzinfo=None)  # local clock time just before the change
            lines += [f"BEGIN:{kind}", f"DTSTART:{ics_local(wall)}", f"TZOFFSETFROM:{offset(prev)}",
                      f"TZOFFSETTO:{offset(off)}", f"TZNAME:{at(change).tzname()}", f"END:{kind}"]
            prev = off
        t = nxt
    lines.append("END:VTIMEZONE")
    return lines


def build_ics(events: list[dict], extras: dict) -> bytes:
    head = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ketteringham//f1-calendar//EN", "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH", "X-WR-CALNAME:Formula 1",
            "X-WR-CALDESC:Grands Prix\\, Sprints and pre-season testing. Updated automatically.",
            "X-APPLE-CALENDAR-COLOR:#E10600", "REFRESH-INTERVAL;VALUE=DURATION:PT3H", "X-PUBLISHED-TTL:PT3H"]
    body = []
    zones = {}
    for e in events:
        c = e["circuit"]
        summary = f"{e['flag']} {e['title']}".strip()
        lines = ["BEGIN:VEVENT", f"UID:{e['uid']}", f"DTSTAMP:{ics_utc(NOW)}", f"SUMMARY:{ics_text(summary)}"]
        if e["timed"]:
            tz = c["tz"]
            zone = ZoneInfo(tz)
            s, f = e["start_utc"].astimezone(zone), e["end_utc"].astimezone(zone)
            years = zones.setdefault(tz, [s.year, f.year])
            years[0], years[1] = min(years[0], s.year), max(years[1], f.year)
            if tz == "UTC":
                lines += [f"DTSTART:{ics_utc(s)}", f"DTEND:{ics_utc(f)}"]
            else:
                lines += [f"DTSTART;TZID={tz}:{ics_local(s)}", f"DTEND;TZID={tz}:{ics_local(f)}"]
        else:
            d = e["start_utc"].date()
            lines += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + dt.timedelta(days=1):%Y%m%d}"]
        lines.append(f"LOCATION:{ics_text(location_text(c))}")
        if c.get("lat") is not None:
            addr = ", ".join(x for x in (c.get("locality"), c.get("country")) if x)
            lines.append(f"GEO:{c['lat']};{c['long']}")
            lines.append(f"X-APPLE-STRUCTURED-LOCATION;VALUE=URI;X-ADDRESS={ics_param(addr)};X-APPLE-RADIUS=500;"
                         f"X-TITLE={ics_param(c['name'])}:geo:{c['lat']},{c['long']}")
        lines.append(f"DESCRIPTION:{ics_text(extras[e['uid']])}")
        if e.get("wiki"):
            lines.append(f"URL;VALUE=URI:{e['wiki']}")
        lines += ["TRANSP:TRANSPARENT",  # Show As: Free
                  "X-MICROSOFT-CDO-BUSYSTATUS:FREE",
                  "STATUS:CONFIRMED",
                  f"SEQUENCE:{e.get('sequence', 0)}",
                  f"CATEGORIES:Formula 1,{e['type'].title()}",
                  "END:VEVENT"]  # deliberately no VALARM: no alerts
        body += lines
    tzs = []
    for tz, (a, b) in sorted(zones.items()):
        if tz != "UTC":
            tzs += vtimezone(tz, a, b)
    return ("\r\n".join(fold(x) for x in head + tzs + body + ["END:VCALENDAR"]) + "\r\n").encode("utf-8")


def public(e: dict, changes: list) -> dict:
    """The event as the Home Assistant pop-up sees it."""
    keep = ("uid", "type", "season", "round", "total_rounds", "sprint_index", "sprint_total", "sprint_weekend",
            "testing_day", "testing_days", "title", "race_name", "flag", "flag_images", "provisional", "timed",
            "circuit", "weekend", "wiki", "last_time", "last_race_here", "first_sprint_here", "result")
    out = {k: e[k] for k in keep if k in e and e[k] is not None}
    out["start"] = e["start_utc"].isoformat()
    out["end"] = e["end_utc"].isoformat()
    if e["timed"]:
        out["local_start"] = e["start_utc"].astimezone(ZoneInfo(e["circuit"]["tz"])).isoformat()
    out["changes"] = changes
    return out


def main() -> int:
    print("Fetching the schedule…")
    seasons = {}
    for year in (TODAY.year, TODAY.year + 1):
        races = season_schedule(year)
        if races:
            seasons[year] = races
            print(f"  {year}: {len(races)} rounds")
    if TODAY.year not in seasons:
        print("The current season's schedule didn't come back. Keeping the previous calendar.", file=sys.stderr)
        return 1

    old = load("state.json", {})
    for year, races in seasons.items():  # guard against a half-empty response wiping the calendar
        before = sum(1 for k in old if k.startswith(f"{year}-") and "-testing-" not in k and k.endswith(f"-race@{UID_DOMAIN}"))
        if before >= 8 and len(races) < before * 0.6:
            print(f"{year} dropped from {before} to {len(races)} rounds in one go; that looks wrong. Stopping.", file=sys.stderr)
            return 1

    soft(refresh_headshots)

    events = []
    for year in (TODAY.year, TODAY.year + 1):
        # next year's testing goes in even before next year's race schedule is published
        evs = race_events(seasons[year]) if year in seasons else []
        circuits = {}
        for e in evs:
            circuits.setdefault(e["circuit"]["country_code"], e["circuit"])
        events += evs
        events += testing_events(year, circuits)

    # headshots and flags are kept in the repository and served from this site, so the pop-up never
    # depends on F1's or flagcdn's servers and the headshots are a sharp size
    for key, d in sorted(CACHE.get("drivers", {}).items()):
        if d.get("headshot"):
            soft(save_headshot, key, d["headshot"], d.get("team", ""))
    for cc in sorted({e["circuit"].get("country_code") for e in events if e["circuit"].get("country_code")}):
        soft(save_flag, cc)

    events = [enrich(e) for e in events]
    events.sort(key=lambda e: e["start_utc"])

    stand = soft(standings)
    watch = title_watch(events, stand)
    log = track_changes(events)
    for e in events:
        e["sequence"] = len(log.get(e["uid"], []))

    upcoming = [e for e in events if e["type"] in ("race", "sprint")]
    extras = {}
    for e in events:
        later = [x for x in upcoming if x["start_utc"] > e["start_utc"] and x["uid"] != e["uid"]]
        extras[e["uid"]] = notes(e, later[0] if later else None, watch, stand, log.get(e["uid"], []))

    SITE.mkdir(exist_ok=True)
    (SITE / "f1.ics").write_bytes(build_ics(events, extras))
    payload = {
        "generated": NOW.isoformat(),
        "calendar": f"{PAGES_URL}/f1.ics",
        "standings": stand,
        "title_watch": watch,
        "events": [public(e, log.get(e["uid"], [])) for e in events],
    }
    (SITE / "f1.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (SITE / ".nojekyll").write_text("")
    if IMG.exists():
        import shutil
        shutil.copytree(IMG, SITE / "img", dirs_exist_ok=True)
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    (SITE / "index.html").write_text(index.replace("{{UPDATED}}", NOW.astimezone(UK).strftime("%-d %b %Y, %H:%M")),
                                     encoding="utf-8")
    save("cache.json", CACHE)
    save("last_run.json", {"date": TODAY.isoformat(), "events": len(events)})
    print(f"Done: {len(events)} events ({sum(e['type'] == 'race' for e in events)} races, "
          f"{sum(e['type'] == 'sprint' for e in events)} sprints, {sum(e['type'] == 'testing' for e in events)} testing days)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
