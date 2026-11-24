# TRIBES 3 Map Tool

This is the in-development Windows source for the TRIBES 3 community map editor. It creates CTF drafts and converts missions from Tribes 1, Tribes 2, Vengeance and Ascend into editable Unreal Engine 5.3 projects. The editor includes terrain and texture review, minimap authoring, mission boundaries, and Direct Host staging.

## Get started

Install Python 3.13 and Unreal Engine 5.3, then run these commands from this checkout with your own workspace parent.

```powershell
py -3.13 -m pip install Pillow
py -3.13 tools/community_map_editor/bootstrap.py "D:/Maps" --launch
```

Setup detects UE 5.3 through Epic Games Launcher. For an unregistered
build, pass its installation directory after the workspace parent.
To reopen the workspace, run
`py -3.13 tools/community_map_editor/launch.py "D:/Maps/CommunityMapEditor/local-settings.json"`.

See the [editor guide](tools/community_map_editor/README.md) for conversion, cooking, offline playtests and host export requirements. The source snapshot contains no installed game, Unreal Editor binaries or cooked maps.

The docked Object Review panel can be built and installed for the
workspace's exact UE 5.3 build. See the
[Object Review installation guide](tools/community_map_editor/README.md#dock-object-review-inside-unreal).

The stock Unreal workspace can use **Build for Direct Host** after importing a draft. Select a verified Direct Host bundle that supports standard Unreal World Settings. Offline playtesting uses the separate game-compatible setup described in the editor guide.

From a fresh UE 5.3 workspace, authored Tribes 1, Tribes 2, Vengeance, and Ascend fixtures passed import, batch rebuild, Windows cooking, isolated Direct Host staging, and rollback.

Direct Host export and staging check the target bundle's support for editor draft World Settings. Rolling back the last custom map preserves the content directory required by the original native-map catalog.

The combined bundle also loaded all four authored fixtures in separate
native Direct Host matches. Each host reached ready with two flags and
two flag stands linked. These checks covered host startup and objective
setup. The Starter downloaded the same four maps through a loopback
test distributor, verified their minimap PNGs, and reopened them from
its map cache.

Minimap-bearing drafts require a Direct Host bundle whose game launcher
supports portable minimaps. Export and staging check the inventoried
launcher and reader before publishing the bundle.

A connected headless client joined each of the four authored fixtures
from its cached map. On each map, the test moved the player's pawn to
the flag and capture zones. The host recorded the pickup, advanced the
score from zero to one, and reset the flag at home. Both the host and
client shut down under their original process owners.

A fresh checkout with the updated setup successfully created and
reopened a blank CTF draft in headless UE 5.3. The draft contained three
spawns and one flag per team, and the starter grass material loaded.
Setup rejected UE 5.8 before creating a workspace.

## Flag boundary verification

An Unreal Engine 5.3 draft with a three-second flag return timer passed a Windows cook, Direct Host export, staging and rollback checks. In an offline match, reentry cleared the warning and kept the flag with the carrier. A second exit returned the flag home after 3.034 seconds and displayed the HUD notice. A later windowed capture confirmed that the warning sits below the match-start announcement.

Two isolated Direct Host matches verified notification delivery to a connected client. With an on-foot carrier, the host returned the flag after 3.060 seconds. In the second match, an Unreal test pawn with a Vehicle-channel root crossed the ceiling while the attached character remained inside. The flag returned after 3.066 seconds. Reentry cleared the warning and kept the flag in both matches. A drivable vehicle and the rendered network HUD still need in-game verification.

## Source tests

```powershell
py -3.13 -m pip install Pillow lupa
py -3.13 -B -m unittest discover -s . -p "test_*.py"
```

[Source revisions](source-revisions.json) record how this snapshot was assembled.
