# TRIBES 3 Map Tool

This is the in-development Windows source for the TRIBES 3 community map editor. It creates CTF drafts and converts missions from Tribes 1, Tribes 2, Vengeance and Ascend into editable Unreal Engine 5.3 projects. The editor includes terrain and texture review, minimap authoring, mission boundaries, and Direct Host staging.

## Get started

Install Python 3.13 and Unreal Engine 5.3, then run these commands from this checkout. Use your own workspace parent and Unreal binaries directory.

```powershell
py -3.13 -m pip install Pillow
py -3.13 tools/community_map_editor/bootstrap.py "D:/Maps" "C:/UE_5.3/Engine/Binaries/Win64"
py -3.13 tools/community_map_editor/launch.py "D:/Maps/CommunityMapEditor/local-settings.json"
```

See the [editor guide](tools/community_map_editor/README.md) for conversion, cooking, offline playtests and host export requirements. The source snapshot contains no installed game, Unreal Editor binaries or cooked maps.

## Flag boundary verification

An Unreal Engine 5.3 draft with a three-second flag return timer passed a Windows cook, Direct Host export, staging and rollback checks. In the isolated cooked CTF match, reentering the boundary cleared the warning and kept the flag with the carrier. On a second exit, the flag returned home after 3.034 seconds and the HUD showed the return notice. The gameplay check covered an offline match. Connected multiplayer and occupied vehicles still need in-game verification.

## Source tests

```powershell
py -3.13 -m pip install Pillow lupa
py -3.13 -B -m unittest discover -s . -p "test_*.py"
```

[Source revisions](source-revisions.json) record how this snapshot was assembled.
