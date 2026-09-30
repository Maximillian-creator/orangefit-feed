"""
Orangefit — gedeelde kern
=========================
orangefit.nl is een **headless Shopify**-winkel: de voorkant is Next.js met teksten
uit DatoCMS, de kassa en de catalogus draaien op Shopify (`orangefitnl.myshopify.com`,
bereikbaar als `checkout.orangefit.nl`). Geen login nodig. Alles komt uit vier
openbare bronnen:

  1. checkout.orangefit.nl/products.json         -> catalogus: titel, type, opties,
                                                     afbeeldingen, varianten (sku,
                                                     prijs, compare_at, available)
  2. checkout.orangefit.nl/products/<handle>.json -> de EAN per variant (`barcode`;
                                                     staat niet in products.json)
  3. www.orangefit.nl/producten                  -> welke Shopify-id bij welke
                                                     productpagina hoort
  4. de productpagina zelf (`__NEXT_DATA__`)     -> de teksten: voordelen,
                                                     ingredienten en voedingswaarde
                                                     per smaak, FAQ

Bijzonderheden van deze winkel — waarom de code doet wat hij doet:

- **Handles krijgen het voorvoegsel `orangefit-`.** Orangefit noemt zijn producten
  kaal: `protein`, `diet`, `creatine`, `probiotica`, `magnesium`. `creatine` en
  `probiotica` bestaan bij Good For You al als handle van een ander merk (gemeten
  30-09-2026). Met "varianten samenvoegen in bestaande producten" AAN in Stock Sync
  zou Orangefit daar in schuiven. Zelfde les als bij Kala.
- **SKU's krijgen het voorvoegsel `OF-`.** Orangefit gebruikt SKU's van twee, drie
  tekens (`D3`, `O3`, `M3`, `PRO`). Numerieke en korte SKU's botsen tussen
  leveranciers (`01503` = Optimox bij ons, CinSulin bij Vitalized). De SKU van
  Orangefit zelf staat altijd in `sku_leverancier`.
- **Pakketten en marktplaats-displays vallen weg.** Die hebben geen eigen SKU, of
  een samengestelde code als `HV1000-HV1000-FS750|L2490-L2490-H0` (onderdelen plus
  lotnummers van Firmhouse). Zonder vaste SKU is een product na het aanmaken nooit
  meer bij te werken. Accessoires, cadeaubonnen, sokken en receptenboeken ook.
  Alles wat wegvalt staat met reden in `orangefit_overgeslagen.csv`.
- **Titels krijgen "Orangefit " ervoor** ("Magnesium" wordt "Orangefit Magnesium").
- Voorraad is alleen `available`, **geen aantal**. Er wordt geen aantal verzonnen.
- De teksten zijn letterlijk Orangefit, in hun eigen stem (je-vorm, "wij"), en
  ontdaan van winkelpraat: Trustpilot-cijfers, Repeat-abonnement, kortingen,
  pakketten, links. Wat eruit gaat staat in `orangefit_geschrapt.csv`. Wat blijft
  moet nog langs Themis en herschreven worden naar de u-vorm vóór publicatie.

Prijsbeleid (env `ORANGEFIT_PRIJS_BASIS`):
  "advies"  = de reguliere prijs; loopt er een actie (compare_at > prijs), dan
              de compare_at-prijs (STANDAARD)
  "actueel" = de vandaag getoonde prijs, inclusief lopende actie
Er wordt nooit een compare_at_price verzonnen: die blijft leeg in de feed.
Er is geen kostprijs: de inkoopvoorwaarden bij Orangefit zijn (nog) onbekend.

Lokaal testen achter een SSL-onderscheppende proxy: INSECURE_SSL=1.
Een product testen: TEST_HANDLE=<shopify-handle>. Pagina's cachen: ORANGEFIT_CACHE_DIR=.cache
"""

import csv
import hashlib
import json
import os
import re
import time
from html import unescape

import requests

SHOP_URL = os.environ.get("ORANGEFIT_SHOP", "https://checkout.orangefit.nl").rstrip("/")
SITE_URL = os.environ.get("ORANGEFIT_SITE", "https://www.orangefit.nl").rstrip("/")
BRAND = "Orangefit"
HANDLE_PREFIX = "orangefit-"
SKU_PREFIX = "OF-"
REQUEST_DELAY = 0.4

