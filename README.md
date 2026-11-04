# Фіди Meta для yapiko.ua

8 товарних фідів (RSS 2.0, `g:`) для каталогів Meta: 4 міста × «Сети» (5 позицій) / «Асорті» (5 позицій).

Щодня о 05:00 за Києвом GitHub Actions відкриває сайт у браузері, обирає місто, збирає з карток товарів назву, ціну, акційну ціну, наявність, фото й склад і перезаписує файли в `feeds/`. Ціни додатково звіряються з Google Shopping фідом міста (`feeds/report.txt`). Якщо товар не вдалося зібрати, файл не оновлюється, і лишається вчорашня версія.

| Місто | Сети | Асорті |
|---|---|---|
| Харків | `feeds/harkiv_sety.xml` | `feeds/harkiv_asorti.xml` |
| Київ | `feeds/kyiv_sety.xml` | `feeds/kyiv_asorti.xml` |
| Одеса | `feeds/odesa_sety.xml` | `feeds/odesa_asorti.xml` |
| Полтава | `feeds/poltava_sety.xml` | `feeds/poltava_asorti.xml` |

Склад фідів змінюється в `FEEDS` у `build_feeds.py`. Ручний запуск: Actions → Meta feeds → Run workflow.
