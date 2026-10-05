---
name: jot
description: >
  Reads and edits a note or table on a running jot instance through a share link:
  for a note it fetches the markdown, applies edits and replies in comment threads;
  for a table it reads with a query and writes with a batch of operations. Use when
  the user invokes the jot skill, runs /jot, or asks to work on a jot share URL
  (http://…/s/<id>).
---

# jot

Work on the note or table through the HTTP API of the running instance. The share link is in the user's request. Your name in comments is `Agent` unless the user gives another.

The instance must already be running. This skill does not start it.

## Input

From the address `http(s)://<host>/s/<shareId>` take the origin (scheme, host, port) and the `shareId`. The segment after `/s/` is the whole ticket. It may contain `A–Z`, `a–z`, `0–9`, `-` and `_`, and goes into the API exactly as it appears in the address. Any further path, query or fragment does not belong in the calls.

If the request has no address, ask for it and call nothing.

## Steps

1. `GET /api/share/<shareId>`. If it returns a note, work with what it returns now.
2. If it returns `404`, try `GET /api/share/<shareId>/data`. If that succeeds, the link leads to a table and the [Table](#table) section applies.
3. If both return `404`, the link is wrong, expired, or revoked by the owner. Only the owner can issue a new one. Tell the user and try nothing else.
4. For a note, choose operations by `note.shareAccess`.
5. Make only the change the user asked for. Leave text they did not mention unchanged.
6. When a change comes from a comment, reply in that thread afterwards.
7. Load the note or table again, and only then tell the user what it contains.

## Link validity

The response from `GET /api/share/<shareId>` and the JSON from `/data` carry `linkExpires`. For CSV it is the `X-Jot-Link-Expires` header.

- A date (`2026-09-27`) is the last valid UTC day. Such a daily link serves only the task at hand. Do not store it for later.
- `null` (`never` for CSV) is a permalink. It does not expire until the owner revokes it. If you are to use it repeatedly, store it where the user tells you and treat it like a password: do not print it in output, logs or commits.

`canReply`, `canEdit` and `canResolve` in the response describe the browser session. Do not use them to decide whether an API call will succeed.

## Reading

```bash
curl -sS "$ORIGIN/api/share/$SHARE_ID"
```

Take `note.markdown` as the source text, `note.title` as the title, and `threads` as the comments. For a thread read `id`, `resolved` and `anchor.quote`, and for its messages `id`, `authorName` and `body`.

## Editing text

Requires `shareAccess` `edit`.

```bash
curl -sS -X POST "$ORIGIN/api/share/$SHARE_ID/edit" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/jot-edit.json
```

Body:

```json
{"edits":[{"oldText":"exact unique piece of markdown","newText":"new piece"}]}
```

- `oldText` is an exact substring of the `markdown` field, including spaces and newlines. Not text from the rendered HTML.
- `oldText` must not be empty and must occur in the current markdown exactly once. If it occurs more than once, add surrounding context until it is unique.
- An empty `newText` deletes the piece.
- Several items in `edits` apply in order. If one fails, nothing is saved.
- To append text, replace the unique end of the note with the same end plus a new paragraph.
- An empty note cannot be filled through this endpoint. Tell the user.
- This link changes neither the note's title nor its sharing level.

When `oldText` contains quotes or newlines, write the JSON to a file in `/tmp` and send it with `--data-binary`. That file does not belong in a repository.

## Comments

Reading and new comments need at least `comment`. Text edits still need `edit`.

Reply to an existing message:

```bash
curl -sS -X POST "$ORIGIN/api/share/$SHARE_ID/threads/$THREAD_ID/replies" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/jot-reply.json
```

```json
{"parentMessageId":"<message id>","name":"Agent","body":"reply text"}
```

Anchor a new comment to a piece of visible text. The `quote` must stay findable in the note; if you rewrite that piece, the thread loses its place. `start` and `end` are the indexes of `quote` in the `markdown` field (`end` is the position after the piece). Put about 40 characters of the surroundings in `prefix` and `suffix`.

```json
{"name":"Agent","body":"comment text","anchor":{"quote":"piece","prefix":"text before ","suffix":" text after","start":10,"end":15}}
```

```bash
curl -sS -X POST "$ORIGIN/api/share/$SHARE_ID/threads" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/jot-comment.json
```

Send `name` with every request. Do not resolve or delete threads through a share link; that is left to the note's owner.

## Table

A table has named columns and rows with ids. A cell is always text: `001` stays `001` and comparison is textual. Level `view` only reads, `edit` reads and writes.

### Reading

```bash
curl -sS -G "$ORIGIN/api/share/$SHARE_ID/data" --data-urlencode "q=SELECT name, status WHERE status = 'open' ORDER BY name LIMIT 50"
```

Without `q` the whole table comes back. The response has `version`, `columns` (names), `columnIds` (name → column id), `rows` and `truncated`. Each row has an `id` field and values keyed by column name. With `format=csv` you get CSV, with `_id` as the first column and the version in the `X-Jot-Version` header.

`q` is a single statement: `SELECT` with a list of columns or `*`, optionally `WHERE`, `ORDER BY` (`ASC`, `DESC`) and `LIMIT`. Conditions know `=`, `<>`, `<`, `<=`, `>`, `>=`, `LIKE` (`%` and `_`), `AND`, `OR` and parentheses. A value goes in single quotes. Put a name with a space in double quotes: `"unit price"`. Column names are case sensitive. `_id` may be filtered and sorted on. An unknown column or other syntax returns `400`.

Above 2000 rows the server does not return the whole table. Narrow it with `WHERE` or `LIMIT` until the result fits.

### Writing

Requires `shareAccess` `edit`. Read the `version` first and send it as `baseVersion`.

```bash
curl -sS -X POST "$ORIGIN/api/share/$SHARE_ID/ops" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/jot-ops.json
```

```json
{"baseVersion": 12, "ops": [
  {"op": "insert_column", "name": "status"},
  {"op": "insert_row", "values": {"name": "Ada", "status": "open"}},
  {"op": "set", "row": "<row id>", "column": "status", "value": "closed"}
]}
```

| `op` | fields |
| --- | --- |
| `insert_column` | `name`, optionally `before` (column id, otherwise at the end) |
| `rename_column` | `name`, `newName` |
| `delete_column` | `name` |
| `move_column` | `name`, optionally `before` (column id, otherwise at the end) |
| `resize_column` | `name`, `width` (40–2000 px, `null` restores the default) |
| `insert_row` | optionally `before` (row id, otherwise at the end), optionally `values` by column name |
| `delete_row` | `row` |
| `move_row` | `row`, optionally `before` (row id, otherwise at the end) |
| `set` | `row`, `column`, `value` |

- `row` is a row id, or `{"column": "sku", "value": "ABC"}`. The condition must match exactly one row.
- `name` and `column` are column names. `before` for a column is an id from `columnIds`, for a row the row's `id`.
- Operations run in order. If one fails, nothing is saved and `op` in the response says which one.
- A `409` response means the table changed in the meantime. Read it again and write only what still applies. Do not repeat the same request.
- Column changes (`insert_column`, `rename_column`, `delete_column`) pass only on the current version.

### CSV import

An empty table with no columns or rows can be filled with a whole file at once. A table that already has content refuses the import; change it with a batch.

```bash
curl -sS -X POST "$ORIGIN/api/share/$SHARE_ID/import-csv" \
  -H "Content-Type: text/csv; charset=utf-8" \
  -H "X-Jot-Base-Version: <version>" \
  --data-binary @file.csv
```

The first line holds the column names. A `_id` column is dropped. Imports above 2000 rows are refused.

## After a change

Load `GET /api/share/<shareId>` again, or `GET /api/share/<shareId>/data` for a table. Check the markdown and the thread text, or the rows the batch changed. Tell the user what changed. An open editor or grid receives the change on its own.
