from jotpy.collab import collab_from_markdown, collab_to_markdown
from jotpy.id_list import IdList


def test_collab_markdown_roundtrip_counts_utf16_units():
    text = "a😀b"
    state = collab_from_markdown(text, 3)
    assert state.id_list.length == 4
    assert collab_to_markdown(state) == text
    assert state.server_counter == 3
    assert IdList.load(state.id_list.save()).save() == state.id_list.save()
