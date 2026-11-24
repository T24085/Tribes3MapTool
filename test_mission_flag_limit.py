"""Exercise the authored flag-only rule with native-shaped Lua objects."""

from pathlib import Path
import unittest

from lupa.lua54 import LuaError, LuaRuntime


FIXTURE = r"""
return function(factory)
 local function name(text)return {ToString=function()return text end}end
 local seq=0
 local function object(label,world)
  seq=seq+1
  return {id=seq,label=label,world=world,alive=true,
   GetFullName=function(self)return self.label end,GetAddress=function(self)return self.id end,
   HasAuthority=function()return true end,GetWorld=function(self)return self.world end,
   K2_GetActorLocation=function(self)return self.at end}
 end
 local function valid(v)return v~=nil and v.alive end
 local world,foreign=object('Owned World'),object('Other World')
 local gm=object('Mode',world);gm.GameState=object('Game State',world)
 gm.GetMatchState=function()return name('InProgress')end
 gm.GameState.r_GameplayPhaseInfo={Phase={TagName=name('GamePhase.GameplayStartPhase')}}
 FName=function(v)return v end
 local function marker(tags,x,y,owner)
  if type(tags)=='string'then tags={tags}end
  local a=object(tags[1],owner or world);a.at={X=x,Y=y,Z=0}
  a.ActorHasTag=function(_,wanted)
   for _,tag in ipairs(tags)do if tag==wanted then return true end end
   return false
  end
  return a
 end
 local pawn=object('Pathfinder Player',world)
 local nextPawn=object('Raider Player',world)
 local pawn_class=object('Class /Script/Valhalla.ValCharacter')
 for _,p in ipairs({pawn,nextPawn})do
  p.IsA=function(_,kind)return kind==pawn_class end
 end
 local flags={}
 for team=1,2 do
  local f=object('Flag '..team,world);f.r_TeamId=team;f.at={X=0,Y=0,Z=0};f.moves=0;f.drops=0
  f.GetOwner=function(self)return self.owner end
  f.GetFlagState=function(self)return self.state end
  f.Drop=function(self,scored,inherit,forced)
   assert(scored==false and inherit==false and self.owner)
   self.at=self.owner.at;self.owner=nil;self.forced=forced;self.drops=self.drops+1
   self.state=forced and 1 or 4
  end
  f.K2_SetActorLocation=function(self,target,sweep,_,teleport)
   assert(not self.owner and self.state==4 and not sweep and teleport)
   self.at=target;self.moves=self.moves+1
  end
  flags[team]=f
 end
 local points={marker('T3MissionFlagMin',-1000,-1200),marker('T3MissionFlagMax',1000,2000),
               marker('T3MissionFlagMax',70000,70000,foreign)}
 FindAllOf=function(kind)
  if kind=='TargetPoint'then return points end
  assert(kind=='TopPickup_Flag');return flags
 end
 local fields={
  ['/Script/Topaz.TopPickup_Flag:Drop']={{'bScored','BoolProperty',0},{'bInheritVelocity','BoolProperty',1},
    {'bForceReturnToStart','BoolProperty',2}},
  ['/Script/Topaz.TopPickup_Flag:GetFlagState']={{'Querier','ObjectProperty',0},{'ReturnValue','EnumProperty',8}},
  ['/Script/Engine.Actor:K2_SetActorLocation']={{'NewLocation','StructProperty',0},{'bSweep','BoolProperty',24},
    {'SweepHitResult','StructProperty',32},{'bTeleport','BoolProperty',264},{'ReturnValue','BoolProperty',265}},
 }
 StaticFindObject=function(path)
  if path=='/Script/Valhalla.ValCharacter'then return pawn_class end
  if path=='/Script/Topaz.ETopFlagState'then
   local e=object(path);e.ForEachName=function(_,cb)cb(name('ETopFlagState::Dropped'),4)end;return e
  end
  assert(fields[path],path)
  local fn=object('Function '..path)
  fn.ForEachProperty=function(_,cb)
   for _,p in ipairs(fields[path])do
    cb({GetFullName=function()return p[2]..' '..path..':'..p[1]end,GetOffset_Internal=function()return p[3]end})
   end
  end
  return fn
 end
 local notices,pending={}
 ExecuteInGameThreadWithDelay=function(delay,callback)assert(delay==75);pending=callback end
 local service=factory({valid=valid,scope=function()end,report=function(v)notices[#notices+1]=v end})
 local function tick()local call=assert(pending);pending=nil;call()end
 local function move(p,x,y,z)p.at={X=x,Y=y,Z=z}end
 return {start=function()return service(gm)end,move=move,tick=tick,
  pawn=pawn,nextPawn=nextPawn,flags=flags,world=world,notices=notices,
  points=function(enabled)if not enabled then points={points[3]}end end,
  polygon=function(vertices,size)
   points={}
   for i,p in ipairs(vertices)do
    points[#points+1]=marker({'T3MissionFlagPolygon','T3MissionFlagVertex'..i,'T3MissionFlagVertexCount'..size},p[1],p[2])
   end
  end}
end
"""


