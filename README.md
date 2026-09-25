# jotpy

Python přepis serveru [jot](https://github.com/mariozechner/jot) (MIT, Mario Zechner / `@mariozechner/jot`).

Prohlížeč (`public/`) je z původního projektu beze změn. Docker součástí není. Formát dat v `data/` je kompatibilní s TypeScript serverem (JSON poznámky, odvozený markdown, `auth.json` se scrypt hashi).

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

## Testy

```bash
uv run pytest
```