OVERGESLAGEN_FILE = "orangefit_overgeslagen.csv"
GESCHRAPT_FILE = "orangefit_geschrapt.csv"
TELLING_FILE = "orangefit_telling.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GFY-OrangefitFeed/1.0)",
    "Accept-Language": "nl-NL,nl;q=0.9",
}

VERIFY_SSL = os.environ.get("INSECURE_SSL") != "1"
if not VERIFY_SSL:
    import urllib3
    urllib3.disable_warnings()

PRIJS_BASIS = os.environ.get("ORANGEFIT_PRIJS_BASIS", "advies").lower()
CACHE_DIR = os.environ.get("ORANGEFIT_CACHE_DIR", "")

# Shopify-producttypes van Orangefit die een levensmiddel of supplement zijn. Alles
# daarbuiten (Cadeaubon, Receptenboek, Tas, Shaker, en de typeloze gum, bewaarbus en
# sokken) valt weg — met reden in de CSV. Verschijnt er bij Orangefit een nieuw type,
# dan valt het eerst weg en staat het zichtbaar in de CSV tot het hier bij komt.
# Waarde = het producttype bij Good For You. Alleen "Afvalshakes" krijgt een andere
# naam: het producttype gaat mee naar Google Shopping, en "afval-" is daar een
# gewichtsclaim. "Maaltijdvervangers" is Orangefits eigen collectienaam voor
# dezelfde producten.
VOEDING_TYPES = {
    "Protein": "Protein",
    "Afvalshakes": "Maaltijdvervangers",
    "Maaltijdshakes": "Maaltijdshakes",
    "Maaltijdrepen": "Maaltijdrepen",
    "Eiwitrepen": "Eiwitrepen",
    "Vitamines": "Vitamines",
    "Creatine": "Creatine",
    "Overnight Oats": "Overnight Oats",
    "Elektrolyten": "Elektrolyten",
    "Collageen": "Collageen",
    "Green Juice": "Green Juice",
    "Vezels": "Vezels",
}

# Optienamen zijn bij Orangefit half Engels. De waarden (Vanille, Lemon Orange)
# blijven zoals ze zijn: dat zijn Orangefits smaaknamen.
OPTIE_NAMEN = {"Flavour": "Smaak", "Size": "Inhoud", "Grootte": "Verpakking"}

# Een echte artikelcode: letters/cijfers met streepjes. Geen spaties, geen `|`
# (Firmhouse-pakketcode met lotnummers), geen herhaling van onderdelen.
SKU_RE = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _cache_pad(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".txt")


def _get(url, retries=3):
    """Haal een URL op. Mislukt het na drie pogingen, dan een FOUT — nooit een
    stille `None`.

    Dat is het tweede lek van 31-08-2026: een productpagina die faalde werd stil
    overgeslagen, en wie toevallig ontbrak als Stock Sync de feed las, werd
    gearchiveerd. Een run die faalt laat de vorige feed staan; dat is veiliger.
    """
    if CACHE_DIR and os.path.exists(_cache_pad(url)):
        with open(_cache_pad(url), encoding="utf-8") as f:
            return f.read()
    for poging in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, verify=VERIFY_SSL)
            r.raise_for_status()
            tekst = r.text
            if CACHE_DIR:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(_cache_pad(url), "w", encoding="utf-8") as f:
                    f.write(tekst)
            return tekst
        except Exception as e:
            if poging < retries - 1:
                wacht = (poging + 1) * 10
                print(f"    !  {url} faalt ({e}), opnieuw over {wacht}s")
                time.sleep(wacht)
            else:
                raise RuntimeError(f"{url} faalt na {retries} pogingen: {e}") from e


_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def next_data(html, url=""):
    m = _NEXT_DATA.search(html)
    if not m:
        raise RuntimeError(f"Geen __NEXT_DATA__ in {url} — is orangefit.nl omgebouwd?")
    return json.loads(m.group(1))


# --------------------------------------------------------------------------- #
# Bronnen
# --------------------------------------------------------------------------- #
def fetch_catalogus():
    producten, pagina = [], 1
    while True:
        batch = json.loads(_get(f"{SHOP_URL}/products.json?limit=250&page={pagina}"))
        batch = batch.get("products", [])
        if not batch:
            break
        producten.extend(batch)
        if len(batch) < 250:
            break
        pagina += 1
        time.sleep(REQUEST_DELAY)
    return producten


