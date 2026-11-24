"""Run the warning service with a moving local player and game-shaped UI."""

from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


class MissionBoundaryWarningTest(unittest.TestCase):
    def test_crossing_warns_locally_and_reentry_and_travel_clear_the_hud(self):
        runtime = LuaRuntime()
        source = Path(__file__).parent / "runtime-fixes/mission_boundary_warning.lua"
        factory = runtime.execute(source.read_text(encoding="utf-8"))
        runtime.execute(r"""
        return function(factory)
         local stamp, timer, reports = 10, nil, {}
         local world, foreign = {}, {}
         local function object(name, owner, tags)
          local row = {tags=tags or {}}
          row.GetFullName=function()return name end
          row.GetAddress=function()return row end
          row.GetWorld=function()return owner end
          row.ActorHasTag=function(_,tag)return row.tags[tag]==true end
          row.K2_GetActorLocation=function()return row.point end
          return row
         end
         world,foreign=object('World'),object('OtherWorld')
         local flag, panel = object('Flags',world), object('Canvas',world)
         flag.GetParent=function()return panel end
         local canvasClass, textClass = {}, {}
         panel.IsA=function(_,class)return class==canvasClass end
         local slot={}
         for _,fn in ipairs({'SetAnchors','SetAlignment','SetAutoSize','SetPosition','SetZOrder'})do
          slot[fn]=function()end
         end
         panel.AddChildToCanvas=function(_,label)label.parent=panel;return slot end
         local overlay=object('/Engine/Transient.HUD',world)
         overlay.Flags=flag
         local created,removed=0,0
         StaticConstructObject=function(class,owner)
          assert(class==textClass and owner==overlay)
          created=created+1
          local row=object('Label',world)
          row.GetParent=function()return row.parent end
          row.RemoveFromParent=function()row.parent=nil;removed=removed+1 end
          row.SetText=function(_,text)assert(text:find('OUT OF BOUNDS',1,true))end
          for _,fn in ipairs({'SetShadowOffset','SetVisibility','SetColorAndOpacity','SetShadowColorAndOpacity'})do
           row[fn]=function()end
          end
          return row
         end
         StaticFindObject=function(path)
          return ({['/Script/UMG.TextBlock']=textClass,['/Script/UMG.CanvasPanel']=canvasClass})[path]
         end
         FText=function(text)return text end
         FName=function(name)return name end
         local cue=object('Sound',world,{
          T3MissionWarningCue=true,T3MissionWarningCount8=true,T3MissionWarningCeiling=true,
         })
         local plays=0
         cue.AudioComponent={GetOwner=function()return cue end,Sound={},Play=function(_,offset)
          assert(offset==0);plays=plays+1
         end}
         local points={}
         for i,p in ipairs({{-10,-10},{10,-10},{10,10},{2,10},{2,0},{-2,0},{-2,10},{-10,10}}) do
          local marker=object('Vertex'..i,world,{T3MissionWarning=true,
                                                ['T3MissionWarningVertex'..i]=true})
          marker.point={X=p[1],Y=p[2],Z=0}
          points[i]=marker
         end
         local cap=object('Cap',world,{T3MissionWarning=true,T3MissionWarningCeiling=true})
         cap.point={X=0,Y=0,Z=10}
         points[#points+1]=cap
         local foreignCue=object('Foreign',foreign,{T3MissionWarningCue=true})
         FindAllOf=function(kind)
          return ({AmbientSound={cue,foreignCue},TargetPoint=points,UI_Game_Overlays_C={overlay}})[kind]
         end
         local ps,pawn,pc=object('PS',world),object('Pawn',world),object('PC',world)
         pc.PlayerState,pc.Pawn=ps,pawn
         pc.IsLocalPlayerController=function()return true end
         ExecuteInGameThreadWithDelay=function(ms,fn)assert(ms==100 and not timer);timer=fn;return 1 end
         local function at(x,y,z)
          pawn.point={X=x,Y=y,Z=z}
          local fn=assert(timer,reports[#reports]);timer=nil;fn()
         end
         local service=factory({valid=function(value)return value~=nil end,scope=function()end,
                                report=function(event)reports[#reports+1]=event end,now=function()return stamp end})
         service(pc,ps)
         at(-5,0,0);at(5,0,0) -- Touching the notch edge is legal.
         assert(plays==0 and created==0)
         at(5,8,0);at(-5,8,0) -- Cross outside and back between samples.
         assert(plays==1 and created==1)
         stamp=14
         at(-5,8,0)
         assert(removed==1)
         at(-5,8,11);at(-5,8,14)
         assert(plays==2 and created==2)
         at(-5,8,2)
         assert(removed==2)
         at(-5,8,14)
         ps.r_bDead=true;at(-5,8,14)
         assert(plays==3 and removed==3)
         ps.r_bDead=false;at(-5,8,14)
         assert(plays==3 and created==4) -- Outside on a new life is not a crossing.
         pc.GetWorld=function()return foreign end
         at(-5,8,14)
         assert(removed==4 and not timer)
         assert(#reports==4 and reports[1]=='MISSION_BOUNDARY_WARNING_READY')
        end
        """)(factory)
