"""
Invarianten van de Orangefit-feeds
==================================
Geen gedrukt getal zonder een test die zijn betekenis vastpint. Draai deze test
na elke scraper-run:

    python test_feed.py                # alle feeds
    python test_feed.py --alleen-update

Hij kijkt niet of de scraper "werkt", maar of de XML betekent wat het etiket zegt:
elke regel een echte artikelcode met OF-voorvoegsel, een prijs boven nul, een
kloppende of lege EAN, geen dubbelen, geen winkelpraat van Orangefit in de teksten,
en de optelsom feed + overgeslagen = de hele Shopify-catalogus van Orangefit.
"""

import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HIER = Path(__file__).parent
sys.path.insert(0, str(HIER))
import orangefit_common as oc  # noqa: E402

UPDATE = HIER / "orangefit_feed.xml"
ADD = HIER / "orangefit_add_feed.xml"
PLAT = HIER / "orangefit_add_feed_plat.xml"
OVERGESLAGEN = HIER / "orangefit_overgeslagen.csv"
GESCHRAPT = HIER / "orangefit_geschrapt.csv"
BRON = HIER / "orangefit_tekstbron.csv"
TELLING = HIER / "orangefit_telling.json"

# De catalogus op de dag van bouwen (30-09-2026): 35 Shopify-producten met 148
# varianten, waarvan 17 producten / 38 varianten verkoopbaar. Wijkt een run hier ver
# vanaf, dan is er iets veranderd aan de winkel of aan de scraper — en dat wil je
# weten vóórdat Stock Sync ermee aan de haal gaat.
VERWACHT_MINIMAAL = 25
VERWACHT_MAXIMAAL = 80

SKU_RE = re.compile(r"OF-[A-Z0-9]+(?:-[A-Z0-9]+)*")

# Wat na het schoonmaken nooit meer in een beschrijving mag staan. Dit is de test
# onder `weer_winkelpraat`: valt hij om, dan staat Orangefits winkel op onze pagina.
VERBODEN_IN_TEKST = [
    ("contactgegevens van Orangefit", r"hallo@orangefit|582[\s-]?2351|klantenservice"),
    ("verwijzing naar Orangefits site", r"orangefit\.(?:nl|com|eu)"),
    ("Trustpilot of reviews van Orangefit", r"trustpilot|\breviews?\b|\{amount\}|\{score\}"),
    ("Orangefits abonnement of korting", r"\brepeat\b|abonnement|kortingscode"),
    ("Orangefits bezorgbelofte", r"in huis\b|22[.:]00|verzend"),
    ("Orangefits geld-terug-garantie", r"geld terug|niet tevreden"),
    ("prijs van Orangefit", r"€\s?\d|t\.w\.v"),
    ("ranglijst of testuitslag", r"\bnr\W{0,2}1\b|consumentenbond"),
    ("script, stijl of klasse", r"<script|<style|style=|class=|<iframe|<form|<div|<span"),
    ("link of afbeelding", r"<a\s|href=|<img"),
    ("losse zin na een afkorting", r"(?:^|>)\s*1 van nederland"),
]

fouten = []


def eis(voorwaarde, boodschap):
    if not voorwaarde:
        fouten.append(boodschap)


def lees(pad):
    if not pad.exists():
        fouten.append(f"{pad.name} bestaat niet — draai eerst de scraper")
        return None
    return ET.parse(pad).getroot()


def tekst(el, tag):
    kind = el.find(tag)
    return (kind.text or "").strip() if kind is not None else ""


