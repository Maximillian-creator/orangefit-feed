"""
Orangefit ADD-feed
==================
Volledige productinfo om met Stock Sync NIEUWE producten aan te maken.
Bron: orangefit.nl (openbare headless Shopify + de productpagina's).

  price       = consumentenprijs van orangefit.nl (incl. BTW), 1-op-1
  cost        = NIET in de feed — inkoopvoorwaarden Orangefit onbekend
  barcode     = EAN per variant uit Shopify, alleen als het controlecijfer klopt
  sku         = OF-<sku van Orangefit>; de kale SKU staat in sku_leverancier
  handle      = orangefit-<handle>, nooit kaal (creatine/probiotica bestaan al)
  title       = "Orangefit <titel>"
  description = samenvatting + voordelen + ingredienten/voedingswaarde per smaak +
                FAQ, letterlijk van orangefit.nl, zonder winkelpraat
  published   = ALTIJD false. Bouwen mag, publiceren verdien je: elke tekst moet
                eerst langs Themis en naar de u-vorm. Zie themis_check.py.

Twee vormen, dezelfde inhoud:
  orangefit_add_feed_plat.xml — één <product> per variant (AANBEVOLEN; Stock Sync
                                slaat een <variants> met één <variant> stil over)
  orangefit_add_feed.xml      — genest, één <product> met <variants>

Zet in Stock Sync de ADD-koppeling op "alleen nieuwe producten aanmaken".
Lokaal: INSECURE_SSL=1, TEST_HANDLE=<handle>, ORANGEFIT_CACHE_DIR=.cache.
"""

import csv
import re
import time
import xml.etree.ElementTree as ET
from xml.dom import minidom

import orangefit_common as oc

OUTPUT_FILE = "orangefit_add_feed.xml"
PLAT_FILE = "orangefit_add_feed_plat.xml"
BRON_FILE = "orangefit_tekstbron.csv"
REPO_RAW = "https://raw.githubusercontent.com/Maximillian-creator/orangefit-feed/main/"


def add(parent, tag, waarde):
    el = ET.SubElement(parent, tag)
    el.text = "" if waarde is None else str(waarde)
    return el


def _productvelden(item, p):
    add(item, "handle", p["handle"])
    add(item, "title", p["titel"])
    add(item, "vendor", p["vendor"])
    add(item, "brand", p["vendor"])
    add(item, "product_type", p["product_type"])
    add(item, "tags", p["tags"])
    add(item, "published", "false")          # concept-only, altijd
    add(item, "description", p["beschrijving"])
    add(item, "option1_name", p["optie1_naam"])
    add(item, "option2_name", p["optie2_naam"])
    add(item, "leverancier_url", p["url"])
    add(item, "leverancier_handle", p["leverancier_handle"])


def _variantvelden(el, v, eerste):
    add(el, "sku", v["sku"])
    add(el, "sku_leverancier", v["sku_leverancier"])
    add(el, "barcode", v["barcode"])
    add(el, "price", f"{v['prijs']:.2f}")
    add(el, "compare_at_price", "")          # nooit verzonnen
    add(el, "available", "true" if v["available"] else "false")
    add(el, "variant_title", v["variant_titel"])
    add(el, "option1", v["optie1"])
    add(el, "option2", v["optie2"])
    add(el, "weight", v["gewicht"])
    add(el, "weight_unit", "g")
    add(el, "image", v["afbeelding"] or eerste)


