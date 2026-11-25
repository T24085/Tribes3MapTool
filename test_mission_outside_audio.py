"""Check the DIF outside gain rules independently of the native sound mixer."""

from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


class OutsideAudioTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime()
        root = Path(__file__).parent
        self.acoustic = self.lua.execute((root / "runtime-fixes/mission_outside_audio.lua").read_text("utf-8"))
        self.table = lambda value: self.lua.table_from(value, recursive=True)

    def placement(self, *, outside=False, portals=(), offset=0, null_face=False, exterior_room=False):
        vertices = self.table([
            [-20, -20, 0], [-20, 20, 0], [20, 20, 0], [20, -20, 0], [-20, -20, -5], [20, 20, 10],
        ])
        data = self.table({
            "nodes": [[0, 0, 1, 0, -2 if exterior_room else -3, -1]],
            "outside_surfaces": [outside],
            "portal_centers_m": [[], list(portals)],
            "collision": {
                "node_planes": [0], "solid_children": [[-1, 0]],
                "leaves": [[] if null_face else [0]], "faces": [[0, 0, 1, 2, 3]],
            },
        })
        localize = self.lua.eval("function(offset) return function(p) return {p[1], p[2], p[3] - offset} end end")
        return self.lua.table(model=self.acoustic.prepare(vertices, data), localize=localize(offset))

    def gain(self, placements, x, height=2, prior=.37):
        return self.acoustic.gain(self.lua.table_from(placements), self.table([x, 0, height]),
                                  self.table([x, 0, height - 2000]), prior)

    def test_indoor_only_the_current_rooms_portals_contribute(self):
        enclosed = self.placement()
        self.assertEqual(self.gain([enclosed], 0), 0)
        open_room = self.placement(portals=([0, 0, 2],))
        self.assertAlmostEqual(self.gain([open_room], 5), .5)
        self.assertEqual(self.gain([open_room], 10), 0)
        two_openings = self.placement(portals=([0, 0, 2], [8, 0, 2]))
        self.assertEqual(self.gain([two_openings], 4), 1)

    def test_nearest_source_face_and_missing_face_each_have_distinct_gain(self):
        self.assertEqual(self.gain([self.placement()], 21), 1)
        self.assertEqual(self.gain([self.placement(exterior_room=True)], 0), 1)
        self.assertEqual(self.gain([self.placement(null_face=True)], 0), .37)
        roof = self.placement(outside=True, offset=1)
        lower_room = self.placement()
        for order in ([roof, lower_room], [lower_room, roof]):
            self.assertEqual(self.gain(order, 0), 1)
        self.assertEqual(self.gain([roof, lower_room], 0, height=.5), .37)

    def test_listener_updates_all_emitter_gains_from_one_trace(self):
        room = self.placement(portals=([0, 0, 2],))
        low, high = [], []
        tracks = self.lua.table_from([
            self.lua.table(source_gain=.25, set_gain=low.append),
            self.lua.table(source_gain=.75, set_gain=high.append),
        ])
        update = self.acoustic.listener(self.lua.table_from([room]), tracks, 1)
        for x in (15, 5, 5, 21):
            update(self.table([x, 0, 2]))
        self.assertEqual(low, [0, .125, .25])
        self.assertEqual(high, [0, .375, .75])

    def test_native_binding_selects_the_owned_world_and_applies_gain_before_play(self):
        model = self.placement(portals=([0, 0, 2],)).model
        data = model.data
        counts = self.lua.execute("""
            return function(acoustic, vertices, data)
                local world={GetAddress=function()return 12 end}
                local foreign={GetAddress=function()return 45 end}
                local captured={}
                local component={Sound={},SetVolumeMultiplier=function(_,v)captured[#captured+1]=v end,
                    Play=function()captured[#captured+1]='start' end}
                local function actor(owner, tag, audio)
                    return {AudioComponent=audio,GetWorld=function()return owner end,
                        ActorHasTag=function(_,query)return query==tag end,
                        K2_GetActorLocation=function()return {X=0,Y=0,Z=200} end,
                        GetActorForwardVector=function()return {X=1,Y=0,Z=0} end}
                end
                local room=actor(world,'room')
                local sound=actor(world,'sound',component)
                local document={100,{{'room',1}},{{vertices,data.nodes,data.outside_surfaces,data.portal_centers_m,
                    data.collision.node_planes,data.collision.solid_children,data.collision.leaves,data.collision.faces}},
                    {{'sound',.5,{30,90,.25}}}}
                local update=acoustic.bind({valid=function(x)return type(x)=='table'end,name=function(v)return v end,
                    find=function(kind)
                        if kind=='StaticMeshActor'then return {room}end
                        return {actor(foreign,'sound'),sound}
                    end,local_point=function(_,p)return {X=p[1],Y=p[2],Z=p[3]}end},12,document)
                update({1500,0,200})
                update({500,0,200})
                update({-500,0,200})
                return captured
            end
        """)(self.acoustic, model.vertices, data)
        self.assertEqual(list(counts.values()), [0, "start", .25, .0625])
