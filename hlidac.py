import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ================= NASTAVENÍ =================
# Hlídání "natvrdo" (volitelné). Další zadáváš zprávou v Discordu.
HLEDANI = [
    # {"datum": "2026-11-27", "od": "17:00", "do": "20:00", "delka_min": 90},
]
# =============================================

# Tajné hodnoty se berou z GitHub Secrets
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")

URL = "https://onepadel.cz/book"
HEADERS = {
    "accept": "text/x-component",
    "content-type": "text/plain;charset=UTF-8",
    "next-action": "406265e6b32ee4eb192a7a6b28e77e38830db60a39",
    "origin": "https://onepadel.cz",
    "referer": "https://onepadel.cz/book",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36",
}
PRAHA = ZoneInfo("Europe/Prague")
STAV_SOUBOR = Path(__file__).with_name("stav.json")
POVOLENE_DELKY = {60, 90, 120, 150, 180, 210, 240}
DATUM_RE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.?(?:\s*(\d{4}))?")
NAPOVEDA = ("Příklad: hlidej 27.11. 17-20 90 (datum, rozmezí začátku, délka v minutách). "
            "Další příkazy: zrus 27.11. · zrus vse · seznam")


# ---------- odesílání zpráv ----------
def posli_zpravu(text):
    if not DISCORD_WEBHOOK:
        raise RuntimeError("Chybí DISCORD_WEBHOOK (nastav ho v GitHub Secrets)")
    r = requests.post(DISCORD_WEBHOOK, json={"content": text}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Discord vrátil chybu {r.status_code}")


# ---------- stav ----------
def nacti_stav():
    try:
        return json.loads(STAV_SOUBOR.read_text(encoding="utf-8"))
    except Exception:
        return {}


def uloz_stav(stav):
    STAV_SOUBOR.write_text(json.dumps(stav, ensure_ascii=False), encoding="utf-8")


# ---------- příkazy z Discordu ----------
def bez_diakritiky(t):
    return "".join(c for c in unicodedata.normalize("NFD", t)
                   if unicodedata.category(c) != "Mn")


def popis(h):
    d = date.fromisoformat(h["datum"]).strftime("%d.%m.%Y")
    return f'{d}, start {h["od"]}–{h["do"]}, hra {h["delka_min"]} min'


def najdi_datum(text, dnes):
    m = DATUM_RE.search(text)
    if not m:
        return None
    den, mesic = int(m.group(1)), int(m.group(2))
    rok = int(m.group(3)) if m.group(3) else None
    try:
        d = date(rok or dnes.year, mesic, den)
        if rok is None and d < dnes:
            d = date(dnes.year + 1, mesic, den)
    except ValueError:
        return None
    return d, m


def parse_hlidej(text, dnes):
    nalez = najdi_datum(text, dnes)
    if not nalez:
        return None
    d, m = nalez
    zbytek = text[:m.start()] + " " + text[m.end():]
    t = re.search(r"(\d{1,2})(?::(\d{2}))?\s*[-–]\s*(\d{1,2})(?::(\d{2}))?", zbytek)
    if not t:
        return None
    h1, m1 = int(t.group(1)), int(t.group(2) or 0)
    h2, m2 = int(t.group(3)), int(t.group(4) or 0)
    if not (0 <= h1 <= 23 and 0 <= h2 <= 23 and m1 < 60 and m2 < 60):
        return None
    od, do = f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"
    if od > do:
        return None
    zbytek2 = zbytek[:t.start()] + " " + zbytek[t.end():]
    dl = re.search(r"\b(\d{2,3})\b", zbytek2)
    delka = int(dl.group(1)) if dl else 90
    if delka not in POVOLENE_DELKY:
        return None
    return {"datum": d.isoformat(), "od": od, "do": do, "delka_min": delka}


def zpracuj_prikaz(text, hledani, dnes):
    t = bez_diakritiky(text.strip().lower())
    slovo, _, zbytek = t.partition(" ")
    zbytek = zbytek.strip()

    if slovo in ("hlidej", "hledej", "hlidat"):
        h = parse_hlidej(zbytek, dnes)
        if not h:
            return "❓ Nerozumím. " + NAPOVEDA
        if h in hledani:
            return f"ℹ️ Už hlídám: {popis(h)}"
        hledani.append(h)
        return f"✅ Hlídám: {popis(h)}"

    if slovo in ("zrus", "smaz"):
        if zbytek == "vse":
            n = len(hledani)
            hledani.clear()
            return f"🗑️ Zrušeno všech hlídání: {n}"
        nalez = najdi_datum(zbytek, dnes)
        if not nalez:
            return "❓ Napiš datum, např.: zrus 27.11. (nebo: zrus vse)"
        iso = nalez[0].isoformat()
        zbyva = [h for h in hledani if h["datum"] != iso]
        n = len(hledani) - len(zbyva)
        hledani[:] = zbyva
        return f"🗑️ Zrušeno hlídání pro {nalez[0].strftime('%d.%m.%Y')}: {n}"

    if slovo in ("seznam", "stav"):
        vsechna = HLEDANI + hledani
        if not vsechna:
            return "📋 Teď nic nehlídám."
        return "📋 Hlídám:\n" + "\n".join("• " + popis(h) for h in vsechna)

    return None  # ostatní zprávy ignorujeme


def nacti_zpravy(last_id):
    params = {"limit": 50}
    if last_id:
        params["after"] = last_id
    r = requests.get(
        f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                 "User-Agent": "DiscordBot (padel-hlidac, 1.0)"},
        params=params, timeout=30,
    )
    r.raise_for_status()
    return sorted(r.json(), key=lambda m: int(m["id"]))