def toets_regel(el, waar):
    """Wat voor elke variantregel geldt, in elke feed."""
    sku = tekst(el, "sku")
    eis(bool(SKU_RE.fullmatch(sku)),
        f"{waar}: '{sku}' is geen OF-<artikelcode> (pakketcode of lege SKU gelekt?)")
    eis(sku == oc.SKU_PREFIX + tekst(el, "sku_leverancier"),
        f"{waar}: sku '{sku}' is niet OF- + sku_leverancier '{tekst(el, 'sku_leverancier')}'")
    prijs = tekst(el, "price")
    eis(bool(prijs) and float(prijs) > 0, f"{sku}: prijs is '{prijs}' (moet > 0)")
    eis(tekst(el, "compare_at_price") == "", f"{sku}: compare_at_price gevuld (nooit verzinnen)")
    eis(tekst(el, "available") in ("true", "false"),
        f"{sku}: available is '{tekst(el, 'available')}'")
    ean = tekst(el, "barcode")
    eis(ean == "" or oc.geldige_ean(ean), f"{sku}: barcode '{ean}' heeft geen geldig controlecijfer")
    eis(tekst(el, "option1"), f"{sku}: geen option1 — varianten botsen dan")
    return sku, ean


def toets_update():
    root = lees(UPDATE)
    if root is None:
        return []
    regels = root.findall("product")
    skus, eans, handles, paren = [], [], set(), []
    for r in regels:
        sku, ean = toets_regel(r, "update-feed")
        handle = tekst(r, "handle")
        eis(handle.startswith(oc.HANDLE_PREFIX),
            f"{sku}: handle '{handle}' mist het voorvoegsel {oc.HANDLE_PREFIX} (dan kan "
            f"Stock Sync hem in andermans product schuiven; creatine/probiotica bestaan al)")
        eis(r.find("description") is None, f"{sku}: de update-feed hoort geen beschrijving te bevatten")
        in_actie = tekst(r, "in_actie") == "true"
        if in_actie and oc.PRIJS_BASIS == "advies":
            eis(float(tekst(r, "price")) > float(tekst(r, "prijs_actueel")),
                f"{sku}: in actie maar price is niet de reguliere prijs")
        if not in_actie:
            eis(tekst(r, "price") == tekst(r, "prijs_actueel"),
                f"{sku}: geen actie, maar price {tekst(r, 'price')} != prijs_actueel "
                f"{tekst(r, 'prijs_actueel')}")
        skus.append(sku)
        if ean:
            eans.append(ean)
        handles.add(handle)
        paren.append((handle, tekst(r, "option1"), tekst(r, "option2")))
    eis(len(skus) == len(set(skus)),
        f"dubbele SKU in de update-feed: {sorted({s for s in skus if skus.count(s) > 1})}")
    eis(len(eans) == len(set(eans)),
        f"dubbele EAN in de update-feed: {sorted({e for e in eans if eans.count(e) > 1})}")
    eis(len(paren) == len(set(paren)),
        "twee varianten van hetzelfde product met dezelfde opties")
    eis(VERWACHT_MINIMAAL <= len(skus) <= VERWACHT_MAXIMAAL,
        f"update-feed heeft {len(skus)} varianten, verwacht tussen "
        f"{VERWACHT_MINIMAAL} en {VERWACHT_MAXIMAAL}")
    print(f"   update-feed: {len(skus)} varianten over {len(handles)} producten, "
          f"{len(eans)} met EAN")
    return skus


