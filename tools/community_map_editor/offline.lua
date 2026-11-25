-- Only an isolated, rendered map editor playtest can load this adapter.
local EXPECTED,OUTPUT=@@EXPECTED@@,@@OUTPUT@@
local PATH=@@DRAFT_WORLD@@
local starts,flags=@@DRAFT_STARTS@@,@@DRAFT_FLAGS@@
local bootstrap=assert(loadfile(@@BOOTSTRAP@@))()
local state,stands,pickups={},{},{}
local installed,linked,last
local function valid(actor)return actor~=nil and actor:IsValid()end
local function report(line)
 local output=assert(io.open(OUTPUT,'ab'));output:write(line,'\n');output:close()
end
local function asset(path)
 local result=StaticFindObject(path)
 if not valid(result)then result=LoadAsset(path)end
 assert(valid(result),'Missing CTF asset: '..path)
 return result
end
local function scope()
 local sys=asset('/Script/Engine.Default__KismetSystemLibrary')
 local cmd=sys:GetCommandLine();cmd=type(cmd)=='string' and cmd or cmd:ToString()
 assert(IsInGameThread()==true and cmd:match('^%s*(.-)%s*$')==EXPECTED,'Unexpected draft launch command')
 return sys
end
local start_flag_limit=(function()
@@DRAFT_FLAG_LIMIT@@
end)()({valid=valid,report=report,scope=scope})
local configure_perimeter=(function()
@@DRAFT_COLLISION_FILTER@@
end)()({valid=valid,report=report,scope=scope})
local warn_players=(function()
@@DRAFT_BOUNDARY_WARNING@@
end)()({valid=valid,report=report,scope=scope})
local outside_document=@@DRAFT_OUTSIDE_AUDIO@@
local outside_audio=(function()
@@DRAFT_OUTSIDE_AUDIO_RUNTIME@@
end)()
local outside_started=false
local function start_outside_audio(gm)
 if not outside_document or outside_started then return end
 outside_started=true
 local owner=gm:GetWorld():GetAddress()
 local math_library=asset('/Script/Engine.Default__KismetMathLibrary')
 local statics=asset('/Script/Engine.Default__GameplayStatics')
 local listener
 local function update()
  scope()
  if not valid(gm) or gm:GetWorld():GetAddress()~=owner then return end
  if not listener then
   listener=outside_audio.bind({valid=valid,find=FindAllOf,name=FName,local_point=function(actor,p)
    return math_library:InverseTransformLocation(actor:GetTransform(),{X=p[1],Y=p[2],Z=p[3]})
   end},owner,outside_document)
   if listener then report('DRAFT_OUTSIDE_AUDIO_READY')end
  end
  local camera=statics:GetPlayerCameraManager(gm,0)
  if listener and valid(camera)then
   local p=camera:GetCameraLocation()
   listener({p.X,p.Y,p.Z})
  end
  ExecuteInGameThreadWithDelay(50,function()
   local ok,why=pcall(update)
   if not ok then report('RUNTIME_ERROR|'..tostring(why))end
  end)
 end
 update()
end
local function objective(gm,class,team,marker,isFlag)
 local api=asset('/Script/Engine.Default__GameplayStatics')
 local half=math.rad(marker[4])/2
 local transform={Rotation={X=0,Y=0,Z=math.sin(half),W=math.cos(half)},
  Translation={X=marker[1],Y=marker[2],Z=marker[3]+(isFlag and 40 or 115.87)},Scale3D={X=1,Y=1,Z=1}}
 local actor=api:BeginDeferredActorSpawnFromClass(gm,class,transform,1,nil,0)
 assert(valid(actor),'Could not create the draft objective')
 actor.TeamDefinition=FName(team==1 and 'TeamA' or 'TeamB')
 if isFlag then actor.r_TeamId=team else actor.TeamNum=team end
 actor=api:FinishSpawningActor(actor,transform,0)
 assert(valid(actor),'Could not finish the draft objective')
 return actor
end
local function install(gm)
 local world=gm:GetWorld()
 local startClass=asset('/Script/Topaz.TopPlayerStart')
 local standClass=asset('/Game/Topaz/Game/Objectives/BP_FlagStand.BP_FlagStand_C')
 local flagClass=asset('/Game/Topaz/Pickups/BP_TopazPickup_Flag.BP_TopazPickup_Flag_C')
 for team=1,2 do
  for _,position in ipairs(starts[team])do
   local start=world:SpawnActor(startClass,{X=position[1],Y=position[2],Z=position[3]},
      {Pitch=0,Yaw=position[4],Roll=0})
   assert(valid(start),'Could not create a draft spawn')
   start.TeamNumber=team
  end
  stands[team]=objective(gm,standClass,team,flags[team],false)
  pickups[team]=objective(gm,flagClass,team,flags[team],true)
 end
 installed=true
 report('DRAFT_CTF_ACTORS|starts='..(#starts[1]+#starts[2])..'|flags=2|stands=2')
end
local function step()
 local sys=scope()
 local modes=FindAllOf('TopGameMode_Topaz') or {}
 if #modes~=1 then return end
 local gm=modes[1]
 if not gm:GetFullName():find(PATH,1,true)then return end
 assert(gm:HasAuthority() and sys:IsStandalone(gm),'Draft CTF must run in an isolated offline world')
 if not installed then
  configure_perimeter(gm)
  install(gm)
  start_outside_audio(gm)
 end
 local status=bootstrap(state)
 local players=FindAllOf('ValPlayerController')or{}
 if #players==1 and valid(players[1])then warn_players(players[1],players[1].PlayerState)end
 if status~=last then report(status);last=status end
 if not linked and (state.finished or status:find('Choose a loadout',1,true))then
  local names={}
  for _,team in ipairs(FindAllOf('TeamState')or{})do
   if valid(team)then names[team.r_TeamDefinition:ToString()]=team:GetTeamId()end
  end
  assert(names.TeamA==1 and names.TeamB==2,'Draft CTF team registration failed')
  for team=1,2 do
   local flag,stand=pickups[team],stands[team]
   local before=flag.r_TeamId
   stand.TeamNum=team;flag.r_TeamId=team;flag:OnRep_TeamId(before)
   stand:OnBeginOverlapActor(flag)
   assert(stand:IsFlagAtHome(),'Draft flag did not link to its stand')
  end
  start_flag_limit(gm)
  report('DRAFT_CTF_READY|flags_linked=2')
  linked=true
 end
end
local function tick()
 ExecuteInGameThreadWithDelay(750,function()
  local ok,why=pcall(step)
  if not ok then report('RUNTIME_ERROR|'..tostring(why));return end
  if not (linked and state.finished)then tick()end
 end)
end
report('DRAFT_CTF_ADAPTER_LOADED')
tick()
