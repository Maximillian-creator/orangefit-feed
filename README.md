# Orangefit feed

Leveranciersfeed voor **Orangefit** (orangefit.nl, Alkmaar) → Stock Sync → Shopify.
Gebouwd 30-09-2026. Openbare bronnen, geen login, geen secrets.

| Feed | Bestand | Draait | Inhoud |
|---|---|---|---|
| **Update** | `orangefit_feed.xml` | 2×/dag (05:30 + 17:30 UTC) | prijs + beschikbaarheid, geen tekst |
| **Add** (plat, aanbevolen) | `orangefit_add_feed_plat.xml` | wekelijks (ma 04:30 UTC) | alles, één regel per variant |
| Add (genest) | `orangefit_add_feed.xml` | idem | alles, één product met `<variants>` |

Feed-URL's (zodra de repo op GitHub staat):

- `https://raw.githubusercontent.com/Maximillian-creator/orangefit-feed/main/orangefit_feed.xml`
- `https://raw.githubusercontent.com/Maximillian-creator/orangefit-feed/main/orangefit_add_feed_plat.xml`

## Wat erin zit (30-09-2026)

**17 producten, 38 varianten** van de 35 producten en 148 varianten in Orangefits
Shopify. 34 van de 38 hebben een EAN; de vier "Display (12 repen)"-varianten hebben
bij Orangefit zelf geen barcode.

Weg, met reden in `orangefit_overgeslagen.csv` (110 varianten):
pakketten zonder eigen SKU (Protein Pakket 36, Afslankpakket 49, Hero Pakket,
Vitaminepakket, Vezelpakket), marktplaats-displays, cadeaubonnen, receptenboeken,
shaker, tas, sokken, bewaarbus, gum. De test controleert dat feed + overgeslagen
precies de hele catalogus is.

## Bronnen

orangefit.nl is **headless Shopify**: voorkant Next.js + DatoCMS, catalogus op
Shopify (`orangefitnl.myshopify.com` = `checkout.orangefit.nl`).

1. `checkout.orangefit.nl/products.json`: SKU, prijs, available, opties, afbeeldingen
2. `checkout.orangefit.nl/products/<handle>.json`: EAN per variant
3. `www.orangefit.nl/producten`: Shopify-id → productpagina
4. productpagina (`__NEXT_DATA__`): voordelen, ingrediënten + voedingswaarde per
   smaak, aminozuurprofiel, FAQ

## Keuzes (omkeerbaar, in code vastgelegd)

- **Prijs** = Orangefits consumentenprijs incl. BTW, 1-op-1. Loopt er een actie
  (compare_at > prijs), dan de reguliere prijs (`ORANGEFIT_PRIJS_BASIS=advies`,
  standaard; `actueel` volgt de actie). `compare_at_price` blijft altijd leeg.
- **Geen kostprijs**: de inkoopvoorwaarden bij Orangefit zijn onbekend.
- **SKU = `OF-<sku>`**: Orangefit gebruikt SKU's als `D3`, `O3`, `M3` en `PRO`, die
  gegarandeerd botsen. De kale SKU staat in `sku_leverancier`.
- **Handle = `orangefit-<handle>`**: `creatine` en `probiotica` bestaan bij GFY al
  als handle van een ander merk.
- **Titel = "Orangefit <titel>"**: "Magnesium" wordt "Orangefit Magnesium".
- **Producttype** = Orangefits type, behalve *Afvalshakes* → *Maaltijdvervangers*
  (het type gaat mee naar Google Shopping).
- **Optienamen** Flavour → Smaak, Grootte → Verpakking. De repen hebben twee
  opties (smaak + per stuk/display): map in Stock Sync óók `option2`.
- **Teksten** zijn letterlijk Orangefit, zonder winkelpraat. 60 fragmenten eruit
  (Trustpilot, Repeat-abonnement, pakketten, gratis verzending, geld-terug,
  "Nr. 1", "Consumentenbond", eigen prijzen). Elk fragment staat met reden in
  `orangefit_geschrapt.csv`.
- **`published` = altijd false.** Themis 30-09: 15 afkeuren / 1 let op / 1 ok
  (`orangefit_themis.md`). De teksten zijn in je-vorm en in Orangefits wij-vorm
  (tellingen in `orangefit_tekstbron.csv`) en moeten herschreven worden vóór
  publicatie.

## Stock Sync

**Add** (plat): parent node `products.product[*]`, variant node **leeg**,
identificeerder `sku`, variantgroep `handle`, optie 1 `option1`, optie 2 `option2`,
"varianten samenvoegen in bestaande producten" AAN. Alleen nieuwe producten aanmaken.

**Update**: match op **barcode** (34 van 38), anders op `sku`. Map `price` en
`available` (voorraadbeleid). **Niet** `description` of `title`.
**Store Product Filter: Vendor is in `Orangefit`**, en auto-archive op "No action"
tijdens de proef. Zonder vendorfilter rekent een remove-actie tegen de hele winkel
(zo gaat DS - AUTO CLEAR tegen alle 4.525 varianten).

## Vangnetten

- `controleer_omvang()`: 0 regels of minder dan de helft van de vorige feed →
  run stopt, vorige feed blijft staan (`FORCE_FEED=1` overrulet).
- Elke mislukte bron-URL is een fout, nooit een stil overgeslagen product.
- `test_feed.py` draait in elke Action vóór de commit; faalt hij, dan wordt er
  niets gepubliceerd.

## Lokaal

```bash
INSECURE_SSL=1 PYTHONIOENCODING=utf-8 python add_scraper.py
INSECURE_SSL=1 PYTHONIOENCODING=utf-8 python scraper.py
python test_feed.py
python themis_check.py        # alleen in "Claude Code Projecten" (gfy-themis ernaast)
```

Eén product: `TEST_HANDLE=hydrate`. Pagina's cachen: `ORANGEFIT_CACHE_DIR=.cache`.
