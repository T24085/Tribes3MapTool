"""Verify a fresh authoring workspace can create and reopen its starter draft."""

import json
import os
from pathlib import Path

import t3_map_editor as editor


if os.environ['T3_EDITOR_INITIAL'] == 'blank':
    editor.create_blank()

snapshot = editor.marker_snapshot()
assert editor.ASSETS.does_asset_exist(editor.PALETTE + '/Materials/M_Grass')
output = Path(os.environ['T3_EDITOR_REQUEST']).with_name('authoring-smoke.json')
output.write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
