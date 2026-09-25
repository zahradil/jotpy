import json
import random
import subprocess

from jotpy.collab import collab_from_markdown, collab_to_markdown
from jotpy.id_list import ElementId, IdList

ORACLE = r"""
const { createRequire } = require("module");
const { IdList } = createRequire("/home/zahradil/Work/jot/jot/package.json")("articulated");
const fs = require("fs");
const ops = JSON.parse(fs.readFileSync(0, "utf8"));
let list = IdList.new();
const out = [];
for (const op of ops) {
  if (op.op === "insertAfter") {
    list = list.insertAfter(op.before, op.id, op.count ?? 1);
  } else if (op.op === "delete") {
    list = list.delete(op.id);
  } else if (op.op === "deleteRange") {
    list = list.deleteRange(op.from, op.to);
  } else if (op.op === "reload") {
    list = IdList.load(list.save());
  } else {
    throw new Error("bad op " + op.op);
  }
  out.push(list.save());
}
process.stdout.write(JSON.stringify(out));
"""


def _id(value):
    if value is None:
        return None
    return ElementId(value["bunchId"], value["counter"])


def _json_id(element_id):
    if element_id is None:
        return None
    return {"bunchId": element_id.bunch_id, "counter": element_id.counter}


def replay(ops):
    listed = IdList.new()
    saved = []
    for op in ops:
        if op["op"] == "insertAfter":
            listed = listed.insert_after(_id(op["before"]), _id(op["id"]), op.get("count", 1))
        elif op["op"] == "delete":
            listed = listed.delete(_id(op["id"]))
        elif op["op"] == "deleteRange":
            listed = listed.delete_range(op["from"], op["to"])
        elif op["op"] == "reload":
            listed = IdList.load(listed.save())
        else:
            raise AssertionError(op)
        saved.append(listed.save())
    return saved


def oracle(ops):
    completed = subprocess.run(
        ["node", "-e", ORACLE],
        input=json.dumps(ops).encode(),
        check=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def explicit_ops():
    return [
        {"op": "insertAfter", "before": None, "id": {"bunchId": "a", "counter": 0}, "count": 1},
        {"op": "insertAfter", "before": {"bunchId": "a", "counter": 0}, "id": {"bunchId": "b", "counter": 0}, "count": 1},
        {"op": "insertAfter", "before": {"bunchId": "b", "counter": 0}, "id": {"bunchId": "c", "counter": 5}, "count": 4},
        {"op": "delete", "id": {"bunchId": "c", "counter": 6}},
        {"op": "deleteRange", "from": 0, "to": 2},
        {"op": "reload"},
        {"op": "insertAfter", "before": None, "id": {"bunchId": "d", "counter": 0}, "count": 3},
        {"op": "insertAfter", "before": {"bunchId": "d", "counter": 0}, "id": {"bunchId": "e", "counter": 0}, "count": 2},
        {"op": "insertAfter", "before": None, "id": {"bunchId": "m", "counter": 0}, "count": 1},
        {"op": "insertAfter", "before": {"bunchId": "m", "counter": 0}, "id": {"bunchId": "m", "counter": 2}, "count": 1},
        {"op": "insertAfter", "before": {"bunchId": "m", "counter": 0}, "id": {"bunchId": "m", "counter": 1}, "count": 1},
        {"op": "delete", "id": {"bunchId": "m", "counter": 1}},
        {"op": "reload"},
    ]


def random_ops(seed=12345, count=80):
    rng = random.Random(seed)
    listed = IdList.new()
    ops = []
    counters = {}
    bunches = ["p", "q", "s"]
    for _ in range(count):
        known = [element.id for element in listed._state]
        roll = rng.randrange(5)
        if roll <= 2 or listed.length == 0:
            bunch = rng.choice(bunches)
            start = counters.get(bunch, 0)
            width = rng.randint(1, 5)
            counters[bunch] = start + width
            new_id = ElementId(bunch, start)
            before = None if not known or rng.randrange(4) == 0 else rng.choice(known)
            listed = listed.insert_after(before, new_id, width)
            ops.append(
                {
                    "op": "insertAfter",
                    "before": _json_id(before),
                    "id": _json_id(new_id),
                    "count": width,
                }
            )
        elif roll == 3 and known:
            target = rng.choice(known)
            listed = listed.delete(target)
            ops.append({"op": "delete", "id": _json_id(target)})
        elif listed.length:
            start = rng.randrange(listed.length)
            end = rng.randrange(start + 1, listed.length + 1)
            listed = listed.delete_range(start, end)
            ops.append({"op": "deleteRange", "from": start, "to": end})
    ops.append({"op": "reload"})
    return ops


def test_save_matches_articulated_id_list():
    ops = explicit_ops() + random_ops()
    assert replay(ops) == oracle(ops)


def test_collab_markdown_roundtrip_counts_utf16_units():
    text = "a😀b"
    state = collab_from_markdown(text, 3)
    assert state.id_list.length == 4
    assert collab_to_markdown(state) == text
    assert state.server_counter == 3
    assert IdList.load(state.id_list.save()).save() == state.id_list.save()
