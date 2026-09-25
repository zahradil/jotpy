# Pokyny pro práci na jotpy

## Když přidáš nebo změníš funkci

Když se změní to, co server nebo prohlížeč umí, uprav ve stejném commitu i popis:

1. `README.md`: co jotpy umí, API a formát dat. Popisuje aktuální stav, ne historii.
2. `skill-jot/SKILL.md`: skill pro agenta, který pracuje s poznámkou nebo tabulkou přes sdílený odkaz. Server ho vydává na `/skill/jot/SKILL.md`. Musí sedět s tím, co API pod `/api/share/…` opravdu přijme a vrátí.

Tlačítko „Agent setup“ u poznámky i tabulky (`openAgentModal` v `public/app.js` a `public/sheet.js`) dává agentovi jen adresu skillu a sdílený odkaz. Návod k API do něj nepiš, patří do skillu. Uprav ho, jen když se změní způsob předání agentovi.

Nové chování serveru pokryj testem v `tests/`.

## Testy

```bash
uv run pytest
```