def toets_add(update_skus):
    root = lees(ADD)
    if root is None:
        return []
    producten = root.findall("product")
    skus, handles = [], []
    for p in producten:
        handle = tekst(p, "handle")
        handles.append(handle)
        eis(handle.startswith(oc.HANDLE_PREFIX), f"{handle}: handle mist het voorvoegsel")
        eis(tekst(p, "title").startswith("Orangefit "), f"{handle}: titel begint niet met 'Orangefit '")
        eis(tekst(p, "vendor") == oc.BRAND, f"{handle}: vendor is '{tekst(p, 'vendor')}'")
        eis(tekst(p, "product_type") in oc.VOEDING_TYPES.values(),
            f"{handle}: producttype '{tekst(p, 'product_type')}' staat niet in VOEDING_TYPES")
        eis(tekst(p, "published") == "false", f"{handle}: published moet 'false' zijn (concept-only)")
        eis(p.findall("images/image"), f"{handle}: geen afbeelding")
        eis(tekst(p, "option1_name"), f"{handle}: geen naam voor optie 1")

        beschrijving = tekst(p, "description")
        lengte = len(oc.plat(beschrijving))
        eis(lengte > 1500, f"{handle}: beschrijving is maar {lengte} tekens")
        for naam, patroon in VERBODEN_IN_TEKST:
            m = re.search(patroon, beschrijving, re.I)
            eis(not m, f"{handle}: {naam} staat nog in de beschrijving ({m.group(0) if m else ''})")

        varianten = p.findall("variants/variant")
        eis(varianten, f"{handle}: geen varianten")
        opties = []
        for v in varianten:
            sku, _ = toets_regel(v, f"add-feed {handle}")
            skus.append(sku)
            opties.append((tekst(v, "option1"), tekst(v, "option2")))
        eis(len(opties) == len(set(opties)), f"{handle}: twee varianten met dezelfde opties {opties}")
        if any(o2 for _, o2 in opties):
            eis(tekst(p, "option2_name"), f"{handle}: option2 gevuld maar geen option2_name")

    eis(len(handles) == len(set(handles)),
        f"dubbele handle in de add-feed: {sorted({h for h in handles if handles.count(h) > 1})}")
    eis(len(skus) == len(set(skus)),
        f"dubbele SKU in de add-feed: {sorted({s for s in skus if skus.count(s) > 1})}")
    if update_skus:
        eis(set(skus) == set(update_skus),
            "add-feed en update-feed bevatten niet dezelfde SKU's: "
            f"alleen in add {sorted(set(skus) - set(update_skus))[:5]}, "
            f"alleen in update {sorted(set(update_skus) - set(skus))[:5]}")
    print(f"   add-feed: {len(handles)} producten, {len(skus)} varianten")
    return skus


def toets_plat(add_skus):
    """De platte add-feed draagt exact dezelfde varianten als de geneste.

    Stock Sync slaat een `<variants>` met één `<variant>` stil over (Kala, 24-09-2026).
    Raakt de platte vorm uit de pas met de geneste, dan mist er weer iets zonder
    foutmelding.
    """
    root = lees(PLAT)
    if root is None:
        return
    regels = root.findall("product")
    skus = [tekst(r, "sku") for r in regels]
    for r in regels:
        toets_regel(r, "platte add-feed")
        eis(tekst(r, "published") == "false", f"{tekst(r, 'sku')}: published moet 'false' zijn")
        eis(tekst(r, "handle").startswith(oc.HANDLE_PREFIX),
            f"{tekst(r, 'sku')}: handle mist het voorvoegsel")
    eis(len(skus) == len(set(skus)), "dubbele SKU in de platte feed")
    if add_skus:
        eis(set(skus) == set(add_skus),
            "platte en geneste add-feed dragen niet dezelfde SKU's")
    print(f"   platte add-feed: {len(regels)} regels over "
          f"{len({tekst(r, 'handle') for r in regels})} producten")


def toets_variantfotos():
    """Elke variant heeft een eigen foto, en die toont niet de smaak van een andere
    variant. Stock Sync koppelt `image` aan de variant; een verkeerde foto laat de
    klant een andere smaak zien dan hij kiest (Diet Vanille toonde Banaan, 30-09)."""
    root = lees(PLAT)
    if root is None:
        return
    per_handle = {}
    for r in root.findall("product"):
        per_handle.setdefault(tekst(r, "handle"), []).append(r)
    fout, galerij = 0, {}
    for handle, regels in per_handle.items():
        tokens = {tekst(r, "sku"): oc.smaak_tokens(tekst(r, "option1")) for r in regels}
        for r in regels:
            sku, foto = tekst(r, "sku"), tekst(r, "image")
            eis(bool(foto), f"{sku}: geen variantfoto")
            # Stock Sync hangt de EERSTE URL van de rij aan de variant.
            links = tekst(r, "image_links").split(",")
            eis(links[0] == foto, f"{sku}: image_links begint niet met de variantfoto "
                                  f"(dan krijgt deze smaak de foto van een andere)")
            eis(len(links) == len(set(links)), f"{sku}: dubbele foto in image_links")
            galerij.setdefault(handle, []).append(frozenset(links))
            if len(regels) < 2:
                continue
            anderen = set().union(*(t for s, t in tokens.items() if s != sku)) - tokens[sku]
            if oc.foto_klopt_niet(oc._bestandsnaam(foto), tokens[sku], anderen):
                fout += 1
                eis(False, f"{sku}: variantfoto {oc._bestandsnaam(foto)} toont een andere smaak")
    for handle, sets in galerij.items():
        eis(len(set(sets)) == 1, f"{handle}: niet elke regel draagt dezelfde galerij "
                                 f"(alleen de volgorde mag verschillen)")
    print(f"   variantfoto's: {sum(len(r) for r in per_handle.values())} gecontroleerd, "
          f"{fout} met de smaak van een andere variant")


