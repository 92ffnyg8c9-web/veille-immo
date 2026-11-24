import json, os, smtplib, statistics, time
from email.message import EmailMessage
import requests

# ====== RÉGLAGES (modifie ici) ======
LIEUX = ["Nîmes", "Gard"]
PRIX_MAX = 90000
LOYER_M2 = 12.0         # hypothèse : loyer longue durée en €/m²/mois
FACTEUR_AIRBNB = 1.6    # hypothèse : Airbnb = loyer classique x 1,6
SEUIL_DECOTE = 0.15     # 15 % sous la médiane €/m² = pépite
SEUIL_RENDEMENT = 0.09  # 9 % brut = pépite
MAX_ENVOI = 15
DPE_OK = {"A", "B", "C", "D", "E"}   # F, G et DPE inconnu sont exclus
MOTS = ["travaux", "rénover", "renover", "rafraîchir", "investisseur", "succession", "urgent"]
# ====================================

BASE = "https://www.bienici.com"
H = {"User-Agent": "Mozilla/5.0 (veille immo perso)"}


def zone_ids(nom):
    r = requests.get(f"{BASE}/suggest.json", params={"q": nom}, headers=H, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        print("Zone introuvable :", nom)
        return []
    print("Zone", nom, "->", data[0].get("name"), data[0].get("zoneIds"))
    return data[0].get("zoneIds", [])


def annonces(zones):
    out, frm = [], 0
    while frm < 600:
        filters = {
            "size": 100, "from": frm, "filterType": "buy",
            "propertyType": ["house", "flat"], "maxPrice": PRIX_MAX,
            "sortBy": "publicationDate", "sortOrder": "desc",
            "onTheMarket": [True], "zoneIdsByTypes": {"zoneIds": zones},
        }
        r = requests.get(f"{BASE}/realEstateAds.json",
                         params={"filters": json.dumps(filters)}, headers=H, timeout=30)
        r.raise_for_status()
        lot = r.json().get("realEstateAds", [])
        out += lot
        if len(lot) < 100:
            break
        frm += 100
        time.sleep(2)
    return out


def analyse(ads):
    rows = []
    for a in ads:
        prix, surf = a.get("price"), a.get("surfaceArea")
        if isinstance(prix, list):
            prix = prix[0] if prix else None
        if not prix or not surf or surf < 9:
            continue
        dpe = a.get("energyClassification") or "?"
        if dpe not in DPE_OK:
            continue
        texte = (a.get("title", "") + " " + a.get("description", "")).lower()
        rows.append({
            "id": a.get("id"), "prix": prix, "surf": surf, "dpe": dpe,
            "ville": a.get("city", "?"), "ppm2": prix / surf,
            "mots": [m for m in MOTS if m in texte],
            "url": f"{BASE}/annonce/{a.get('id')}",
        })
    if not rows:
        return rows
    par_ville = {}
    for r in rows:
        par_ville.setdefault(r["ville"], []).append(r["ppm2"])
    global_med = statistics.median([r["ppm2"] for r in rows])
    for r in rows:
        v = par_ville[r["ville"]]
        ref = statistics.median(v) if len(v) >= 4 else global_med
        r["decote"] = 1 - r["ppm2"] / ref
        r["rdt"] = r["surf"] * LOYER_M2 * 12 / r["prix"]
        r["rdt_bnb"] = r["rdt"] * FACTEUR_AIRBNB
        r["score"] = r["decote"] * 100 + r["rdt"] * 100 + 3 * len(r["mots"])
    return rows


def pepite(r):
    return r["decote"] >= SEUIL_DECOTE or r["rdt"] >= SEUIL_RENDEMENT


def envoyer(liste):
    lignes = []
    for r in liste:
        note = " | DPE E : interdiction de location prévue en 2034" if r["dpe"] == "E" else ""
        mots = (" | mots : " + ", ".join(r["mots"])) if r["mots"] else ""
        lignes.append(
            f"{r['ville']} - {r['prix']:,} EUR - {r['surf']:.0f} m2 - {r['ppm2']:.0f} EUR/m2 - DPE {r['dpe']}\n"
            f"  decote vs mediane : {r['decote']*100:.0f} % | rdt brut loyer classique ~{r['rdt']*100:.1f} % | Airbnb ~{r['rdt_bnb']*100:.1f} % (estimations){note}{mots}\n"
            f"  {r['url']}\n")
    msg = EmailMessage()
    msg["Subject"] = f"Veille immo : {len(liste)} pepite(s)"
    msg["From"], msg["To"] = os.environ["MAIL_USER"], os.environ["MAIL_TO"]
    msg.set_content("\n".join(lignes))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["MAIL_USER"], os.environ["MAIL_PASS"])
        s.send_message(msg)


def main():
    vus = set(json.load(open("seen.json"))) if os.path.exists("seen.json") else set()
    zones = []
    for lieu in LIEUX:
        zones += zone_ids(lieu)
    zones = list(dict.fromkeys(zones))
    if not zones:
        raise SystemExit("Aucune zone trouvee")
    rows = analyse(annonces(zones))
    print(len(rows), "annonces analysees")
    nouveaux = [r for r in rows if r["id"] not in vus]
    top = sorted([r for r in nouveaux if pepite(r)], key=lambda r: -r["score"])[:MAX_ENVOI]
    print(len(nouveaux), "nouvelles,", len(top), "pepites")
    if top:
        envoyer(top)
    vus |= {r["id"] for r in rows}
    json.dump(sorted(vus), open("seen.json", "w"))


if __name__ == "__main__":
    main()
