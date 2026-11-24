"""Create both draft types with the actual editor and queue the blank for cooking."""
import json
import os
from pathlib import Path

import t3_map_editor as editor


blank_level = editor.LEVEL_ROOT+'/DraftCTF001/Arena_DraftCTF001_P'
if editor.ASSETS.does_asset_exist(blank_level):
    assert editor.LEVELS.load_level(blank_level)
else:
    editor.create_blank()
editor.materials(rebuild=True)
blank = editor.marker_snapshot()
copy_level = editor.LEVEL_ROOT+'/DraftDamnation001/Arena_DraftDamnation001_P'
if editor.ASSETS.does_asset_exist(copy_level):
    assert editor.LEVELS.load_level(copy_level)
else:
    editor.copy_damnation()
copy = editor.marker_snapshot()
assert len(editor.ASSETS.list_assets(editor.PALETTE+'/Materials')) == 3
assert editor.ASSETS.does_asset_exist(editor.SOURCE_LEVEL)
output = Path(os.environ['T3_EDITOR_REQUEST'])
output.with_name('editor-smoke.json').write_text(json.dumps({'blank':blank, 'copy':copy}, indent=2), encoding='utf-8')
ident = copy['map'] if os.environ.get('T3_EDITOR_SMOKE_COPY') == '1' else blank['map']
assert editor.LEVELS.load_level(f'{editor.LEVEL_ROOT}/{ident}/Arena_{ident}_P')
editor.build_playtest()
