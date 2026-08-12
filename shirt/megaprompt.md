# Megaprompt — חיפוש חנויות פרמיום במחירי חיסול באליאקספרס

**איך משתמשים:** העתק את כל הבלוק באנגלית למטה והדבק אותו ב‑AI עם גלישה חיה —
ChatGPT (עם browsing), Perplexity, או Claude עם web search. ה‑AI יחזיר טבלת
חנויות עם קישורים ישירים, מחירים נוכחיים ואחוזי הנחה.

**מה לצפות לקבל:** לפחות 10 חנויות מדורגות, עם 2–3 פריטים לדוגמה מכל אחת,
ראיות איכות (GSM, תמונות ביקורות), ומחיר משלוח לישראל.

**טיפ:** תריץ את זה מחדש פעם בשבוע–שבועיים. חיסולי מלאי מתחלפים מהר,
והתוצאות של היום לא יהיו שם בעוד חודש.

---

## The prompt — copy everything below

```
Act as a professional apparel sourcing researcher with live web access.

GOAL
Find actual AliExpress STORES (not single listings) that sell clothing at the
quality level of premium European brands — but at minimal prices. I am hunting
for the cheap end of genuinely good production:

- Past-season leftovers and end-of-line stock
- Factory overruns and cancelled-order surplus
- Sample pieces / "sample sale" items
- Unbranded or de-branded blanks from factories that manufacture for premium
  brands (heavyweight fabrics, proper construction, no logo)

I am NOT looking for counterfeit branded goods. No fake logos, no replica
brand items. Unbranded quality only.

WHAT "PREMIUM QUALITY" MEANS HERE
- Heavyweight cotton: 250 gsm or more for tees/sweats (listing must state GSM)
- 100% cotton or clearly stated composition — no vague "polyester blend"
- Construction details mentioned: side-seamed, 2x2 rib collar, double-needle
  hems, drop-shoulder / boxy cuts, pre-shrunk or washed fabric
- Real customer review PHOTOS showing fabric thickness and stitching
- Store rating 95%+ with meaningful order history (not a store opened last month)
- "Factory store" / "official store" labels are a plus, but verify with reviews

WHAT TO HUNT FOR SPECIFICALLY
- Extreme clearance: search terms like "clearance", "last stock", "sample",
  "warehouse deal", "end of season", combined with "heavyweight cotton",
  "280gsm", "300gsm", "boxy tee", "oversized tee", "mock neck", "vintage wash"
- Store-wide sales and coupon stacking (store coupons + AliExpress coins/codes)
- AliExpress Choice items with free/cheap shipping to Israel
- Stores whose catalogue is NARROW (only tees/sweats/hoodies) — specialists,
  not general traders selling phone cases next to shirts

CONSTRAINTS
- Must ship to Israel
- Each order should stay UNDER $75 USD total (Israeli VAT exemption threshold)
- Prefer stores with buyer protection / free returns
- Prices: target $8–20 per heavyweight tee, $15–35 per heavyweight hoodie or
  sweatshirt. Flag anything claiming 280gsm below $6 as suspicious.

OUTPUT FORMAT — a ranked table with at least 10 stores:
| # | Store name | Store link | 2–3 example items (link + current price + discount %) | Shipping to Israel | Quality evidence found | Red flags |

After the table, add:
1. Your top 3 picks and one sentence why each
2. Which stores currently run store-wide clearance events
3. Search term combinations that produced the best results, so I can rerun
   them myself in the AliExpress app

RED FLAGS TO FILTER OUT
- "280gsm" claims at impossibly low prices (usually 180gsm mislabeled)
- Catalogue/model photos only, no real review photos
- Mixed random catalogue (electronics + clothes) = reseller, not factory outlet
- Any counterfeit brand logos — exclude these stores entirely
```
