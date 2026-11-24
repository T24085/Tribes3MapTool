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
 local clock=object('GameplayStatics');local time=0
 clock.GetTimeSeconds=function(_,context)assert(context==gm);return time end
 gm.GetMatchState=function()return name('InProgress')end
 gm.GameState.r_GameplayPhaseInfo={Phase={TagName=name('GamePhase.GameplayStartPhase')}}
 FName=function(v)return v end
 local function marker(tags,x,y,owner)
  if type(tags)=='string'then tags={tags}end
  local a=object(tags[1],owner or world);a.at={X=x,Y=y,Z=0};a.tags=tags
  a.ActorHasTag=function(_,wanted)
   for _,tag in ipairs(tags)do if tag==wanted then return true end end
   return false
  end
  return a
 end
 local pawn=object('Pathfinder Player',world)
 local nextPawn=object('Raider Player',world)
 local vehicle=object('Vehicle',world)
 local pawn_class=object('Class /Script/Engine.Pawn')
 local character_class=object('Class /Script/Valhalla.ValCharacter')
 local player_class=object('Class /Script/Engine.PlayerController')
 for _,p in ipairs({pawn,nextPawn,vehicle})do
  p.IsA=function(_,kind)return kind==pawn_class or (p~=vehicle and kind==character_class) end
  p.GetAttachParentActor=function(self)return self.parent end
  p.GetController=function(self)return self.controller end
 end
 local function controller(holder)
  local pc,ps=object('Player Controller',world),object('Player State',world)
  pc.Pawn,pc.PlayerState=holder,ps
  holder.controller,holder.PlayerState=pc,ps
  ps.GetOwner=function()return pc end
  pc.IsA=function(_,kind)return kind==player_class end
  pc.messages={}
  pc.ClientMessage=function(self,text,kind,life)
   assert((life==4 or life==0) and #text>0)
   self.messages[#self.messages+1]=kind
  end
  return pc
 end
 local pilot,other=controller(pawn),controller(nextPawn)
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
  ['/Script/Engine.Actor:GetAttachParentActor']={{'ReturnValue','ObjectProperty',0}},
  ['/Script/Engine.Pawn:GetController']={{'ReturnValue','ObjectProperty',0}},
  ['/Script/Engine.GameplayStatics:GetTimeSeconds']={{'WorldContextObject','ObjectProperty',0},
    {'ReturnValue','DoubleProperty',8}},
  ['/Script/Engine.PlayerController:ClientMessage']={{'S','StrProperty',0},{'Type','NameProperty',16},
    {'MsgLifeTime','FloatProperty',24}},
 }
 StaticFindObject=function(path)
  if path=='/Script/Engine.Pawn'then return pawn_class end
  if path=='/Script/Valhalla.ValCharacter'then return character_class end
  if path=='/Script/Engine.PlayerController'then return player_class end
  if path=='/Script/Engine.Default__GameplayStatics'then return clock end
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
  pawn=pawn,nextPawn=nextPawn,vehicle=vehicle,flags=flags,world=world,notices=notices,
  pilot=pilot,other=other,at_time=function(value)time=value end,
  enter_vehicle=function()
   pawn.parent,pawn.controller=vehicle,nil
   vehicle.controller,vehicle.PlayerState=pilot,pilot.PlayerState
   pilot.Pawn=vehicle
  end,
  fail_notice=function()pilot.ClientMessage=function()error('closed connection')end end,
  points=function(enabled)if not enabled then points={points[3]}end end,
  return_timer=function(seconds,omit)
   for _,entry in ipairs(points)do
    if entry.world==world and not entry:ActorHasTag('T3MissionFlagFloor') and not entry:ActorHasTag('T3MissionFlagCeiling')then
     table.insert(entry.tags,'T3MissionFlagNeedsReturnTimer')
    end
   end
   if not omit then
    points[#points+1]=marker({'T3MissionFlagReturnTimer','T3MissionFlagReturnSeconds'..seconds},0,0)
   end
  end,
  altitude=function(floor,ceiling,omit)
   for _,entry in ipairs(points)do
    if entry.world==world then
     if floor then table.insert(entry.tags,'T3MissionFlagNeedsFloor')end
     if ceiling then table.insert(entry.tags,'T3MissionFlagNeedsCeiling')end
    end
   end
   for _,cap in ipairs({{'Floor',floor},{'Ceiling',ceiling}})do
    if cap[2] and cap[1]~=omit then
     local entry=marker('T3MissionFlag'..cap[1],0,0)
     entry.at.Z=cap[2];points[#points+1]=entry
    end
   end
  end,
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
        self.assertEqual(list(f.pilot.messages.values()), ["T3FlagBoundaryDrop"])
        self.assertEqual(len(f.other.messages), 0)

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
        self.assertEqual(list(f.nextPawn.controller.messages.values()), ["T3FlagBoundaryReturn"])
        self.assertEqual(len(f.pilot.messages), 0)

    def test_vehicle_crossing_preserves_the_players_last_inside_sample(self):
        for transferred in (False, True):
            with self.subTest(transferred=transferred):
                f = self.fixture()
                flag = f.flags[1]
                flag.owner = f.pawn
                f.move(f.pawn, 975, 150, 680)
                self.assertTrue(f.start())
                f.enter_vehicle()
                if transferred:
                    flag.owner = f.vehicle
                f.move(f.vehicle, 1450, 157, 705)
                f.tick()
                self.assertFalse(flag.forced)
                self.assertEqual((flag.at.X, flag.at.Y, flag.at.Z), (975, 150, 680))
                self.assertEqual(list(f.pilot.messages.values()), ["T3FlagBoundaryDrop"])
                self.assertEqual(len(f.other.messages), 0)

    def test_failed_notification_does_not_stop_boundary_enforcement_or_other_notices(self):
        f = self.fixture()
        f.flags[1].owner = f.pawn
        f.flags[2].owner = f.nextPawn
        f.move(f.pawn, 900, 500, 400)
        f.move(f.nextPawn, -900, 500, 410)
        self.assertTrue(f.start())
        f.fail_notice()
        f.move(f.pawn, 1400, 500, 400)
        f.move(f.nextPawn, -1400, 500, 410)
        f.tick()
        self.assertEqual((f.flags[1].drops, f.flags[2].drops), (1, 1))
        self.assertEqual(list(f.other.messages.values()), ["T3FlagBoundaryDrop"])
        self.assertIn("MISSION_FLAG_LIMIT_NOTICE_UNAVAILABLE", f.notices[3])

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

    def test_altitude_crossing_releases_at_the_last_valid_sample_for_each_shape(self):
        polygon = [(-900, -900), (900, -900), (900, 900), (-900, 900)]
        for vertices in (None, polygon):
            for height, outside in ((500, 501), (-200, -201)):
                with self.subTest(vertices=vertices, height=height):
                    f = self.fixture(vertices)
                    f.altitude(-200, 500)
                    flag = f.flags[1]
                    flag.owner = f.pawn
                    f.move(f.pawn, 100, 50, height)
                    self.assertTrue(f.start())
                    f.tick()
                    self.assertEqual(flag.drops, 0)
                    f.move(f.pawn, 101, 52, outside)
                    f.tick()
                    self.assertEqual((flag.drops, flag.moves), (1, 1))
                    self.assertEqual((flag.at.X, flag.at.Y, flag.at.Z), (100, 50, height))
                    self.assertEqual(list(f.pilot.messages.values()), ["T3FlagBoundaryDrop"])

    def test_bad_height_markers_and_outside_stands_reject_startup(self):
        f = self.fixture()
        f.altitude(-100, 100, "Ceiling")
        with self.assertRaisesRegex(LuaError, "incomplete_height_markers"):
            f.start()
        f = self.fixture()
        f.altitude(200, 100)
        with self.assertRaisesRegex(LuaError, "invalid_height_range"):
            f.start()
        f = self.fixture()
        f.altitude(1, 300)
        with self.assertRaisesRegex(LuaError, "flag_outside_boundary"):
            f.start()

    def test_return_timer_clears_on_reentry_and_returns_the_flag_after_a_fresh_delay(self):
        f = self.fixture()
        f.return_timer(3)
        flag = f.flags[1]
        flag.owner = f.pawn
        f.move(f.pawn, 900, 0, 100)
        self.assertTrue(f.start())
        f.move(f.pawn, 1100, 0, 100)
        f.at_time(1)
        f.tick()
        f.at_time(3.9)
        f.tick()
        self.assertEqual(flag.drops, 0)
        f.move(f.pawn, 900, 0, 100)
        f.tick()
        f.move(f.pawn, 1100, 0, 100)
        f.at_time(4)
        f.tick()
        f.at_time(6.9)
        f.tick()
        self.assertEqual(flag.drops, 0)
        f.at_time(7)
        f.tick()
        self.assertTrue(flag.forced)
        self.assertEqual((flag.drops, flag.moves), (1, 0))
        self.assertIn('reason=timer_elapsed|elapsed=3.000', f.notices[4])
        self.assertEqual(list(f.pilot.messages.values()), ['T3FlagBoundaryGrace', 'T3FlagBoundaryGraceCleared',
                                                           'T3FlagBoundaryGrace', 'T3FlagBoundaryReturn'])

    def test_return_timer_survives_same_player_vehicle_transfer_and_ignores_old_carriers(self):
        for changed_player in (False, True):
            with self.subTest(changed_player=changed_player):
                f = self.fixture()
                f.return_timer(3)
                flag = f.flags[1]
                flag.owner = f.pawn
                f.move(f.pawn, 1100, 0, 100)
                self.assertTrue(f.start())
                f.at_time(2)
                if changed_player:
                    flag.owner = f.nextPawn
                    f.move(f.nextPawn, 1100, 0, 100)
                else:
                    f.enter_vehicle()
                    flag.owner = f.vehicle
                    f.move(f.vehicle, 1100, 0, 100)
                f.tick()
                f.at_time(3)
                f.tick()
                self.assertEqual(flag.drops, 0 if changed_player else 1)
                if changed_player:
                    self.assertEqual(list(f.pilot.messages.values()), ['T3FlagBoundaryGrace', 'T3FlagBoundaryGraceCleared'])
                    f.at_time(5)
                    f.tick()
                    self.assertEqual(flag.drops, 1)
                    self.assertEqual(list(f.other.messages.values()), ['T3FlagBoundaryGrace', 'T3FlagBoundaryReturn'])

    def test_required_return_timer_must_have_a_valid_marker(self):
        f = self.fixture()
        f.return_timer(4, True)
        with self.assertRaisesRegex(LuaError, 'incomplete_return_timer'):
            f.start()
        f = self.fixture()
        f.return_timer(61)
        with self.assertRaisesRegex(LuaError, 'missing_numbered_marker=T3MissionFlagReturnSeconds'):
            f.start()

    def test_polygon_marker_errors_fail_at_startup(self):
        for size in (900, 8_000_000):
            f = self.fixture([(-size, -size), (size, size), (size, -size), (-size, size)])
            with self.subTest(size=size), self.assertRaisesRegex(LuaError, "polygon_edges_intersect"):
                f.start()
        f = self.fixture([(-900, -900), (900, -900), (900, 900), (-900, 900)], claimed_size=5)
        with self.assertRaisesRegex(LuaError, "missing_polygon_vertex"):
            f.start()
