# jotpy

Python přepis serveru [jot](https://github.com/mariozechner/jot) (MIT, Mario Zechner / `@mariozechner/jot`).

Prohlížeč (`public/`) je z původního projektu, navíc má tabulky (`sheet.js`). Docker součástí není. Formát dat v `data/` je kompatibilní s TypeScript serverem (JSON poznámky, odvozený markdown, `auth.json` se scrypt hashi).

Pořadí znaků v editoru drží jednoduchá verze knihovny [articulated](https://github.com/mweidner037/articulated) (MIT, Matthew Weidner): pole a splice se stejným `save()` jako `IdList` 1.3.1, ne její strom.

## Instalace

```bash
uv sync
```

## Spuštění

```bash
uv run jot
uv run jot --port=3210 --data=./data
```

Port bere `--port=` nebo proměnnou `PORT`, výchozí je 3210. Adresář dat bere `--data=` nebo `DATA_DIR`, výchozí je `./data` vůči aktuálnímu adresáři.

## Tabulky

Vedle poznámek leží tabulky v `data/sheets/<id>.json`, s kopií `<id>.csv` pro čtení. Buňka je text.

Čtení je `GET /api/sheets/:id/data`, volitelně s `q` (jedna věta `SELECT`) a `format=csv`. Zápis je dávka `POST /api/sheets/:id/ops` s tělem `{"baseVersion": 12, "ops": [...]}`. Stejné operace posílá mřížka v prohlížeči přes WebSocket. Jedna chybná operace shodí celou dávku.

| `op` | pole |
| --- | --- |
| `insert_column` | `name`, volitelně `before` (id sloupce, jinak na konec) |
| `rename_column` | `name`, `newName` |
| `delete_column` | `name` |
| `move_column` | `name`, volitelně `before` (id sloupce, jinak na konec) |
| `resize_column` | `name`, `width` (celé číslo 40–2000 px, `null` vrátí výchozí šířku) |
| `insert_row` | volitelně `before` (id řádku, jinak na konec), volitelně `values` podle jmen sloupců |
| `delete_row` | `row` |
| `move_row` | `row`, volitelně `before` (id řádku, jinak na konec) |
| `set` | `row`, `column`, `value` |

`row` je id řádku, nebo podmínka `{"column": "sku", "value": "ABC"}`, která musí trefit právě jeden řádek. `name` a `column` jsou jména sloupců.

`set` a `delete_row` vrátí konflikt 409, když se dotčená buňka změnila po `baseVersion`. `insert_column`, `rename_column` a `delete_column` projdou jen na aktuální verzi. Přesuny a šířka verzi nekontrolují. Šířka se ukládá u sloupce jako `width` a do CSV ani dotazu nejde.

## Testy

```bash
uv run pytest
```