class MissionFlagLimitTest(unittest.TestCase):
    def fixture(self, vertices=None, *, claimed_size=None):
        runtime = LuaRuntime()
        source = (Path(__file__).parent / "runtime-fixes/mission_flag_limit.lua").read_text(encoding="utf-8")
        result = runtime.execute(FIXTURE)(runtime.execute(source))
        if vertices is not None:
            result.polygon(runtime.table_from([runtime.table_from(point) for point in vertices]),
                           len(vertices) if claimed_size is None else claimed_size)
        return result

    def test_crossing_releases_the_flag_once_at_the_last_legal_location(self):
        f = self.fixture()
        flag = f.flags[2]
        flag.owner = f.pawn
        f.move(f.pawn, 950, 100, 619)
        self.assertTrue(f.start())
        f.move(f.pawn, 1800, 240, 671)
        f.tick()
        f.tick()
        self.assertIsNone(flag.owner)
        self.assertFalse(flag.forced)
        self.assertEqual((flag.drops, flag.moves), (1, 1))
        self.assertEqual((flag.at.X, flag.at.Y, flag.at.Z), (950, 100, 619))
        self.assertEqual(f.pawn.at.X, 1800)
        self.assertIn("MISSION_FLAG_LIMIT_DROP|team=2", f.notices[2])

    def test_new_carrier_outside_never_reuses_an_old_carriers_position(self):
        f = self.fixture()
        flag = f.flags[1]
        flag.owner = f.pawn
        f.move(f.pawn, -150, 500, 406)
        self.assertTrue(f.start())
        flag.owner = f.nextPawn
        f.move(f.nextPawn, 0, -1350, 418)
        f.tick()
        self.assertTrue(flag.forced)
        self.assertEqual((flag.drops, flag.moves), (1, 0))
        self.assertIn("MISSION_FLAG_LIMIT_RETURN|team=1", f.notices[2])

    def test_foreign_markers_do_not_activate_the_rule(self):
        f = self.fixture()
        f.points(False)
        self.assertFalse(f.start())
        self.assertEqual(len(f.notices), 0)

    def test_polygon_detects_crossing_a_notch_between_two_inside_samples(self):
        vertices = [(-900, -900), (900, -900), (900, 900), (250, 900), (0, 350), (-250, 900), (-900, 900)]
        f = self.fixture(vertices)
        flag = f.flags[2]
        flag.owner = f.pawn
        f.move(f.pawn, -700, 700, 150)
        self.assertTrue(f.start())
        f.move(f.pawn, 700, 700, 180)
        f.tick()
        self.assertEqual((flag.drops, flag.moves), (1, 1))
        self.assertEqual((flag.at.X, flag.at.Y, flag.at.Z), (-700, 700, 150))

    def test_polygon_tangent_to_notch_stays_legal(self):
        vertices = [(-900, -900), (900, -900), (900, 900), (250, 900), (0, 350), (-250, 900), (-900, 900)]
        f = self.fixture(list(reversed(vertices)))
        flag = f.flags[1]
        flag.owner = f.pawn
        f.move(f.pawn, -700, 350, 150)
        self.assertTrue(f.start())
        f.move(f.pawn, 700, 350, 180)
        f.tick()
        self.assertEqual(flag.drops, 0)
        self.assertIn("MISSION_FLAG_LIMIT_READY", f.notices[1])

    def test_polygon_marker_errors_fail_at_startup(self):
        for size in (900, 8_000_000):
            f = self.fixture([(-size, -size), (size, size), (size, -size), (-size, size)])
            with self.subTest(size=size), self.assertRaisesRegex(LuaError, "polygon_edges_intersect"):
                f.start()
        f = self.fixture([(-900, -900), (900, -900), (900, 900), (-900, 900)], claimed_size=5)
        with self.assertRaisesRegex(LuaError, "missing_polygon_vertex"):
            f.start()