def zpracuj_discord(stav, dnes):
    if not (DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID):
        return
    try:
        zpravy = nacti_zpravy(stav.get("last_msg"))
    except Exception as e:
        print("Příkazy z Discordu nelze načíst:", e)
        if not stav.get("chyba_prikazy"):
            try:
                posli_zpravu(f"⚠️ Nemohu číst příkazy z Discordu: {str(e)[:150]}")
                stav["chyba_prikazy"] = True
            except Exception as e2:
                print("Nepodařilo se odeslat upozornění:", e2)
        return
    stav["chyba_prikazy"] = False

    odpovedi = []
    for m in zpravy:
        stav["last_msg"] = m["id"]
        if m.get("author", {}).get("bot") or m.get("webhook_id"):
            continue
        o = zpracuj_prikaz(m.get("content", ""), stav["hledani"], dnes)
        if o:
            odpovedi.append(o)
    if odpovedi:
        try:
            posli_zpravu("\n".join(odpovedi))
        except Exception as e:
            print("Odpověď se nepodařilo odeslat:", e)


# ---------- hledání kurtů ----------
def nacti_sloty(ymd, delka):
    body = [{"tenantSlug": "onepadel", "venueSlug": "praha-zlicin",
             "ymd": ymd, "durationMinutes": delka}]
    r = requests.post(URL, headers=HEADERS, data=json.dumps(body), timeout=30)
    r.raise_for_status()
    for radek in r.text.splitlines():
        _, _, obsah = radek.partition(":")
        if not obsah.startswith("{"):
            continue
        try:
            data = json.loads(obsah)
        except ValueError:
            continue
        if isinstance(data, dict) and "ok" in data:
            if not data["ok"]:
                raise RuntimeError(f"API vrátilo ok=false: {obsah[:200]}")
            return data.get("slots", [])
    raise RuntimeError("V odpovědi nebyla data o slotech (změnil se web?)")


def hledej(h):
    nalezene = {}
    for s in nacti_sloty(h["datum"], h["delka_min"]):
        zac = datetime.fromisoformat(s["startsAt"].replace("Z", "+00:00")).astimezone(PRAHA)
        kon = datetime.fromisoformat(s["endsAt"].replace("Z", "+00:00")).astimezone(PRAHA)
        zacatek, konec = zac.strftime("%H:%M"), kon.strftime("%H:%M")
        # okno platí jen pro ZAČÁTEK rezervace
        if h["od"] <= zacatek <= h["do"] and s.get("freeCourtsCount", 0) > 0:
            klic = f'{h["datum"]}|{h["delka_min"]}|{zacatek}'
            cena = s.get("minPriceMinor", 0) // 100
            nalezene[klic] = (f'{zac.strftime("%d.%m.")} {zacatek}–{konec} · '
                              f'volných kurtů: {s["freeCourtsCount"]} · od {cena} Kč')
    return nalezene


def main():
    if "--test" in sys.argv:
        posli_zpravu("Test: hlídač kurtů funguje ✅")
        print("Testovací zpráva odeslána.")
        return

    dnes = datetime.now(PRAHA).date()
    stav = nacti_stav()
    stav.setdefault("videno", [])
    stav.setdefault("hledani", [])
    stav.setdefault("chyba", False)
    stav.setdefault("chyba_prikazy", False)
    stav.setdefault("last_msg", None)
    stav["hledani"] = [h for h in stav["hledani"] if h["datum"] >= dnes.isoformat()]

    zpracuj_discord(stav, dnes)
    uloz_stav(stav)

    videno = set(stav["videno"])
    aktualni = {}
    try:
        for h in HLEDANI + stav["hledani"]:
            if h["datum"] < dnes.isoformat():
                continue
            aktualni.update(hledej(h))
    except Exception as e:
        print("CHYBA:", e)
        if not stav["chyba"]:              # upozorní jen jednou
            try:
                posli_zpravu(f"⚠️ Hlídač kurtů nefunguje: {str(e)[:150]}")
                stav["chyba"] = True
                uloz_stav(stav)
            except Exception as e2:
                print("Nepodařilo se odeslat upozornění:", e2)
        return

    nove = sorted(k for k in aktualni if k not in videno)
    if nove:
        radky = [aktualni[k] for k in nove[:15]]
        zprava = "🎾 Volný kurt One Padel Zličín:\n" + "\n".join(radky)
        if len(nove) > 15:
            zprava += f"\n… a dalších {len(nove) - 15}"
        zprava += "\nhttps://onepadel.cz/book"
        posli_zpravu(zprava)
        print(zprava)
    else:
        print("Nic nového.")

    # slot, který zmizí a pak se znovu uvolní, ohlásí skript znovu
    stav["videno"] = list(aktualni)
    stav["chyba"] = False
    uloz_stav(stav)


if __name__ == "__main__":
    main()
