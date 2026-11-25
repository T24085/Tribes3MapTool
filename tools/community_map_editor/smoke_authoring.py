"""Verify a fresh authoring workspace can create and reopen its starter draft."""

import json
import os
from pathlib import Path
import re

import t3_map_editor as editor
from source_profiles import ANY_DRAFT


if os.environ['T3_EDITOR_INITIAL'] == 'blank':
    editor.create_blank()
else:
    draft = Path(os.environ['T3_EDITOR_LAST_DRAFT']).read_text(encoding='utf-8').strip()
    assert re.fullmatch(ANY_DRAFT, draft)
    assert editor.LEVELS.load_level(f'{editor.LEVEL_ROOT}/{draft}/Arena_{draft}_P')

snapshot = editor.marker_snapshot()
assert editor.ASSETS.does_asset_exist(editor.PALETTE + '/Materials/M_Grass')
output = Path(os.environ['T3_EDITOR_REQUEST']).with_name('authoring-smoke.json')
output.write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
