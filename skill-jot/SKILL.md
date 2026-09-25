---
name: jot
description: >
  Čte a upravuje běžící poznámku jot přes sdílený odkaz: stáhne markdown,
  aplikuje úpravy a odpovídá ve vláknech komentářů. Use when the user invokes
  the jot skill, runs /jot, says „skill jot“ or „pomocí skillu jot“, or asks
  to work on a jot share URL (http://…/s/<id>).
---

# jot

Poznámku upravuj přes HTTP API běžící instance. Sdílený odkaz je v zadání uživatele. Jméno v komentářích je `Grok`, pokud uživatel neřekne jiné.

Instance už musí běžet. Tento skill ji nespouští.

## Vstup

Z adresy `http(s)://<host>/s/<shareId>` vezmi původ (schéma, host, port) a `shareId`. Segment za `/s/` je celý ticket. Může obsahovat `A–Z`, `a–z`, `0–9`, `-` a `_`, a do API se dává tak, jak v adrese je. Další cesta, dotaz ani kotva do volání nepatří.

Když adresa v zadání není, zeptej se na ni a nic nevolej.

## Postup

1. `GET /api/share/<shareId>` a pracuj s tím, co vrátí teď.
2. Podle `note.shareAccess` zvol operace níže. `404` znamená špatný odkaz, nebo že sdílení je vypnuté.
3. Proveď jen změnu, kterou uživatel zadal. Text, o kterém nemluvil, nech beze změny.
4. Když změna plyne z komentáře, po ní na to vlákno odpověz.
5. Znovu zavolej `GET` a teprve podle něj uživateli řekni, co v poznámce je.

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

## Po změně

Znovu načti `GET /api/share/<shareId>`. Ověř markdown a text vlákna. Uživateli napiš, co se změnilo. Otevřený editor dostane změnu sám.
