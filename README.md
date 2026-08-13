# field

אתר סטטי (GitHub Pages: `andreycherno.github.io/field`) שמאגד כמה תתי-פרויקטים. זהו קו הבנייה של הריפו — כל קובץ חדש צריך להשתייך לאחד מהאזורים האלה.

## מבנה

```
/                       דפי המחקר הראשיים (HTML סטטי, ללא build)
├── index.html          דף הבית
├── brands.html         סקירת מותגים
├── china.html          מותגים סיניים
├── sales.html          מבצעים
├── sourcing.html       סורסינג
│
├── shelf/              ״המדף״ — קטלוג הפריטים (הליבה של הפרויקט)
│   ├── index.html      ממשק הקטלוג הראשי
│   ├── brands.html     אינדקס מותגים
│   ├── super-sales.html
│   ├── items.json      כל הפריטים (40k+)
│   ├── brands.json / activities.json / landed-text.json /
│   │   shipping-policies.json / size-guides.json   נתוני עזר לדפים
│   ├── f/              נתוני קטלוג דחוסים (0.json אינדקס, 1.json גוף)
│   ├── img/<retailer>/ תמונות מקומיות, נתיב: img/<חנות>/<hash>.webp
│   ├── sitemap.xml, og.png   SEO
│
└── brand/print/        פרויקט נפרד: ארטוורק דפוס Meridian
    ├── make_artwork.py     מחולל ה-SVG/PDF (מקור האמת)
    ├── verify_thickness.py בדיקת עובי קווים מינימלי לדפוס
    └── *.svg / *.pdf       תוצרים
```

## כללים

- כל תמונה ב-`shelf/img/` חייבת להיות מופנית מ-`items.json`, מדפי ה-HTML או מ-`shelf/f/*.json`. תמונות ללא הפניה — למחיקה.
- אין להוסיף פרויקט חדש לשורש; פרויקט חדש מקבל תיקייה משלו (כמו `brand/`).
- `.nojekyll` נדרש ל-GitHub Pages — לא למחוק.
