# jotpy

Server pro poznámky a tabulky, na kterých pracuje člověk v prohlížeči a agent přes HTTP API zároveň. Obojí se dá sdílet odkazem, u poznámek s komentáři.

Prohlížeč v `public/` vychází z projektu [jot](https://github.com/mariozechner/jot) (MIT, Mario Zechner / `@mariozechner/jot`). Pořadí znaků v editoru drží jednoduchá verze knihovny [articulated](https://github.com/mweidner037/articulated) (MIT, Matthew Weidner): pole a splice se stejným `save()` jako `IdList` 1.3.1, ne její strom.

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

## Přístup

Instance má jednoho vlastníka. Při prvním otevření si nastaví heslo a prohlížeč pak drží session v cookie. Agent se jako vlastník přihlásí API klíčem v hlavičce `Authorization: Bearer <klíč>`. Klíče vlastník spravuje přes `GET`, `POST` a `DELETE /api/keys`.

Kdo nemá heslo ani klíč, vidí jen to, co mu vlastník nasdílí odkazem.

## Poznámky

Poznámka je markdown s titulkem. Leží v `data/notes/<id>.md` a vedle ní `<id>.json` s titulkem, sdílením, komentáři a stavem společné editace. Id má pět znaků `0-9a-z` a poznámky ho sdílejí s tabulkami.

Editor v prohlížeči zvládne víc lidí najednou. Změny jdou přes WebSocket a každý vidí kurzory ostatních. Náhled markdownu vyrenderuje server, bloky `mermaid` nakreslí prohlížeč.

Vlastník má tyto endpointy:

| volání | co dělá |
| --- | --- |
| `GET /api/notes?q=` | seznam poznámek, volitelně hledání v titulku a textu |
| `POST /api/notes` | nová prázdná poznámka |
| `GET /api/notes/:id` | celá poznámka; s `offset` a `limit` vrátí jen očíslované řádky |
| `PUT /api/notes/:id` | přepíše `title`, `markdown` nebo `shareAccess`; `rotateShare: true` vydá nový odkaz, `renewLink: true` posune konec denního odkazu |
| `DELETE /api/notes/:id` | smaže poznámku |
| `POST /api/notes/:id/edit` | cílené úpravy textu, viz níže |

Úprava je `{"edits": [{"oldText": "...", "newText": "..."}]}`. `oldText` je přesný úsek markdownu a v poznámce musí být právě jednou. Úpravy se aplikují postupně a když jedna selže, neuloží se žádná. Otevřené editory změnu dostanou hned, bez přepsání celého textu.

### Komentáře

Komentář je vlákno ukotvené k úryvku textu: `quote` spolu s okolím v `prefix` a `suffix`, aby se úryvek našel i po úpravách. Na zprávy ve vlákně se dá odpovídat. Vlákno vyřeší nebo smaže vlastník, nebo ten, kdo do něj psal. Kdo komentuje přes odkaz, zadá jméno a server si ho pamatuje v cookie.

### Sdílení

Poznámka má úroveň sdílení `none`, `view`, `comment` nebo `edit`. Odkaz `/s/<ticket>` nese id, úroveň, generaci a den konce platnosti a je podepsaný klíčem `data/link.key`. Sdílení má dva odkazy se stejnou úrovní:

- denní odkaz (`shareUrl`) platí do konce následujícího dne UTC, tedy 24 až 48 hodin. Poslední den je v `shareExpires`. `renewLink: true` ho posune na zítřek; dialog Share to udělá při každém otevření.
- permalink (`sharePermalink`) má den 4095 a nevyprší. Patří do konfigurace agenta, který s poznámkou nebo tabulkou pracuje pravidelně, a drží se jako heslo.

Změna úrovně nebo tlačítko „rotate links“ (`rotateShare: true`) zvednou generaci a oba odkazy přestanou platit. Generace se nevrací na nulu: po 255 změnách server další odmítne s `409` a sdílení jde už jen vypnout. Odpovědi pod `/api/share/<ticket>` nesou `linkExpires`, poslední den použitého odkazu, nebo `null` u permalinku. U tabulky v CSV je to hlavička `X-Jot-Link-Expires` s datem nebo `never`. Sdílená stránka platnost ukáže v hlavičce.

Přes odkaz jde totéž API pod `/api/share/<ticket>/…`: čtení, úprava textu (`edit`) a komentáře (`comment` a výš).

## Tabulky

Vedle poznámek leží tabulky v `data/sheets/<id>.json`, s kopií `<id>.csv` pro čtení. Buňka je text. Sdílet se dají pro `view` nebo `edit`, stejnými odkazy jako poznámky. Nová tabulka je rovnou sdílená pro `edit`, aby ji agent mohl hned naplnit.

Čtení je `GET /api/sheets/:id/data`, volitelně s `q` (jedna věta `SELECT`) a `format=csv`. JSON vrací jména vybraných sloupců v `columns`, jejich id v `columnIds` a řádky s polem `id`. Prázdnou tabulku naplní `POST /api/sheets/:id/import-csv`. Zápis je dávka `POST /api/sheets/:id/ops` s tělem `{"baseVersion": 12, "ops": [...]}`. Stejné operace posílá mřížka v prohlížeči přes WebSocket. Jedna chybná operace shodí celou dávku.

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

Nad 2000 řádků se mřížka v prohlížeči neotevře a tabulku jde číst jen dotazem s `WHERE` nebo `LIMIT`.

## Agent

Server vydává skill pro agenta na `/skill/jot/SKILL.md` (zdroj je v `skill-jot/`). Popisuje práci s poznámkou i tabulkou přes sdílený odkaz. Dialog Share v editoru i v mřížce má u denního odkazu i u permalinku tlačítko „for agent“, které zkopíruje text pro agenta: adresu skillu a odkaz. Ikonka robota u vlastníka ten dialog otevře. Na sdílené stránce ikonka robota ukáže stejný text s odkazem, přes který návštěvník stránku otevřel.

## Testy

```bash
uv run pytest
```
