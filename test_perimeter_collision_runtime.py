"""Check the shared runtime wall mask against game-shaped Lua objects."""

from pathlib import Path
import unittest

from lupa.lua54 import LuaError, LuaRuntime


FIXTURE = r"""
return function(factory, bad)
 local seq=0
 local function object(name)
  seq=seq+1
  return {name=name,id=seq,GetAddress=function(self)return self.id end,GetFullName=function(self)return self.name end}
 end
 local world,elsewhere=object('World'),object('Other World')
 local function wall(class,owned,filtered,capability)
  local w=object(class..' wall')
  local c=object('Class /Script/Engine.'..class)
  w.GetWorld=function()return owned end
  w.GetClass=function()return c end
  w.ActorHasTag=function(_,tag)
   return tag=='T3MissionPerimeter' or (filtered and tag=='T3MissionPerimeterFiltered') or tag==capability
  end
  local shape=object(class..' collision')
  shape.responses={}
  for channel=0,31 do shape.responses[channel]=2 end
  shape.writes=0
  shape.GetWorld=function()return owned end
  shape.GetOwner=function()return w end
  shape.GetCollisionObjectType=function()return 0 end
  shape.GetCollisionResponseToChannel=function(self,channel)return self.responses[channel]end
  shape.SetCollisionResponseToChannel=function(self,channel,value)
   self.responses[channel]=value;self.writes=self.writes+1
  end
  w[class=='TriggerBox'and'CollisionComponent'or'StaticMeshComponent']=shape
  return w,shape
 end
 local box,box_shape=wall('TriggerBox',world,true,'T3MissionPerimeterBlockVehicle')
 local mesh,mesh_shape=wall('StaticMeshActor',world,true,bad and 'wrong' or 'T3MissionPerimeterBlockPawn')
 local default,default_shape=wall('TriggerBox',world,false)
 local other,other_shape=wall('StaticMeshActor',elsewhere,true,'T3MissionPerimeterBlockPawn')
 local context=object('Draft mode');context.GetWorld=function()return world end
 local definitions={TriggerBox={box,default},StaticMeshActor={mesh,other}}
 FindAllOf=function(class)return assert(definitions[class])end
 FName=function(value)return value end
 local fields={GetCollisionObjectType={'ReturnValue'},GetCollisionResponseToChannel={'Channel','ReturnValue'},
  SetCollisionResponseToChannel={'Channel','NewResponse'}}
 StaticFindObject=function(path)
  local fn=object('Function '..path)
  local field=assert(fields[path:match('([^:]+)$')])
  fn.ForEachProperty=function(_,cb)
   for i,key in ipairs(field)do
    cb({GetFullName=function()return 'ByteProperty '..path..':'..key end,
        GetOffset_Internal=function()return i-1 end})
   end
  end
  return fn
 end
 local notices={}
 local apply=factory({valid=function(value)return value~=nil end,scope=function()end,
  report=function(line)notices[#notices+1]=line end})
 return {run=function()return apply(context)end,
  defaults_only=function()definitions={TriggerBox={default},StaticMeshActor={other}}end,
  box=box_shape,mesh=mesh_shape,default=default_shape,other=other_shape,notices=notices}
end
"""


class PerimeterCollisionRuntimeTest(unittest.TestCase):
    def fixture(self, *, malformed=False):
        runtime = LuaRuntime()
        module = Path(__file__).parent / "runtime-fixes/mission_perimeter_collision.lua"
        return runtime.execute(FIXTURE)(runtime.execute(module.read_text(encoding="utf-8")), malformed)

    def test_restores_all_thirty_two_slots_only_on_owned_marked_walls(self):
        fixture = self.fixture()
        self.assertTrue(fixture.run())
        for shape, blocked in ((fixture.box, 6), (fixture.mesh, 2)):
            self.assertEqual([shape.responses[index] for index in range(32)],
                             [2 if index == blocked else 0 for index in range(32)])
            self.assertEqual(shape.writes, 31)
        self.assertEqual((fixture.other.writes, fixture.default.writes), (0, 0))
        self.assertTrue(fixture.run())
        self.assertEqual((fixture.mesh.writes, fixture.box.writes), (31, 31))
        self.assertEqual(fixture.notices[2], "MISSION_PERIMETER_COLLISION_READY|faces=2|corrected=0")

    def test_malformed_tag_is_rejected_before_any_valid_wall_is_modified(self):
        fixture = self.fixture(malformed=True)
        with self.assertRaisesRegex(LuaError, "perimeter_collision_empty_filter"):
            fixture.run()
        self.assertEqual((fixture.box.writes, fixture.mesh.writes), (0, 0))

    def test_unfiltered_drafts_remain_unchanged(self):
        fixture = self.fixture()
        fixture.defaults_only()
        self.assertFalse(fixture.run())
        self.assertEqual((fixture.default.writes, fixture.other.writes), (0, 0))
