-- A cooked community map opts in with rectangle or indexed polygon TargetPoints.
local MAX_VERTICES=32
local function cross(ax,ay,bx,by)return ax*by-ay*bx end
local function turn(a,b,p)return cross(b.X-a.X,b.Y-a.Y,p.X-a.X,p.Y-a.Y)end
local function between(a,b,p)
 return p.X>=math.min(a.X,b.X)and p.X<=math.max(a.X,b.X)
    and p.Y>=math.min(a.Y,b.Y)and p.Y<=math.max(a.Y,b.Y)
end
local function intersects(a,b,c,d)
 local ac,ad,ca,cb=turn(a,b,c),turn(a,b,d),turn(c,d,a),turn(c,d,b)
 local opposite_ab=ac~=0 and ad~=0 and (ac<0)~=(ad<0)
 local opposite_cd=ca~=0 and cb~=0 and (ca<0)~=(cb<0)
 return (opposite_ab and opposite_cd)or (ac==0 and between(a,b,c))or(ad==0 and between(a,b,d))
    or (ca==0 and between(c,d,a))or(cb==0 and between(c,d,b))
end
local function polygon_limit(vertices)
 local count=#vertices
 local function next_index(n)return n%count+1 end
 for i,a in ipairs(vertices)do
  local b=vertices[next_index(i)]
  assert(turn(a,b,vertices[next_index(next_index(i))])~=0,'mission_flag_limit_degenerate_polygon_edge')
  for j=i+2,count do
   if i~=next_index(j)then
    assert(not intersects(a,b,vertices[j],vertices[next_index(j)]),'mission_flag_limit_polygon_edges_intersect')
   end
  end
 end
 local function inside(p)
  local result=false
  for i,a in ipairs(vertices)do
   local b=vertices[next_index(i)]
   local side=turn(a,b,p)
   if side==0 and between(a,b,p)then return true end
   if (a.Y>p.Y)~=(b.Y>p.Y)and (side>0)==(b.Y>a.Y)then result=not result end
  end
  return result
 end
 local function crossed(a,b)
  -- On a concave polygon, two legal samples may straddle a notch. Check
  -- the intervals between intersections so a tangent touch stays legal.
  local dx,dy=b.X-a.X,b.Y-a.Y
  if dx==0 and dy==0 then return false end
  local cuts={0,1}
  for i,c in ipairs(vertices)do
   local d=vertices[next_index(i)]
   local ex,ey=d.X-c.X,d.Y-c.Y
   local determinant=cross(dx,dy,ex,ey)
   if determinant~=0 then
    local t=cross(c.X-a.X,c.Y-a.Y,ex,ey)/determinant
    local u=cross(c.X-a.X,c.Y-a.Y,dx,dy)/determinant
    if t>0 and t<1 and u>=0 and u<=1 then cuts[#cuts+1]=t end
   end
  end
  table.sort(cuts)
  for i=2,#cuts do
   if cuts[i]-cuts[i-1]>1e-7 then
    local middle=(cuts[i-1]+cuts[i])/2
    if not inside({X=a.X+middle*dx,Y=a.Y+middle*dy})then return true end
   end
  end
  return false
 end
 return inside,crossed
end
return function(env)
 local valid,report,scope=env.valid,env.report,env.scope
 local function same(a,b)
  return valid(a)and valid(b)and a:GetAddress()==b:GetAddress()and a:GetFullName()==b:GetFullName()
 end
 local function finite(n)return type(n)=='number'and n==n and math.abs(n)<=1e7 end
 local function point(actor)
  local v=actor:K2_GetActorLocation()
  assert(finite(v.X)and finite(v.Y)and finite(v.Z),'mission_flag_limit_invalid_location')
  return {X=v.X,Y=v.Y,Z=v.Z}
 end
 local function tagged_number(actor,prefix,first)
  local result
  for n=first,MAX_VERTICES do
   if actor:ActorHasTag(FName(prefix..n))then
    assert(not result,'mission_flag_limit_ambiguous_polygon_tag')
    result=n
   end
  end
  return assert(result,'mission_flag_limit_missing_polygon_tag')
 end
 local function native(path,fields)
  local fn=StaticFindObject(path)
  assert(valid(fn)and fn:GetFullName()=='Function '..path,'mission_flag_limit_missing_function')
  local n=0
  fn:ForEachProperty(function(p)
   n=n+1
   local wanted=assert(fields[n],'mission_flag_limit_extra_parameter')
   assert(p:GetFullName()==wanted[2]..' '..path..':'..wanted[1]and p:GetOffset_Internal()==wanted[3],
          'mission_flag_limit_changed_signature='..path)
  end)
  assert(n==#fields,'mission_flag_limit_missing_parameter')
 end
 return function(gm)
  scope()
  assert(valid(gm)and gm:HasAuthority(),'mission_flag_limit_authority_required')
  local world=gm:GetWorld()
  assert(valid(world),'mission_flag_limit_world_required')
  local function in_world(actor)return valid(actor)and same(actor:GetWorld(),world)end
  local lower,upper
  local vertices,vertex_count={},nil
  for _,actor in ipairs(FindAllOf('TargetPoint')or{})do
   if in_world(actor)then
    if actor:ActorHasTag(FName('T3MissionFlagMin'))then
     assert(not lower,'mission_flag_limit_duplicate_lower_marker');lower=point(actor)
    end
    if actor:ActorHasTag(FName('T3MissionFlagMax'))then
     assert(not upper,'mission_flag_limit_duplicate_upper_marker');upper=point(actor)
    end
    if actor:ActorHasTag(FName('T3MissionFlagPolygon'))then
     local index=tagged_number(actor,'T3MissionFlagVertex',1)
     local size=tagged_number(actor,'T3MissionFlagVertexCount',3)
     assert(not vertices[index] and index<=size and (not vertex_count or vertex_count==size),
            'mission_flag_limit_invalid_polygon_index')
     vertex_count=size
     vertices[index]=point(actor)
    end
   end
  end
  if not lower and not upper and not vertex_count then return false end
  local inside,crossed
  if vertex_count then
   assert(not lower and not upper,'mission_flag_limit_mixed_shapes')
   for n=1,vertex_count do assert(vertices[n],'mission_flag_limit_missing_polygon_vertex')end
   inside,crossed=polygon_limit(vertices)
  else
   assert(lower and upper and lower.X<upper.X and lower.Y<upper.Y,'mission_flag_limit_invalid_markers')
   inside=function(v)return v.X>=lower.X and v.X<=upper.X and v.Y>=lower.Y and v.Y<=upper.Y end
  end
  native('/Script/Topaz.TopPickup_Flag:Drop',{{'bScored','BoolProperty',0},
   {'bInheritVelocity','BoolProperty',1},{'bForceReturnToStart','BoolProperty',2}})
  native('/Script/Topaz.TopPickup_Flag:GetFlagState',{{'Querier','ObjectProperty',0},{'ReturnValue','EnumProperty',8}})
  native('/Script/Engine.Actor:K2_SetActorLocation',{{'NewLocation','StructProperty',0},{'bSweep','BoolProperty',24},
   {'SweepHitResult','StructProperty',32},{'bTeleport','BoolProperty',264},{'ReturnValue','BoolProperty',265}})
  native('/Script/Engine.Actor:GetAttachParentActor',{{'ReturnValue','ObjectProperty',0}})
  native('/Script/Engine.Pawn:GetController',{{'ReturnValue','ObjectProperty',0}})
  local pawn_class=StaticFindObject('/Script/Engine.Pawn')
  local character_class=StaticFindObject('/Script/Valhalla.ValCharacter')
  local player_class=StaticFindObject('/Script/Engine.PlayerController')
  local states=StaticFindObject('/Script/Topaz.ETopFlagState')
  assert(valid(pawn_class)and valid(character_class)and valid(player_class)and valid(states),
         'mission_flag_limit_native_types_required')
  local function carrying_pawn(actor)
   return in_world(actor) and actor:HasAuthority() and actor:IsA(pawn_class)
  end
  local function support_pawn(carrier)
   if carrier:IsA(character_class)then
    local attached=carrier:GetAttachParentActor()
    if carrying_pawn(attached) and not attached:IsA(character_class)then return attached end
   end
   return carrier
  end
  local function player_controller(carrier,support)
   local player=carrier.PlayerState
   local pc=carrier:GetController()
   if not valid(pc) and in_world(player)then pc=player:GetOwner() end
   if in_world(pc) and pc:HasAuthority() and pc:IsA(player_class)
      and (same(pc.Pawn,carrier) or (same(pc.Pawn,support) and same(pc.PlayerState,player)))then
    return pc
   end
  end
  local notice_ready
  local function send_notice(pc,has_previous)
   if not pc or notice_ready==false then return end
   if not notice_ready then
    local checked,why=pcall(native,'/Script/Engine.PlayerController:ClientMessage',
                           {{'S','StrProperty',0},{'Type','NameProperty',16},{'MsgLifeTime','FloatProperty',24}})
    notice_ready=checked
    if not checked then
     report('MISSION_FLAG_LIMIT_NOTICE_UNAVAILABLE|'..tostring(why):sub(1,260))
     return
    end
   end
   local ok,why=pcall(function()
    if has_previous then
     pc:ClientMessage('Flag dropped at the mission boundary.',FName('T3FlagBoundaryDrop'),4.0)
    else
     pc:ClientMessage('Flag returned home because you were out of bounds.',FName('T3FlagBoundaryReturn'),4.0)
    end
   end)
   if not ok then
    report('MISSION_FLAG_LIMIT_NOTICE_UNAVAILABLE|'..tostring(why):sub(1,260))
   end
  end
  local dropped
  states:ForEachName(function(name,value)
   if name:ToString():match('([^:]+)$')=='Dropped' then dropped=value end
  end)
  assert(type(dropped)=='number','mission_flag_limit_dropped_state_required')
  local flags,samples={},{}
  for _,flag in ipairs(FindAllOf('TopPickup_Flag')or{})do
   if in_world(flag)and flag:HasAuthority()then
    local team=flag.r_TeamId
    assert((team==1 or team==2)and not flags[team],'mission_flag_limit_ambiguous_flag')
    flags[team]=flag
   end
  end
  assert(flags[1]and flags[2],'mission_flag_limit_two_flags_required')
  if vertex_count then
   for _,flag in ipairs(flags)do assert(inside(point(flag)),'mission_flag_limit_flag_outside_polygon')end
  end
  local function tick()
   if not in_world(gm)or not gm:HasAuthority()then return false end
   scope()
   if gm:GetMatchState():ToString()~='InProgress' then samples={};return true end
   local gs=gm.GameState
   if not in_world(gs)or gs.r_GameplayPhaseInfo.Phase.TagName:ToString()~='GamePhase.GameplayStartPhase' then
    samples={};return true
   end
   for team,flag in ipairs(flags)do
    if not in_world(flag)or not flag:HasAuthority()then return false end
    local carrier=flag:GetOwner()
    if not carrying_pawn(carrier)then
     samples[team]=nil
    else
     local support=support_pawn(carrier)
     local pc=player_controller(carrier,support)
     local here=point(support)
     local last=samples[team]
     local same_carrier=last and same(last.carrier,carrier) and
      ((not last.pc and not pc) or same(last.pc,pc))
     -- Carry the last valid point through a verified possession switch to
     -- or from a vehicle, without inheriting a different player's sample.
     local seat_transfer=last and same(last.pc,pc) and
      (same(last.support,carrier) or same(last.carrier,support)
       or (carrying_pawn(last.carrier) and same(support_pawn(last.carrier),support)))
     local previous=(same_carrier or seat_transfer) and last.location or nil
     if inside(here)and not(crossed and previous and crossed(previous,here))then
      samples[team]={carrier=carrier,support=support,pc=pc,location=here}
     else
      samples[team]=nil
      flag:Drop(false,false,previous==nil)
      assert(not valid(flag:GetOwner()),'mission_flag_limit_native_drop_failed')
      if previous then
       assert(flag:GetFlagState(nil)==dropped,'mission_flag_limit_native_dropped_state_missing')
       flag:K2_SetActorLocation(previous,false,{},true)
       local landed=point(flag)
       assert(inside(landed)and math.abs(landed.X-previous.X)<=1 and math.abs(landed.Y-previous.Y)<=1
              and math.abs(landed.Z-previous.Z)<=1,'mission_flag_limit_drop_position_failed')
       report(('MISSION_FLAG_LIMIT_DROP|team=%d|x=%.2f|y=%.2f|z=%.2f'):format(team,landed.X,landed.Y,landed.Z))
      else
       report('MISSION_FLAG_LIMIT_RETURN|team='..team..'|reason=no_legal_carrier_sample')
      end
      send_notice(pc,previous~=nil)
     end
    end
   end
   return true
  end
  local function poll()
   local ok,again=pcall(tick)
   if ok and again then ExecuteInGameThreadWithDelay(75,poll)
   elseif not ok then report('MISSION_FLAG_LIMIT_ERROR|'..tostring(again))end
  end
  report('MISSION_FLAG_LIMIT_READY')
  poll()
  return true
 end
end
