"""
Themis-controle over de Orangefit-add-feed
==========================================
De teksten in de add-feed zijn LETTERLIJK die van orangefit.nl. Wat daar mag staan,
mag bij Good For You niet automatisch ook: wij zijn zelf verantwoordelijk voor elke
claim op onze eigen pagina's (EFSA/NVWA/KOAG-KAG). Orangefit verkoopt ook
maaltijdvervangers voor gewichtsbeheersing; daar gelden extra regels.

    python themis_check.py

Draait alleen lokaal, in "Claude Code Projecten" waar gfy-themis naast deze repo
staat. In GitHub Actions is Themis er niet; dan stopt het script netjes. Daarom
staat `published` in de add-feed hard op false: bouwen mag, publiceren verdien je.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HIER = Path(__file__).parent
sys.path.insert(0, str(HIER))
import orangefit_common as oc  # noqa: E402

ADD = HIER / "orangefit_add_feed.xml"
RAPPORT = HIER / "orangefit_themis.md"
THEMIS_PAD = HIER.parent.parent / "gfy-themis"


def laad_themis():
    if not THEMIS_PAD.exists():
        print(f"gfy-themis niet gevonden ({THEMIS_PAD}) — controle overgeslagen.")
        print("Draai dit script lokaal in 'Claude Code Projecten'.")
        return None
    sys.path.insert(0, str(THEMIS_PAD))
    import themis
    return themis


def main():
    themis = laad_themis()
    if themis is None:
        return 0
    if not ADD.exists():
        print("orangefit_add_feed.xml ontbreekt — draai eerst add_scraper.py")
        return 1

    root = ET.parse(ADD).getroot()
    regels, tellen = [], {"ok": 0, "let-op": 0, "afkeuren": 0}
    for p in root.findall("product"):
        titel = (p.findtext("title") or "").strip()
        handle = (p.findtext("handle") or "").strip()
        tekst = oc.plat(p.findtext("description") or "")
        if not tekst:
            continue
        rapport = themis.toets(tekst)
        tellen[rapport.oordeel] = tellen.get(rapport.oordeel, 0) + 1
        if rapport.oordeel == "ok":
            continue
        regels.append(f"### {titel} (`{handle}`)\n")
        regels.append(f"**Oordeel: {rapport.oordeel}**\n")
        for pr in rapport.problemen:
            regels.append(f"- `{pr['term']}` — {pr['categorie']} ({pr['bron']}): "
                          f"{pr['toelichting']}")
        for s in rapport.suggesties:
            regels.append(f"- in plaats van \"{s['niet']}\": {s['wel']}")
        for a in rapport.art14_signalen:
            regels.append(f"- artikel-14-doelgroep: {a}")
        regels.append("")

    kop = [
        "# Themis over de Orangefit add-feed",
        "",
        "De teksten hieronder komen letterlijk van orangefit.nl (zonder winkelpraat).",
        "Alles wat hier staat moet aangepast zijn *voordat* een product in Shopify op",
        "'published' gaat. Daarnaast: de teksten zijn in je-vorm en Orangefits",
        "wij-vorm (zie orangefit_tekstbron.csv) en moeten naar de u-vorm.",
        "",
        f"- ok: **{tellen.get('ok', 0)}**",
        f"- let op: **{tellen.get('let-op', 0)}**",
        f"- afkeuren: **{tellen.get('afkeuren', 0)}**",
        "",
    ]
    RAPPORT.write_text("\n".join(kop + regels), encoding="utf-8")
    print(f"Rapport geschreven: {RAPPORT.name}")
    print(f"  ok {tellen.get('ok', 0)} | let op {tellen.get('let-op', 0)} "
          f"| afkeuren {tellen.get('afkeuren', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
