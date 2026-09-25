---
name: jot
description: >
  Čte a upravuje běžící poznámku nebo tabulku jot přes sdílený odkaz: u poznámky
  stáhne markdown, aplikuje úpravy a odpovídá ve vláknech komentářů, u tabulky
  čte dotazem a zapisuje dávkou operací. Use when the user invokes
  the jot skill, runs /jot, says „skill jot“ or „pomocí skillu jot“, or asks
  to work on a jot share URL (http://…/s/<id>).
---

# jot

Poznámku nebo tabulku upravuj přes HTTP API běžící instance. Sdílený odkaz je v zadání uživatele. Jméno v komentářích je `Grok`, pokud uživatel neřekne jiné.

Instance už musí běžet. Tento skill ji nespouští.

## Vstup

Z adresy `http(s)://<host>/s/<shareId>` vezmi původ (schéma, host, port) a `shareId`. Segment za `/s/` je celý ticket. Může obsahovat `A–Z`, `a–z`, `0–9`, `-` a `_`, a do API se dává tak, jak v adrese je. Další cesta, dotaz ani kotva do volání nepatří.

Když adresa v zadání není, zeptej se na ni a nic nevolej.

## Postup

1. `GET /api/share/<shareId>`. Když vrátí poznámku, pracuj s tím, co vrátí teď.
2. Když vrátí `404`, zkus `GET /api/share/<shareId>/data`. Když ten projde, odkaz vede na tabulku a platí sekce [Tabulka](#tabulka).
3. Když `404` vrátí obojí, odkaz je špatný, prošlý, nebo sdílení vypnuli. Odkaz platí 24 až 48 hodin a nový vydá jen vlastník. Řekni to uživateli a nic dalšího nezkoušej.
4. U poznámky zvol operace podle `note.shareAccess`.
5. Proveď jen změnu, kterou uživatel zadal. Text, o kterém nemluvil, nech beze změny.
6. Když změna plyne z komentáře, po ní na to vlákno odpověz.
7. Znovu načti poznámku nebo tabulku a teprve podle toho uživateli řekni, co v ní je.

`canReply`, `canEdit` a `canResolve` v odpovědi popisují prohlížečovou session. Na rozhodnutí, jestli API volání projde, neslouží.

## Čtení

```bash
curl -sS "$ORIGIN/api/share/$SHARE_ID"
```

Ber `note.markdown` jako zdroj textu, `note.title` jako název, `threads` jako komentáře. U vlákna čti `id`, `resolved`, `anchor.quote` a u zpráv `id`, `authorName`, `body`.

## Úprava textu

Chce to `shareAccess` `edit`.

```bash
curl -sS -X POST "$ORIGIN/api/share/$SHARE_ID/edit" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/jot-edit.json
```

Tělo:

```json
{"edits":[{"oldText":"přesný jedinečný úsek markdownu","newText":"nový úsek"}]}
```

- `oldText` je přesný podřetězec pole `markdown`, včetně mezer a nových řádků. Ne text z vyrenderovaného HTML.
- `oldText` nesmí být prázdný a v aktuálním markdownu musí být právě jednou. Když je tam víckrát, přidej okolní kontext, dokud není jedinečný.
- Prázdný `newText` úsek smaže.
- Víc položek v `edits` se aplikuje postupně. Když jedna selže, neuloží se nic.
- Text na konec přidej tak, že jedinečný konec poznámky nahradíš týmž koncem plus novým odstavcem.
- Prázdnou poznámku tímto endpointem nejde naplnit. Řekni to uživateli.
- Název poznámky ani úroveň sdílení tenhle odkaz nemění.

Když `oldText` obsahuje uvozovky nebo nové řádky, JSON zapiš do souboru v `/tmp` a pošli ho přes `--data-binary`. Do repozitáře ten soubor nepatří.

## Komentáře

Čtení a nové komentáře chtějí aspoň `comment`. Úpravy textu dál chtějí `edit`.

Odpověď na existující zprávu:

```bash
curl -sS -X POST "$ORIGIN/api/share/$SHARE_ID/threads/$THREAD_ID/replies" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/jot-reply.json
```

```json
{"parentMessageId":"<id zprávy>","name":"Grok","body":"text odpovědi"}
```

Nový komentář ukotvi na úryvek viditelného textu. `quote` musí v poznámce zůstat k nalezení; když ten úsek přepíšeš, vlákno ztratí místo. `start` a `end` jsou indexy `quote` v poli `markdown` (`end` je pozice za úryvkem). Okolních asi 40 znaků dej do `prefix` a `suffix`.

```json
{"name":"Grok","body":"text komentáře","anchor":{"quote":"úryvek","prefix":"text před ","suffix":" text za","start":10,"end":16}}
```

```bash
curl -sS -X POST "$ORIGIN/api/share/$SHARE_ID/threads" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/jot-comment.json
```

`name` posílej v každém požadavku. Vyřešení ani smazání vlákna přes sdílený odkaz nedělej; to zůstává vlastníkovi poznámky.

## Tabulka

Tabulka má sloupce se jmény a řádky s id. Buňka je vždy text: `001` zůstane `001` a porovnání je textové. Úroveň `view` jen čte, `edit` čte i zapisuje.

### Čtení

```bash
curl -sS -G "$ORIGIN/api/share/$SHARE_ID/data" --data-urlencode "q=SELECT jméno, stav WHERE stav = 'open' ORDER BY jméno LIMIT 50"
```

Bez `q` přijde celá tabulka. Odpověď má `version`, `columns` (jména), `columnIds` (jméno → id sloupce), `rows` a `truncated`. Každý řádek má pole `id` a hodnoty podle jmen sloupců. S `format=csv` přijde CSV, první sloupec `_id` a verze v hlavičce `X-Jot-Version`.

`q` je jedna věta: `SELECT` se seznamem sloupců nebo `*`, volitelně `WHERE`, `ORDER BY` (`ASC`, `DESC`) a `LIMIT`. Podmínky znají `=`, `<>`, `<`, `<=`, `>`, `>=`, `LIKE` (`%` a `_`), `AND`, `OR` a závorky. Hodnota je v apostrofech. Jméno s mezerou dej do uvozovek: `"jednotková cena"`. Jména sloupců rozlišují velká a malá písmena. `_id` se smí filtrovat i řadit. Neznámý sloupec a jiná syntax vrátí `400`.

Nad 2000 řádků server celou tabulku nevydá. Zužuj `WHERE` nebo `LIMIT`, dokud se výsledek nevejde.

### Zápis

Chce to `shareAccess` `edit`. Nejdřív si přečti `version` a pošli ji jako `baseVersion`.

```bash
curl -sS -X POST "$ORIGIN/api/share/$SHARE_ID/ops" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/jot-ops.json
```

```json
{"baseVersion": 12, "ops": [
  {"op": "insert_column", "name": "stav"},
  {"op": "insert_row", "values": {"jméno": "Ada", "stav": "open"}},
  {"op": "set", "row": "<id řádku>", "column": "stav", "value": "closed"}
]}
```

| `op` | pole |
| --- | --- |
| `insert_column` | `name`, volitelně `before` (id sloupce, jinak na konec) |
| `rename_column` | `name`, `newName` |
| `delete_column` | `name` |
| `move_column` | `name`, volitelně `before` (id sloupce, jinak na konec) |
| `resize_column` | `name`, `width` (40–2000 px, `null` vrátí výchozí) |
| `insert_row` | volitelně `before` (id řádku, jinak na konec), volitelně `values` podle jmen sloupců |
| `delete_row` | `row` |
| `move_row` | `row`, volitelně `before` (id řádku, jinak na konec) |
| `set` | `row`, `column`, `value` |

- `row` je id řádku, nebo `{"column": "sku", "value": "ABC"}`. Podmínka musí trefit právě jeden řádek.
- `name` a `column` jsou jména sloupců. `before` u sloupce je id z `columnIds`, u řádku `id` řádku.
- Operace se provádějí postupně. Když jedna selže, neuloží se nic a odpověď v `op` řekne, která to byla.
- Odpověď `409` znamená, že se tabulka mezitím změnila. Přečti ji znovu a zapiš jen to, co pořád platí. Stejný požadavek neopakuj.
- Úpravy sloupců (`insert_column`, `rename_column`, `delete_column`) projdou jen na aktuální verzi.

### Import CSV

Prázdnou tabulku bez sloupců a řádků naplníš celým souborem naráz. Tabulku, která už něco obsahuje, import odmítne a úpravy jdou dávkou.

```bash
curl -sS -X POST "$ORIGIN/api/share/$SHARE_ID/import-csv" \
  -H "Content-Type: text/csv; charset=utf-8" \
  -H "X-Jot-Base-Version: <verze>" \
  --data-binary @soubor.csv
```

První řádek jsou jména sloupců. Sloupec `_id` se zahodí. Nad 2000 řádků import neprojde.

## Po změně

Znovu načti `GET /api/share/<shareId>`, u tabulky `GET /api/share/<shareId>/data`. Ověř markdown a text vlákna, nebo řádky, které dávka měnila. Uživateli napiš, co se změnilo. Otevřený editor i mřížka dostanou změnu samy.