def build_plat_xml(producten):
    """Eén <product>-regel per variant — de vorm die Stock Sync betrouwbaar leest.

    Bij Kala kwamen op 24-09-2026 zes producten niet binnen: precies de zes met één
    variant, want Stock Sync leest een `<variants>` met één `<variant>` als los object
    en slaat de rij stil over. Orangefit heeft er tien met één variant (30-09-2026).

    `image_links` begint per regel met de foto van DIE variant, daarna de rest van
    de galerij. Stock Sync ("koppel de afbeelding aan de variant wanneer de URL op
    dezelfde rij staat") hangt de eerste URL van de rij aan de variant; met overal
    dezelfde volgorde kreeg elke smaak de eerste foto (import 30-09-2026). De
    galerij van het product blijft compleet: elke regel draagt alle foto's.
    """
    root = ET.Element("products")
    for p in producten:
        eerste = p["afbeeldingen"][0] if p["afbeeldingen"] else ""
        for v in p["varianten"]:
            eigen = v["afbeelding"] or eerste
            links = [eigen] + [a for a in p["afbeeldingen"] if a != eigen]
            item = ET.SubElement(root, "product")
            _productvelden(item, p)
            add(item, "image_links", ",".join(a for a in links if a))
            _variantvelden(item, v, eerste)
    return root


def build_xml(producten):
    root = ET.Element("products")
    for p in producten:
        item = ET.SubElement(root, "product")
        _productvelden(item, p)
        images_el = ET.SubElement(item, "images")
        for src in p["afbeeldingen"]:
            add(ET.SubElement(images_el, "image"), "src", src)
        add(item, "image_links", ",".join(p["afbeeldingen"]))
        eerste = p["afbeeldingen"][0] if p["afbeeldingen"] else ""
        variants_el = ET.SubElement(item, "variants")
        for v in p["varianten"]:
            _variantvelden(ET.SubElement(variants_el, "variant"), v, eerste)
    return root


_WIJ = re.compile(r"\b(we|wij|ons|onze)\b", re.I)
_JIJ = re.compile(r"\b(je|jij|jou|jouw|jezelf)\b", re.I)


def schrijf_tekstbron(producten, pad=BRON_FILE):
    """Per product: waar de tekst vandaan komt, hoe lang hij is, en in wiens stem.

    `wij_vorm` en `je_vorm` tellen hoe vaak Orangefit over zichzelf praat ("we doen
    niet aan...") en de klant tutoyeert. Beide moeten eruit voordat de tekst op
    Good For You staat: daar is "wij" Good For You, en klanten krijgen de u-vorm.
    """
    with open(pad, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["handle", "titel", "onderdelen", "tekens_tekst", "afbeeldingen",
                    "wij_vorm", "je_vorm", "leverancier_url"])
        for p in producten:
            tekst = oc.plat(p["beschrijving"])
            w.writerow([p["handle"], p["titel"], "|".join(p["onderdelen"]), len(tekst),
                        len(p["afbeeldingen"]), len(_WIJ.findall(tekst)),
                        len(_JIJ.findall(tekst)), p["url"]])
    print(f"   Tekstherkomst vastgelegd in {pad}")


def save_xml(root, filepath):
    xml_str = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(xml_str).toprettyxml(indent="  ")
    regels = pretty.split("\n")
    if regels[0].startswith("<?xml"):
        regels[0] = '<?xml version="1.0" encoding="UTF-8"?>'
    with open(filepath, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(regels))
    print(f"XML opgeslagen: {filepath}")


def main():
    print("Orangefit ADD-feed gestart\n")
    start = time.time()
    producten = oc.fetch_products(met_teksten=True)
    regels = sum(len(p["varianten"]) for p in producten)
    oc.controleer_omvang(regels, OUTPUT_FILE)
    schrijf_tekstbron(producten)
    print()
    save_xml(build_xml(producten), OUTPUT_FILE)
    save_xml(build_plat_xml(producten), PLAT_FILE)
    print(f"\nKlaar in {time.time() - start:.0f}s — {len(producten)} producten, "
          f"{regels} varianten")
    print("\nFeed-URL voor Stock Sync (Add products):")
    print(f"  plat, 1 regel per variant (AANBEVOLEN): {REPO_RAW}{PLAT_FILE}")
    print(f"  genest, 1 regel per product:            {REPO_RAW}{OUTPUT_FILE}")
    print("\nLet op: published staat op false. Draai `python themis_check.py` "
          "voordat er iets in Shopify op zichtbaar gaat.")


if __name__ == "__main__":
    main()