def fetch_barcodes(handle):
    """{variant-id: EAN} uit /products/<handle>.json (products.json heeft geen barcode)."""
    data = json.loads(_get(f"{SHOP_URL}/products/{handle}.json"))["product"]
    return {v["id"]: (v.get("barcode") or "").strip() for v in data.get("variants", [])}


def fetch_paginakaart():
    """{shopify-product-id: (slug, eerste collectie-slug)} van het productoverzicht.

    De productpagina's hangen niet aan de Shopify-handle maar aan een eigen slug in
    een collectiemap (`/producten/eiwitshakes/vegan-protein` voor handle `protein`).
    Het overzicht noemt per pagina de Shopify-id; daarop koppelen we.
    """
    url = f"{SITE_URL}/producten"
    data = next_data(_get(url), url)
    kaart = {}

    def loop(o):
        if isinstance(o, dict):
            if o.get("productId") and o.get("slug") and isinstance(o.get("collection"), list):
                colls = [c.get("slug") for c in o["collection"] if c.get("slug")]
                if colls:
                    kaart.setdefault(str(o["productId"]), (o["slug"], colls[0]))
            for w in o.values():
                loop(w)
        elif isinstance(o, list):
            for w in o:
                loop(w)

    loop(data)
    if not kaart:
        raise RuntimeError(f"0 productpagina's gevonden op {url} — structuur gewijzigd?")
    return kaart


def fetch_pagina(slug, collectie):
    """Het product-object uit de Next.js-data van een productpagina."""
    url = f"{SITE_URL}/producten/{collectie}/{slug}"
    data = next_data(_get(url), url)
    try:
        product = data["props"]["pageProps"]["productSubscription"]["initialData"]["product"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"Geen productdata op {url}: {e}") from e
    if not product:
        raise RuntimeError(f"Lege productdata op {url}")
    return url, product


# --------------------------------------------------------------------------- #
# HTML schoonmaken
# --------------------------------------------------------------------------- #
_WEG_MET_INHOUD = re.compile(
    r"<(script|style|noscript|form|iframe|button|svg|select)\b.*?</\1\s*>", re.I | re.S)
_LOSSE_TAGS = re.compile(r"<(img|input|hr|video|source)\b[^>]*/?>", re.I)
_ANKER = re.compile(r"</?a\b[^>]*>", re.I)
_ATTRIBUUT = re.compile(r"(<[a-z0-9]+)\s[^>]*?(/?>)", re.I)
_TOEGESTAAN = ("p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "h2", "h3", "h4",
               "table", "thead", "tbody", "tr", "th", "td", "sup", "sub")
_ONBEKEND = re.compile(r"</?(?!(?:%s)\b)[a-z0-9]+\b[^>]*>" % "|".join(_TOEGESTAAN), re.I)
_LEGE_BLOKKEN = re.compile(r"<(p|li|ul|ol|h[2-4]|strong|b|em|i)>\s*</\1>", re.I)


def schoon_html(ruw):
    """Orangefits HTML zonder stijlen, klassen, links, div-wrappers en afbeeldingen.

    Er wordt niets herschreven — alleen weggehaald wat niet in een productpagina van
    Good For You hoort: inline stijlen, de `<div class="page">`-wrappers die uit een
    PDF zijn geplakt, en links terug naar orangefit.nl (de linktekst blijft).
    """
    h = unescape(ruw or "").replace("\xa0", " ")
    h = _WEG_MET_INHOUD.sub(" ", h)
    h = _LOSSE_TAGS.sub(" ", h)
    h = _ANKER.sub("", h)
    h = _ONBEKEND.sub(" ", h)                 # div, span, ... weg, inhoud blijft
    h = _ATTRIBUUT.sub(r"\1\2", h)            # <p class=".."> -> <p>
    h = re.sub(r"<b>", "<strong>", h, flags=re.I)
    h = re.sub(r"</b>", "</strong>", h, flags=re.I)
    for _ in range(4):
        h = _LEGE_BLOKKEN.sub("", h)
    h = re.sub(r"(\s*<br\s*/?>\s*){2,}", "<br>", h, flags=re.I)
    h = re.sub(r"[ \t]{2,}", " ", h)
    h = re.sub(r"\s*\n\s*", "\n", h)
    return h.strip()


