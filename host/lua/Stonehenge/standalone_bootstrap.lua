-- Standalone match prototype. Reacquires all Unreal objects each invocation.
local scope={map_package="/Game/Maps/Community/Stonehenge/Arena_Stonehenge_P",expected_players=24,players_per_team=12,match_id="Stonehenge",game_mode_class="/Game/Topaz/Game/BP_TopazGameMode_CTF_Persistent_BotBackfill.BP_TopazGameMode_CTF_Persistent_BotBackfill_C"}
assert(scope.expected_players==24 and scope.players_per_team==12,"Unverified player capacity")
assert(scope.native_persistent_backfill==nil or type(scope.native_persistent_backfill)=="boolean","Invalid persistent backfill selection")
if scope.native_persistent_backfill then
    assert(scope.game_mode_class=="/Game/Topaz/Game/BP_TopazGameMode_TeamDeathmatch_BotFill.BP_TopazGameMode_TeamDeathmatch_BotFill_C","Persistent backfill requires the verified TDM class")
end
return function(state)
    local modes=FindAllOf("TopGameMode_Topaz") or {}
    if #modes~=1 then return "Waiting for selected local map" end
    local gm=modes[1];local identity=gm:GetFullName()
    local map_identity=scope.map_package.."."..scope.map_package:match("([^/]+)$")..":"
    if not identity:find(map_identity,1,true) then return "Waiting for selected local map" end
    if scope.game_mode_class then
        local kind=scope.game_mode_class:sub(1,8)=="/Script/" and "Class " or "BlueprintGeneratedClass "
        assert(gm:GetClass():GetFullName()==kind..scope.game_mode_class,"Game mode differs from selected local match")
    end
    if scope.game_mode_base_class then
        local base=StaticFindObject(scope.game_mode_base_class)
        assert(base:IsValid() and gm:IsA(base),"Map-selected mode does not match the requested mode ancestry")
        assert(gm.DefaultPawnClass:IsValid() and gm.DefaultPawnClass:GetFullName()~="Class /Script/Engine.DefaultPawn","Map-selected mode has placeholder pawn defaults")
    end
    local system=StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    assert(gm:HasAuthority() and system:IsStandalone(gm)==true,"Refuse a networked world")
    if state.world~=identity then
        state.world=identity;state.finished=false;state.status=nil;state.start=os.time();state.callbacks=false
    end
    if state.finished then return state.status end
    assert(os.time()-state.start<600,"Local initialization exceeded ten minutes")
    assert(gm.ExpectedPlayerCount==scope.expected_players,"Unexpected game mode player count")
    if scope.native_persistent_backfill and (gm.bEnblePersistentMatchCycle~=true or gm.bAllowAIBackfill~=true) then
        gm.bEnblePersistentMatchCycle=true
        gm.bAllowAIBackfill=true
        print("[LocalCTF] Selected native persistent TDM backfill enabled\n")
    end
    gm.MembersPerTeamOverride=scope.players_per_team
    local sessions=FindAllOf("RH_OfflineSession") or {}
    if #sessions==0 then
        local boot=FindAllOf("RH_GameInstanceClientBootstrapper") or {}
        if #boot~=1 then return "Waiting for local session bootstrapper" end
        boot[1]:CreateOfflineSession()
    end
    local players=FindAllOf("ValPlayerController") or {}
    if #players~=1 then return "Waiting for local player" end
    local pc=players[1]
    assert(pc:IsLocalPlayerController(),"Expected local player")
    local ps=pc.PlayerState
    if not ps:IsValid() or (ps.r_TeamId~=1 and ps.r_TeamId~=2) then return "Waiting for normal team assignment" end
    local states=FindAllOf("ValGameState") or {}
    if #states~=1 then return "Waiting for selected game state" end
    local glc=states[1].GameLoadoutComponent
    if not glc:IsValid() or not glc:IsComponentReady() then return "Waiting for built-in game loadouts" end
    local plc=pc.PlayerLoadoutComponent
    if not plc:IsValid() then return "Waiting for player loadout component" end
    if not plc:IsComponentReady() and not state.callbacks then
        -- No account inventory is fetched: generate this mode's built-in defaults.
        plc:OnGameStateSet(states[1]);plc:OnGameLoadoutsReady()
        plc:OnPlayerInventoryLoaded();plc:OnPlayerSettingsLoaded()
        state.callbacks=true
    end
    if not plc:IsComponentReady() then return "Waiting for local default loadouts" end
    if not pc.Pawn:IsValid() then
        state.start=os.time() -- Waiting for the human does not consume setup time.
        return "Choose a loadout to enter "..scope.match_id
    end
    state.finished=true;state.status="LOCAL_PAWN_AVAILABLE"
    return state.status
end
