"""
Orangefit UPDATE-feed
=====================
Lichte feed om BESTAANDE producten bij te werken: verkoopprijs + beschikbaarheid.
Match in Stock Sync bij voorkeur op barcode (EAN), anders op SKU (`OF-<sku>`).

  price     = consumentenprijs van orangefit.nl (incl. BTW), 1-op-1; bij een
              lopende actie de reguliere prijs (ORANGEFIT_PRIJS_BASIS=advies)
  available = op voorraad bij Orangefit (Shopify geeft geen aantal)
  GEEN beschrijving — die staat alleen in de add-feed, zodat een update nooit
  de eigen productteksten van Good For You overschrijft.

Bron: checkout.orangefit.nl (openbare Shopify). Zie orangefit_common.py.
Lokaal: INSECURE_SSL=1, TEST_HANDLE=<handle>.
"""

import time
import xml.etree.ElementTree as ET
from xml.dom import minidom

import orangefit_common as oc

OUTPUT_FILE = "orangefit_feed.xml"
FEED_URL = ("https://raw.githubusercontent.com/Maximillian-creator/orangefit-feed/"
            "main/orangefit_feed.xml")


def add(parent, tag, waarde):
    el = ET.SubElement(parent, tag)
    el.text = "" if waarde is None else str(waarde)
    return el


def build_xml(producten):
    root = ET.Element("products")
    for p in producten:
        for v in p["varianten"]:
            item = ET.SubElement(root, "product")
            add(item, "sku", v["sku"])
            add(item, "sku_leverancier", v["sku_leverancier"])
            add(item, "barcode", v["barcode"])
            add(item, "title", p["titel"])
            add(item, "variant_title", v["variant_titel"])
            add(item, "handle", p["handle"])
            add(item, "option1", v["optie1"])
            add(item, "option2", v["optie2"])
            add(item, "price", f"{v['prijs']:.2f}")
            add(item, "compare_at_price", "")     # nooit verzonnen
            add(item, "available", "true" if v["available"] else "false")
            add(item, "in_actie", "true" if v["in_actie"] else "false")
            add(item, "prijs_actueel", f"{v['prijs_actueel']:.2f}")
    return root


def save_xml(root, filepath):
    xml_str = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(xml_str).toprettyxml(indent="  ")
    regels = pretty.split("\n")
    if regels[0].startswith("<?xml"):
        regels[0] = '<?xml version="1.0" encoding="UTF-8"?>'
    with open(filepath, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(regels))
    print(f"\nXML opgeslagen: {filepath}")


def main():
    print("Orangefit UPDATE-feed gestart\n")
    start = time.time()
    producten = oc.fetch_products(met_teksten=False)
    regels = sum(len(p["varianten"]) for p in producten)
    oc.controleer_omvang(regels, OUTPUT_FILE)
    save_xml(build_xml(producten), OUTPUT_FILE)
    print(f"Klaar in {time.time() - start:.0f}s — {len(producten)} producten, "
          f"{regels} varianten")
    print(f"\nFeed-URL voor Stock Sync (Update):\n{FEED_URL}")


if __name__ == "__main__":
    main()