def plat(html):
    """Alleen de tekst — om te tellen en te toetsen, niet om te publiceren."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html or ""))).strip()


# --------------------------------------------------------------------------- #
# Winkelpraat — wat van Orangefits winkel is, niet van het product
# --------------------------------------------------------------------------- #
# Alles wat hier uit de tekst gaat, komt in GESCHRAPT: (handle, reden, fragment).
# Zonder dat logboek zou de opschoning een stille zeef zijn.
GESCHRAPT = []

# Hele zinnen die over Orangefits winkel gaan, niet over het product. Op onze
# pagina zouden ze liegen: "met meer dan 5.000 reviews is onze Protein de Nr. 1"
# gaat over Orangefits reviews, niet over die van Good For You; "met Repeat krijg je
# 15% korting" bestaat bij ons niet.
WEREN_ZIN = [
    ("reviews van Orangefit", re.compile(
        r"trustpilot|\breviews?\b|beoordeeld|\{amount\}|\{score\}", re.I)),
    ("Orangefits klantgemeenschap", re.compile(r"orangefitters?\b", re.I)),
    # "Nr. 1 van Nederland", "Consumentenbond: beste uit de test" — een ranglijst of
    # testuitslag over Orangefit, niet door ons te controleren en met een datum.
    ("ranglijst of testuitslag", re.compile(
        r"\bnr\W{0,2}1\b|nummer 1\b|consumentenbond|beste uit de test|#1\b", re.I)),
    ("winkelactie van Orangefit", re.compile(
        r"\brepeat\b|abonnement|korting|gratis (?:shaker|verzending|cadeau|toegang|bij|"
        r"persoonlijk)|\bbestel|winkelmand|starterspakket|\bpakket\b|afslankpakket|"
        r"voedingsadvies|voedingsschema|adviesgesprek|\bshop\b", re.I)),
    ("bezorging van Orangefit", re.compile(
        r"verzend|in huis\b|22[.:]00|werkdag|bezorg|levertijd", re.I)),
    ("garantie van Orangefit", re.compile(
        r"geld terug|niet tevreden|tevredenheidsgarantie", re.I)),
    # Orangefits eigen prijs ("EUR 0,15 per dag", "t.w.v. EUR 49,90") klopt niet
    # meer zodra onze prijs afwijkt, en veroudert bij elke prijswijziging.
    ("prijs van Orangefit", re.compile(r"€\s?\d|\d\s?euro\b|t\.w\.v", re.I)),
    ("kruisverkoop", re.compile(
        r"probeer (?:dan |ook )*(?:een van )?onze|bekijk onze|ontdek onze", re.I)),
    ("contact of site van Orangefit", re.compile(
        r"hallo@orangefit|orangefit\.(?:nl|com|eu)|582[\s-]?2351|klantenservice|"
        r"check hier|klik hier|bekijk (?:hier|ook)", re.I)),
]

# Koppen waarvan het hele blok tot de volgende kop wegvalt: "Vezels kopen?",
# "Niet goed, geld terug", "Probeer de Hydrate!" — daaronder staat alleen
# bezorging, garantie en aanprijzing van Orangefits winkel.
SECTIES_WEREN = re.compile(r"\bkopen\b|geld terug|^probeer\b|bestellen", re.I)

# FAQ-vragen die over Orangefits winkel gaan: de hele vraag + antwoord valt weg.
WEREN_FAQ = re.compile(
    r"abonnement|repeat|pakket|voedingsadvies|begeleiding|bezorg|verzend|retour|betal|"
    r"bestel|korting|klantenservice|contact|lifetime support", re.I)

_BLOK = re.compile(r"<(p|li|h2|h3|h4|td)\b[^>]*>(.*?)</\1\s*>", re.I | re.S)
_KOP = re.compile(r"<h([2-4])\b[^>]*>(.*?)</h\1\s*>", re.I | re.S)
_ZIN = re.compile(r".*?[.!?]+(?=\s|$)\s*|.+$", re.S)
_TAG = re.compile(r"<(/?)([a-z0-9]+)\b[^>]*?(/?)>", re.I)

# Afkortingen waar de zin NIET eindigt. Zonder deze bescherming knipte "is onze
# vegan Protein de Nr. 1 van Nederland" na "Nr." en bleef "1 van Nederland en
# zelfs Europa." als losse zin staan.
_AFKORTING = re.compile(r"\b(?:nr|t\.w\.v|o\.a|bijv|ca|incl|excl|d\.w\.z|m\.b\.v|resp)\.",
                        re.I)
_PUNT = "․"   # one dot leader: ziet eruit als een punt, knipt geen zin


def _bescherm(tekst):
    return _AFKORTING.sub(lambda m: m.group(0).replace(".", _PUNT), tekst)


def _herstel(tekst):
    return tekst.replace(_PUNT, ".")


def _tags_sluiten(stuk):
    """Opent en sluit dit stuk tekst al zijn eigen tags? Anders mag het niet los weg."""
    stapel = []
    for sluit, naam, zelf in _TAG.findall(stuk):
        naam = naam.lower()
        if zelf or naam == "br":
            continue
        if sluit:
            if not stapel or stapel.pop() != naam:
                return False
        else:
            stapel.append(naam)
    return not stapel


def _schrap_zinnen(binnen, handle):
    """Alleen de zin met winkelpraat eruit, de rest van het blok blijft staan.

    Loopt er opmaak door de zin, dan valt het hele blok weg (met vermelding), zodat
    er nooit een kapotte tag achterblijft.
    """
    if not any(p.search(plat(binnen)) for _, p in WEREN_ZIN):
        return binnen
    houden = []
    for stuk in _ZIN.findall(_bescherm(binnen)):
        stuk = _herstel(stuk)
        reden = next((r for r, p in WEREN_ZIN if p.search(plat(stuk))), None)
        if not reden:
            houden.append(stuk)
            continue
        if ("<" in stuk or ">" in stuk) and not _tags_sluiten(stuk):
            GESCHRAPT.append([handle, reden + " (heel blok)", plat(binnen)[:200]])
            return ""
        GESCHRAPT.append([handle, reden, plat(stuk)[:200]])
    return "".join(houden)


def _schrap_secties(h, handle):
    """Een kop als "Vezels kopen?" plus alles eronder tot de volgende kop van
    hetzelfde of een hoger niveau."""
    while True:
        for m in _KOP.finditer(h):
            if not SECTIES_WEREN.search(plat(m.group(2))):
                continue
            niveau = int(m.group(1))
            eind = len(h)
            for volgende in _KOP.finditer(h, m.end()):
                if int(volgende.group(1)) <= niveau:
                    eind = volgende.start()
                    break
            GESCHRAPT.append([handle, "sectie over Orangefits winkel",
                              plat(h[m.start():eind])[:200]])
            h = h[:m.start()] + h[eind:]
            break
        else:
            return h


def weer_winkelpraat(h, handle=""):
    def _blok(m):
        nieuw = _schrap_zinnen(m.group(2), handle)
        if not plat(nieuw):
            return ""
        return m.group(0).replace(m.group(2), nieuw, 1)

    h = _schrap_secties(h, handle)
    h = _BLOK.sub(_blok, h)
    for _ in range(4):
        h = _LEGE_BLOKKEN.sub("", h)
    return h.strip()


# --------------------------------------------------------------------------- #
# De beschrijving
# --------------------------------------------------------------------------- #
def _smaak_hoort_erbij(label, smaken):
    """Hoort dit specificatie-blok bij een smaak die in de feed zit?

    De pagina van Protein toont ook de ingredienten van Koffie en Blueberry, die
    in de Shopify-catalogus niet (meer) als variant bestaan. Die tekst hoort niet
    op een product dat die smaak niet verkoopt.
    """
    if not label or not smaken:
        return True
    lab = re.sub(r"[^a-z]", "", label.lower())
    return any(lab and (lab in s or s in lab) for s in smaken)


def bouw_beschrijving(pagina, handle, smaken):
    """Samenvatting + voordelen + specificaties per smaak + FAQ, in de volgorde van
    de productpagina. Letterlijk Orangefit, alleen weggehaald — niets bijgeschreven.

    Geeft (html, onderdelen) terug; `onderdelen` zegt welke blokken er tekst
    leverden, zodat de tekstbron-CSV kan laten zien waar de tekst vandaan komt.
    """
    delen, onderdelen = [], []

    samenvatting = (pagina.get("sectionSummary") or {}).get("description") or ""
    if "{" in plat(samenvatting):
        GESCHRAPT.append([handle, "sjabloon met reviewtelling", plat(samenvatting)[:200]])
    else:
        stuk = weer_winkelpraat(schoon_html(samenvatting), handle)
        if plat(stuk):
            delen.append(stuk)
            onderdelen.append("samenvatting")

    voordelen = pagina.get("sectionBenefits") or {}
    stuk = weer_winkelpraat(schoon_html(voordelen.get("body") or ""), handle)
    lijst = []
    for item in voordelen.get("list") or []:
        kop, tekst = plat(item.get("heading")), plat(item.get("paragraph"))
        if kop or tekst:
            lijst.append(f"<li><strong>{kop}</strong>: {tekst}</li>" if kop else f"<li>{tekst}</li>")
    lijst_html = weer_winkelpraat("<ul>\n" + "\n".join(lijst) + "\n</ul>", handle) if lijst else ""
    if plat(stuk) or plat(lijst_html):
        # Ook de kop zelf kan winkelpraat zijn ("Nr. 1 Diet Shake, maar dan als Diet Bar").
        kop = weer_winkelpraat(f"<h2>{plat(voordelen.get('heading'))}</h2>", handle)
        delen.append((kop + "\n" if plat(kop) else "") + "\n".join(
            x for x in (stuk, lijst_html) if plat(x)))
        onderdelen.append("voordelen")

    specs = ((pagina.get("productSpecifications") or {}).get("productSpecification") or {})
    for groep in specs.get("groups") or []:
        blokken = []
        for item in groep.get("items") or []:
            label = plat(item.get("label"))
            if not _smaak_hoort_erbij(label, smaken):
                GESCHRAPT.append([handle, f"{plat(groep.get('label'))} van smaak die niet "
                                          f"in de catalogus staat", label])
                continue
            inhoud = weer_winkelpraat(schoon_html(item.get("content")), handle)
            if not plat(inhoud):
                continue
            blokken.append((f"<p><strong>{label}</strong></p>\n" if label else "") + inhoud)
        if blokken:
            delen.append(f"<h3>{plat(groep.get('label'))}</h3>\n" + "\n".join(blokken))
            onderdelen.append(plat(groep.get("label")).lower())

    faq = [s for s in pagina.get("sections") or [] if s.get("__typename") == "FaqRecord"]
    vragen = []
    for blok in (faq[0].get("blocks") or []) if faq else []:
        vraag, antwoord = plat(blok.get("question")), blok.get("answer") or ""
        if WEREN_FAQ.search(vraag):
            GESCHRAPT.append([handle, "FAQ over Orangefits winkel", vraag[:200]])
            continue
        antwoord = weer_winkelpraat(schoon_html(antwoord), handle)
        if plat(antwoord):
            vragen.append(f"<p><strong>{vraag}</strong></p>\n{antwoord}")
    if vragen:
        delen.append("<h3>Veelgestelde vragen</h3>\n" + "\n".join(vragen))
        onderdelen.append("faq")

    return "\n".join(delen), onderdelen


# --------------------------------------------------------------------------- #
# Normaliseren
# --------------------------------------------------------------------------- #
def geldige_ean(code):
    """EAN-8/12/13/14 met kloppend controlecijfer, anders leeg."""
    s = (code or "").strip()
    if not re.fullmatch(r"\d{8}|\d{12,14}", s):
        return False
    cijfers = [int(c) for c in s]
    controle = cijfers.pop()
    som = sum(c * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(cijfers)))
    return (10 - som % 10) % 10 == controle


def maak_handle(handle):
    return handle if handle.startswith(HANDLE_PREFIX) else HANDLE_PREFIX + handle


def maak_titel(titel):
    t = (titel or "").strip()
    return t if t.lower().startswith("orangefit") else f"{BRAND} {t}"


def bepaal_prijs(v):
    """(verkoopprijs, prijs vandaag, in actie?) volgens ORANGEFIT_PRIJS_BASIS."""
    prijs = round(float(v.get("price") or 0), 2)
    cmp = round(float(v.get("compare_at_price") or 0), 2)
    in_actie = cmp > prijs > 0
    if PRIJS_BASIS == "advies" and in_actie:
        return cmp, prijs, True
    return prijs, prijs, in_actie


def reden_product(p):
    pt = p.get("product_type") or ""
    if pt not in VOEDING_TYPES:
        return f"producttype '{pt or '(leeg)'}' is geen voedingsmiddel of supplement"
    if "marktplaats" in (p.get("handle") or ""):
        return "display voor marktplaatsen (bol e.d.), dubbel met de gewone variant"
    return None


def reden_variant(v):
    sku = (v.get("sku") or "").strip()
    if not sku:
        return "geen SKU (pakket of samenstelling van losse producten)"
    if not SKU_RE.fullmatch(sku):
        return f"SKU '{sku[:40]}' is een samengestelde pakketcode, geen artikel"
    if float(v.get("price") or 0) <= 0:
        return "prijs is 0"
    return None


def normaliseer_variant(v, barcodes, opties):
    sku = v["sku"].strip()
    prijs, actueel, in_actie = bepaal_prijs(v)
    ean = barcodes.get(v["id"], "")
    opts = [v.get("option1") or "", v.get("option2") or "", v.get("option3") or ""]
    return {
        "sku": SKU_PREFIX + sku,
        "sku_leverancier": sku,
        "barcode": ean if geldige_ean(ean) else "",
        "barcode_bron": ean,
        "prijs": prijs,
        "prijs_actueel": actueel,
        "in_actie": in_actie,
        "available": bool(v.get("available")),
        "variant_titel": v.get("title") or "",
        "optie1": opts[0],
        "optie2": opts[1] if len(opties) > 1 else "",
        "gewicht": v.get("grams") or "",
        "afbeelding": (v.get("featured_image") or {}).get("src", "")
        if isinstance(v.get("featured_image"), dict) else "",
        "shopify_id": v["id"],
    }


def normaliseer(p, varianten, pagina_url, pagina, met_teksten):
    opties = [o.get("name", "") for o in p.get("options", [])]
    smaken = {re.sub(r"[^a-z]", "", (v["optie1"] or "").lower()) for v in varianten}
    beschrijving, onderdelen = "", []
    if met_teksten:
        if pagina:
            beschrijving, onderdelen = bouw_beschrijving(pagina, p["handle"], smaken)
        if not plat(beschrijving) and p.get("body_html"):
            # Geen productpagina (of een lege): Shopify's eigen body_html als terugval.
            beschrijving = weer_winkelpraat(schoon_html(p["body_html"]), p["handle"])
            onderdelen = ["shopify body_html"] if plat(beschrijving) else []
    return {
        "handle": maak_handle(p["handle"]),
        "leverancier_handle": p["handle"],
        "titel": maak_titel(p.get("title")),
        "vendor": BRAND,
        "product_type": VOEDING_TYPES[p["product_type"]],
        "tags": ", ".join([BRAND, VOEDING_TYPES[p["product_type"]]]),
        "beschrijving": beschrijving,
        "onderdelen": onderdelen,
        "afbeeldingen": [i["src"] for i in p.get("images", []) if i.get("src")],
        "optie1_naam": OPTIE_NAMEN.get(opties[0], opties[0]) if opties else "Title",
        "optie2_naam": (OPTIE_NAMEN.get(opties[1], opties[1]) if len(opties) > 1 else ""),
        "url": pagina_url or f"{SHOP_URL}/products/{p['handle']}",
        "varianten": varianten,
    }


# --------------------------------------------------------------------------- #
# Zeef + telling — wat wegvalt, valt zichtbaar weg
# --------------------------------------------------------------------------- #
def schrijf_overgeslagen(rijen, pad=OVERGESLAGEN_FILE):
    with open(pad, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["shopify_id", "handle", "titel", "sku", "niveau", "reden"])
        w.writerows(rijen)
    print(f"   Overgeslagen vastgelegd in {pad} ({len(rijen)} regels)")


def schrijf_geschrapt(pad=GESCHRAPT_FILE):
    with open(pad, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["handle", "reden", "fragment"])
        w.writerows(GESCHRAPT)
    tel = {}
    for _, reden, _f in GESCHRAPT:
        tel[reden] = tel.get(reden, 0) + 1
    print(f"   {len(GESCHRAPT)} stukken winkelpraat geschrapt (vastgelegd in {pad}):")
    for reden, n in sorted(tel.items(), key=lambda x: -x[1]):
        print(f"      {n:>3}x {reden}")


# --------------------------------------------------------------------------- #
# Vangnet — een halve feed is gevaarlijker dan geen feed
# --------------------------------------------------------------------------- #
def controleer_omvang(aantal, vorig_bestand):
    """Stop de run bij een lege of gehalveerde feed.

    Stock Sync zet producten die niet in een feed staan op *gearchiveerd*, stil en
    zonder melding — in mei, juni en juli 2026 heeft dat drie keer een catalogus tot
    44 dagen onvindbaar gemaakt. Een scraper die na een wijziging aan orangefit.nl 0
    of de helft van de regels vindt, mag die uitkomst niet wegschrijven: dan blijft
    de vorige feed staan en wordt de Action rood. FORCE_FEED=1 overrulet bewust.

    Geteld op `<sku>` (varianten), zodat de grens in de geneste add-feed hetzelfde
    betekent als in de update-feed.
    """
    if os.environ.get("FORCE_FEED") == "1":
        print("   FORCE_FEED=1 — omvangcontrole overgeslagen")
        return
    if aantal == 0:
        raise SystemExit("STOP: 0 feed-regels gevonden — feed niet weggeschreven.")
    if not os.path.exists(vorig_bestand):
        return
    with open(vorig_bestand, encoding="utf-8") as f:
        vorig = f.read().count("<sku>")
    print(f"   {aantal} feed-regels nu, {vorig} in de vorige feed")
    if vorig and aantal < vorig / 2:
        raise SystemExit(
            f"STOP: {aantal} feed-regels tegenover {vorig} in de vorige feed — minder "
            f"dan de helft. Feed niet weggeschreven (FORCE_FEED=1 overrulet dit).")
    if vorig and aantal < vorig * 0.9:
        print(f"   LET OP: {aantal} van {vorig} regels ({aantal / vorig:.0%}) — flinke "
              f"daling, feed wel geschreven. Stock Sync archiveert wat ontbreekt.")


# --------------------------------------------------------------------------- #
# De hoofdroute
# --------------------------------------------------------------------------- #
def fetch_products(met_teksten=True):
    """De verkoopbare producten, genormaliseerd. De telling sluit altijd:
    elke variant uit de Shopify-catalogus staat in de feed of in de CSV met reden.
    """
    ruw = fetch_catalogus()
    totaal_varianten = sum(len(p.get("variants", [])) for p in ruw)
    print(f"   {len(ruw)} producten, {totaal_varianten} varianten in de Shopify-catalogus")

    overgeslagen, houden = [], []
    for p in ruw:
        reden = reden_product(p)
        if reden:
            for v in p.get("variants", []):
                overgeslagen.append([p["id"], p["handle"], p["title"], v.get("sku") or "",
                                     "product", reden])
            continue
        goed = []
        for v in p.get("variants", []):
            r = reden_variant(v)
            if r:
                overgeslagen.append([p["id"], p["handle"], p["title"], v.get("sku") or "",
                                     "variant", r])
            else:
                goed.append(v)
        if goed:
            houden.append((p, goed))

    test = os.environ.get("TEST_HANDLE")
    if test:
        houden = [(p, g) for p, g in houden if p["handle"] == test]

    kaart = fetch_paginakaart() if met_teksten else {}
    producten = []
    for i, (p, goed) in enumerate(houden, 1):
        barcodes = fetch_barcodes(p["handle"])
        time.sleep(REQUEST_DELAY)
        opties = [o.get("name", "") for o in p.get("options", [])]
        varianten = [normaliseer_variant(v, barcodes, opties) for v in goed]
        pagina_url, pagina = "", None
        if met_teksten and str(p["id"]) in kaart:
            slug, coll = kaart[str(p["id"])]
            pagina_url, pagina = fetch_pagina(slug, coll)
            time.sleep(REQUEST_DELAY)
        prod = normaliseer(p, varianten, pagina_url, pagina, met_teksten)
        producten.append(prod)
        extra = (f" - {len(plat(prod['beschrijving']))} tekens tekst"
                 f" ({', '.join(prod['onderdelen']) or 'GEEN'})") if met_teksten else ""
        print(f"  [{i}/{len(houden)}] {prod['titel'][:36]:<36} {len(varianten)} var.{extra}")

    in_feed = sum(len(p["varianten"]) for p in producten)
    if not test:
        # Telling: elke variant is of in de feed, of met reden overgeslagen.
        if in_feed + len(overgeslagen) != totaal_varianten:
            raise SystemExit(
                f"STOP: telling sluit niet — {in_feed} in de feed + {len(overgeslagen)} "
                f"overgeslagen != {totaal_varianten} in de catalogus")
        schrijf_overgeslagen(overgeslagen)
        with open(TELLING_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "bron_producten": len(ruw),
                "bron_varianten": totaal_varianten,
                "feed_producten": len(producten),
                "feed_varianten": in_feed,
                "overgeslagen_varianten": len(overgeslagen),
            }, f, indent=2)
        print(f"   Telling: {in_feed} varianten in de feed + {len(overgeslagen)} "
              f"overgeslagen = {totaal_varianten} in de catalogus")
    if met_teksten:
        schrijf_geschrapt()
    return producten