def toets_verantwoording(feed_skus, met_teksten=True):
    """De zeef mag niets stil weglaten: feed + overgeslagen = de hele catalogus."""
    if not TELLING.exists() or not OVERGESLAGEN.exists():
        fouten.append("orangefit_telling.json of orangefit_overgeslagen.csv ontbreekt")
        return
    t = json.loads(TELLING.read_text(encoding="utf-8"))
    rijen = list(csv.DictReader(OVERGESLAGEN.open(encoding="utf-8")))
    eis(all(r["reden"] for r in rijen), "een overgeslagen variant zonder reden in de CSV")
    eis(len(rijen) == t["overgeslagen_varianten"],
        f"CSV heeft {len(rijen)} overgeslagen, telling zegt {t['overgeslagen_varianten']}")
    eis(t["feed_varianten"] == len(feed_skus),
        f"telling zegt {t['feed_varianten']} in de feed, de feed heeft er {len(feed_skus)}")
    eis(t["feed_varianten"] + t["overgeslagen_varianten"] == t["bron_varianten"],
        f"{t['feed_varianten']} + {t['overgeslagen_varianten']} != {t['bron_varianten']}")
    print(f"   {t['feed_varianten']} varianten in de feed + {t['overgeslagen_varianten']} "
          f"overgeslagen = {t['bron_varianten']} in Orangefits catalogus")

    if not met_teksten:
        return
    if not GESCHRAPT.exists() or not BRON.exists():
        fouten.append("orangefit_geschrapt.csv of orangefit_tekstbron.csv ontbreekt")
        return
    geschrapt = list(csv.DictReader(GESCHRAPT.open(encoding="utf-8")))
    eis(all(r["reden"] and r["fragment"] for r in geschrapt),
        "een geschrapt fragment zonder reden of zonder tekst")
    bron = list(csv.DictReader(BRON.open(encoding="utf-8")))
    eis(len(bron) == t["feed_producten"],
        f"tekstbron-CSV heeft {len(bron)} regels, telling {t['feed_producten']} producten")
    print(f"   {len(geschrapt)} stukken winkelpraat geschrapt, allemaal met reden")


def main():
    """`--alleen-update` slaat de add-feed over (die draait wekelijks, de update
    dagelijks; vergelijken zou struikelen over iets wat geen fout is)."""
    alleen_update = "--alleen-update" in sys.argv
    print("Invarianten van de Orangefit-feeds\n")
    skus = toets_update()
    if not alleen_update:
        add_skus = toets_add(skus)
        toets_plat(add_skus)
        toets_variantfotos()
    toets_verantwoording(skus, met_teksten=not alleen_update)

    if fouten:
        print(f"\n{len(fouten)} probleem/problemen:")
        for f in fouten:
            print(f"  - {f}")
        sys.exit(1)
    print(f"\nAlles klopt: {len(skus)} varianten, geen dubbelen, geen prijs 0, "
          f"geen winkelpraat van Orangefit in de teksten.")


if __name__ == "__main__":
    main()
