#include "CommunityMapMotionLibrary.h"
#include "CommunityMapGraph.h"

#include "EdGraphSchema_K2.h"
#include "Engine/Level.h"
#include "Engine/LevelScriptBlueprint.h"
#include "GameFramework/Pawn.h"
#include "K2Node_ActorBoundEvent.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_Literal.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "LevelSequenceActor.h"
#include "MovieSceneSequencePlayer.h"

bool AddTimedSequenceStart(FCommunityMapGraph& Graph, ALevelSequenceActor* Sequence, UEdGraphPin* Exec, int32 X, int32 Y)
{
    const UFunction* GetPlayer = ALevelSequenceActor::StaticClass()->FindFunctionByName(TEXT("GetSequencePlayer"));
    const UClass* PlayerType = UMovieSceneSequencePlayer::StaticClass();
    const UFunction* IsPlaying = PlayerType->FindFunctionByName(TEXT("IsPlaying"));
    const UFunction* IsPaused = PlayerType->FindFunctionByName(TEXT("IsPaused"));
    const UFunction* Play = PlayerType->FindFunctionByName(TEXT("Play"));
    const UFunction* Or = UKismetMathLibrary::StaticClass()->FindFunctionByName(TEXT("BooleanOR"));
    if (!GetPlayer || !IsPlaying || !IsPaused || !Play || !Or)
    {
        return false;
    }
    auto* Literal = Graph.Add<UK2Node_Literal>(X, Y + 220, [Sequence](auto& Node) { Node.SetObjectRef(Sequence); });
    auto* Player = Graph.Call(GetPlayer, X + 220, Y + 220);
    auto* Playing = Graph.Call(IsPlaying, X + 470, Y + 185);
    auto* Idle = Graph.Add<UK2Node_IfThenElse>(X + 940, Y);
    auto* Start = Graph.Call(Play, X + 1170, Y);
    UEdGraphPin* PlayerObject = Player->GetReturnValuePin();
    UEdGraphPin* Busy = Playing->GetReturnValuePin();
    bool bOK = Graph.Connect(Literal->GetValuePin(), Player->FindPin(UEdGraphSchema_K2::PN_Self))
        && Graph.Connect(PlayerObject, Playing->FindPin(UEdGraphSchema_K2::PN_Self));
    if (Sequence->PlaybackSettings.bPauseAtEnd)
    {
        // The paused endpoint is the opened state. Every sender checks the same sequence player.
        auto* Paused = Graph.Call(IsPaused, X + 470, Y + 320);
        auto* Started = Graph.Call(Or, X + 705, Y + 240);
        bOK = Graph.Connect(PlayerObject, Paused->FindPin(UEdGraphSchema_K2::PN_Self))
            && Graph.Connect(Busy, Started->FindPin(TEXT("A")))
            && Graph.Connect(Paused->GetReturnValuePin(), Started->FindPin(TEXT("B"))) && bOK;
        Busy = Started->GetReturnValuePin();
    }
    return Graph.Connect(Exec, Idle->GetExecPin())
        && Graph.Connect(Busy, Idle->GetConditionPin())
        && Graph.Connect(Idle->GetElsePin(), Start->GetExecPin())
        && Graph.Connect(PlayerObject, Start->FindPin(UEdGraphSchema_K2::PN_Self)) && bOK;
}

bool UCommunityMapMotionLibrary::ConfigurePermanentDoor(ALevelSequenceActor* Sequence)
{
    if (!IsValid(Sequence) || !Sequence->GetSequence() || Sequence->PlaybackSettings.bAutoPlay
        || Sequence->PlaybackSettings.LoopCount.Value != 0)
    {
        return false;
    }
    Sequence->Modify();
    Sequence->PlaybackSettings.bRestoreState = false;
    Sequence->PlaybackSettings.bPauseAtEnd = true;
    return true;
}

bool UCommunityMapMotionLibrary::BindTimedDoor(AActor* Brush, ALevelSequenceActor* Sequence, bool bPlayerOnly)
{
    if (!IsValid(Brush) || !IsValid(Sequence) || !Brush->GetLevel() || Brush->GetLevel() != Sequence->GetLevel())
    {
        return false;
    }
    ULevelScriptBlueprint* Blueprint = Brush->GetLevel()->GetLevelScriptBlueprint();
    UEdGraph* Graph = Blueprint ? FBlueprintEditorUtils::FindEventGraph(Blueprint) : nullptr;
    const auto* Hit = FindFProperty<FMulticastDelegateProperty>(AActor::StaticClass(), GET_MEMBER_NAME_CHECKED(AActor, OnActorHit));
    const UFunction* HasAuthority = AActor::StaticClass()->FindFunctionByName(TEXT("HasAuthority"));
    const UFunction* IsPlayer = APawn::StaticClass()->FindFunctionByName(TEXT("IsPlayerControlled"));
    if (!Graph || !Hit || !HasAuthority || !IsPlayer)
    {
        return false;
    }
    int32 StartY = 0;
    for (UEdGraphNode* Node : Graph->Nodes)
    {
        if (auto* Bound = Cast<UK2Node_ActorBoundEvent>(Node))
        {
            if (Bound->EventOwner == Brush && Bound->DelegatePropertyName == Hit->GetFName())
            {
                return false;
            }
        }
        StartY = FMath::Max(StartY, Node->NodePosY + 420);
    }

    Blueprint->Modify();
    Graph->Modify();
    FCommunityMapGraph Door(*Graph);
    auto* Impact = Door.Add<UK2Node_ActorBoundEvent>(0, StartY, [Brush, Hit](auto& Node)
    {
        Node.InitializeActorBoundEventParams(Brush, Hit);
    });
    auto* AuthorityGate = Door.Add<UK2Node_IfThenElse>(440, StartY);
    auto* Authority = Door.Call(HasAuthority, 220, StartY + 140);
    bool bOK = Door.Connect(Impact->GetThenPin(), AuthorityGate->GetExecPin())
        && Door.Connect(Authority->GetReturnValuePin(), AuthorityGate->GetConditionPin());

    UEdGraphPin* Dispatch = AuthorityGate->GetThenPin();
    if (bPlayerOnly)
    {
        auto* Pawn = Door.Add<UK2Node_DynamicCast>(650, StartY, [](auto& Node)
        {
            Node.TargetType = APawn::StaticClass();
            Node.SetPurity(false);
        });
        auto* PlayerControlled = Door.Call(IsPlayer, 850, StartY + 150);
        auto* PlayerGate = Door.Add<UK2Node_IfThenElse>(1060, StartY);
        bOK = bOK && Door.Connect(Dispatch, Pawn->GetExecPin())
            && Door.Connect(Impact->FindPin(TEXT("OtherActor")), Pawn->GetCastSourcePin())
            && Door.Connect(Pawn->GetValidCastPin(), PlayerGate->GetExecPin())
            && Door.Connect(Pawn->GetCastResultPin(), PlayerControlled->FindPin(UEdGraphSchema_K2::PN_Self))
            && Door.Connect(PlayerControlled->GetReturnValuePin(), PlayerGate->GetConditionPin());
        Dispatch = PlayerGate->GetThenPin();
    }
    bOK = AddTimedSequenceStart(Door, Sequence, Dispatch, 1260, StartY) && bOK;
    if (bOK)
    {
        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
        FKismetEditorUtilities::CompileBlueprint(Blueprint);
        if (Blueprint->Status != BS_Error)
        {
            return true;
        }
    }
    Door.Remove();
    FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
    FKismetEditorUtilities::CompileBlueprint(Blueprint);
    return false;
}
