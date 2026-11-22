import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ================= NASTAVENÍ =================
# >>> TADY DOPLŇ, CO A KDY CHCEŠ HLÍDAT <<<
HLEDANI = [
    # datum (RRRR-MM-DD)
    # od, do   = rozmezí, ve kterém může rezervace ZAČÍNAT (včetně "do")
    # delka_min = délka hry v minutách (60, 90, 120, ...)
    {"datum": "2026-11-26", "od": "17:00", "do": "20:00", "delka_min": 90},
    # další hledání přidáš zkopírováním řádku výše, např.:
    # {"datum": "2026-11-28", "od": "10:00", "do": "14:00", "delka_min": 90},
]
# =============================================

# Číslo a klíč se sem NEPÍŠOU, berou se z GitHub Secrets
WA_TELEFON = os.environ.get("WA_TELEFON", "")
WA_APIKEY = os.environ.get("WA_APIKEY", "")

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


def posli_wa(text):
    if not WA_TELEFON or not WA_APIKEY:
        raise RuntimeError("Chybí WA_TELEFON nebo WA_APIKEY (nastav je v GitHub Secrets)")
    r = requests.get(
        "https://api.callmebot.com/whatsapp.php",
        params={"phone": WA_TELEFON, "text": text, "apikey": WA_APIKEY},
        timeout=30,
    )
    r.raise_for_status()


def nacti_stav():
    try:
        return json.loads(STAV_SOUBOR.read_text(encoding="utf-8"))
    except Exception:
        return {"videno": [], "chyba": False}


def uloz_stav(stav):
    STAV_SOUBOR.write_text(json.dumps(stav), encoding="utf-8")


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
        posli_wa("Test: hlídač kurtů funguje ✅")
        print("Testovací zpráva odeslána.")
        return

    stav = nacti_stav()
    videno = set(stav.get("videno", []))
    aktualni = {}
    dnes = datetime.now(PRAHA).date().isoformat()

    try:
        for h in HLEDANI:
            if h["datum"] < dnes:
                continue
            aktualni.update(hledej(h))
    except Exception as e:
        print("CHYBA:", e)
        if not stav.get("chyba"):          # upozorní jen jednou
            try:
                posli_wa(f"⚠️ Hlídač kurtů nefunguje: {str(e)[:150]}")
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
        posli_wa(zprava)
        print(zprava)
    else:
        print("Nic nového.")

    # slot, který zmizí a pak se znovu uvolní, ohlásí skript znovu
    uloz_stav({"videno": list(aktualni), "chyba": False})


if __name__ ==
