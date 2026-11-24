#include "CommunityMapMotionLibrary.h"
#include "CommunityMapGraph.h"

#include "EdGraphSchema_K2.h"
#include "Engine/Level.h"
#include "Engine/LevelScriptBlueprint.h"
#include "Engine/StaticMesh.h"
#include "GameFramework/Character.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "K2Node_ActorBoundEvent.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_Literal.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Kismet/BlueprintMapLibrary.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "PhysicsEngine/BodySetup.h"

bool UCommunityMapMotionLibrary::SetConvexVolumeMesh(UStaticMesh* Mesh, const TArray<FVector>& Vertices)
{
    if (!IsValid(Mesh) || Vertices.Num() < 4 || Vertices.Num() > 128
        || Vertices.ContainsByPredicate([](const FVector& Point) { return Point.ContainsNaN(); }))
    {
        return false;
    }
    Mesh->CreateBodySetup();
    UBodySetup* Body = Mesh->GetBodySetup();
    if (!Body)
    {
        return false;
    }
    Mesh->Modify();
    Body->Modify();
    Body->AggGeom.EmptyElements();
    FKConvexElem& Convex = Body->AggGeom.ConvexElems.AddDefaulted_GetRef();
    Convex.VertexData = Vertices;
    Convex.SetTransform(FTransform::Identity);
    Convex.UpdateElemBox();
    Body->CollisionTraceFlag = CTF_UseSimpleAsComplex;
    Body->InvalidatePhysicsData();
    Body->CreatePhysicsMeshes();
    Mesh->MarkPackageDirty();
    return true;
}

namespace
{
const FName VisitorName(TEXT("T3ElevatorVisitor"));
const FName SavedGravityName(TEXT("T3ElevatorPreviousGravity"));
const FName GravityName(GET_MEMBER_NAME_CHECKED(UCharacterMovementComponent, GravityScale));
const FName OtherActorPin(TEXT("OtherActor"));

template <typename Node>
Node* Variable(FCommunityMapGraph& Graph, FName Name, int32 X, int32 Y, UClass* Owner = nullptr)
{
    return Graph.Add<Node>(X, Y, [Name, Owner](auto& Entry)
    {
        if (Owner)
        {
            Entry.VariableReference.SetExternalMember(Name, Owner);
        }
        else
        {
            Entry.VariableReference.SetSelfMember(Name);
        }
    });
}

UK2Node_ActorBoundEvent* OverlapEvent(FCommunityMapGraph& Graph, AActor* Volume,
                                    const FMulticastDelegateProperty* Delegate, int32 Y)
{
    return Graph.Add<UK2Node_ActorBoundEvent>(0, Y, [Volume, Delegate](auto& Entry)
    {
        Entry.InitializeActorBoundEventParams(Volume, Delegate);
    });
}

struct FVisitorPins
{
    UK2Node_DynamicCast* Character;
    UEdGraphPin* Movement;
};

FVisitorPins Visitor(FCommunityMapGraph& Graph, int32 Y, bool& bOK)
{
    auto* Actor = Variable<UK2Node_VariableGet>(Graph, VisitorName, 350, Y + 135);
    auto* Character = Graph.Add<UK2Node_DynamicCast>(570, Y, [](auto& Entry)
    {
        Entry.TargetType = ACharacter::StaticClass();
        Entry.SetPurity(false);
    });
    auto* Movement = Variable<UK2Node_VariableGet>(Graph, TEXT("CharacterMovement"),
                                                   795, Y + 135, ACharacter::StaticClass());
    bOK = Graph.Connect(Actor->GetValuePin(), Character->GetCastSourcePin())
        && Graph.Connect(Character->GetCastResultPin(), Movement->FindPin(UEdGraphSchema_K2::PN_Self)) && bOK;
    return {Character, Movement->GetValuePin()};
}

UK2Node_CallFunction* MapCall(FCommunityMapGraph& Graph, UEdGraphPin* VisitorPin, FName Function,
                             int32 X, int32 Y, bool& bOK)
{
    auto* Map = Variable<UK2Node_VariableGet>(Graph, SavedGravityName, X - 260, Y + 105);
    auto* Call = Graph.Call(UBlueprintMapLibrary::StaticClass()->FindFunctionByName(Function), X, Y);
    bOK = Graph.Connect(Map->GetValuePin(), Call->FindPin(TEXT("TargetMap")))
        && Graph.Connect(VisitorPin, Call->FindPin(TEXT("Key"))) && bOK;
    return Call;
}

UK2Node_VariableSet* GravitySetter(FCommunityMapGraph& Graph, UEdGraphPin* Movement, int32 X, int32 Y, bool& bOK)
{
    auto* Set = Variable<UK2Node_VariableSet>(Graph, GravityName, X, Y, UCharacterMovementComponent::StaticClass());
    bOK = Graph.Connect(Movement, Set->FindPin(UEdGraphSchema_K2::PN_Self)) && bOK;
    return Set;
}
}

bool UCommunityMapMotionLibrary::BindZeroGravityVolumes(const TArray<AActor*>& Volumes)
{
    if (Volumes.IsEmpty() || !IsValid(Volumes[0]))
    {
        return false;
    }
    ULevel* Level = Volumes[0]->GetLevel();
    if (!Level || Volumes.ContainsByPredicate([Level](const AActor* Volume)
        { return !IsValid(Volume) || Volume->GetLevel() != Level; }))
    {
        return false;
    }
    auto* Blueprint = Level->GetLevelScriptBlueprint();
    UEdGraph* EventGraph = Blueprint ? FBlueprintEditorUtils::FindEventGraph(Blueprint) : nullptr;
    if (!EventGraph)
    {
        return false;
    }
    FEdGraphPinType VisitorType;
    VisitorType.PinCategory = UEdGraphSchema_K2::PC_Object;
    VisitorType.PinSubCategoryObject = AActor::StaticClass();
    FEdGraphPinType GravityMap = VisitorType;
    GravityMap.ContainerType = EPinContainerType::Map;
    GravityMap.PinValueType.TerminalCategory = UEdGraphSchema_K2::PC_Real;
    GravityMap.PinValueType.TerminalSubCategory = UEdGraphSchema_K2::PC_Float;
    if (!FBlueprintEditorUtils::AddMemberVariable(Blueprint, VisitorName, VisitorType))
    {
        return false;
    }
    if (!FBlueprintEditorUtils::AddMemberVariable(Blueprint, SavedGravityName, GravityMap))
    {
        FBlueprintEditorUtils::RemoveMemberVariable(Blueprint, VisitorName);
        return false;
    }

    int32 StartY = 0;
    for (const UEdGraphNode* Node : EventGraph->Nodes)
    {
        StartY = FMath::Max(StartY, Node->NodePosY + 400);
    }
    Blueprint->Modify();
    EventGraph->Modify();
    FCommunityMapGraph Graph(*EventGraph);
    bool bOK = true;
    int32 ExitY = StartY + 450;
    const auto Entry = Visitor(Graph, StartY, bOK);
    const auto Exit = Visitor(Graph, ExitY, bOK);

    auto* AlreadySaved = MapCall(Graph, Entry.Character->GetCastResultPin(), TEXT("Map_Contains"), 1180, StartY + 135, bOK);
    auto* FirstEntry = Graph.Add<UK2Node_IfThenElse>(1435, StartY);
    auto* Save = MapCall(Graph, Entry.Character->GetCastResultPin(), TEXT("Map_Add"), 1690, StartY, bOK);
    auto* OldGravity = Variable<UK2Node_VariableGet>(Graph, GravityName, 1430, StartY + 260,
                                                     UCharacterMovementComponent::StaticClass());
    auto* SetZero = GravitySetter(Graph, Entry.Movement, 1980, StartY, bOK);
    bOK = Graph.Connect(Entry.Character->GetValidCastPin(), FirstEntry->GetExecPin())
        && Graph.Connect(AlreadySaved->GetReturnValuePin(), FirstEntry->GetConditionPin())
        && Graph.Connect(FirstEntry->GetElsePin(), Save->GetExecPin())
        && Graph.Connect(Entry.Movement, OldGravity->FindPin(UEdGraphSchema_K2::PN_Self))
        && Graph.Connect(OldGravity->GetValuePin(), Save->FindPin(TEXT("Value")))
        && Graph.Connect(Save->GetThenPin(), SetZero->GetExecPin()) && bOK;
    if (UEdGraphPin* ZeroPin = SetZero->FindPin(GravityName))
    {
        EventGraph->GetSchema()->TrySetDefaultValue(*ZeroPin, TEXT("0.0"));
    }
    else
    {
        bOK = false;
    }

    auto* LastExit = Graph.Add<UK2Node_IfThenElse>(1450, ExitY);
    auto* Find = MapCall(Graph, Exit.Character->GetCastResultPin(), TEXT("Map_Find"), 1660, ExitY + 240, bOK);
    auto* WasSaved = Graph.Add<UK2Node_IfThenElse>(1900, ExitY);
    auto* Restore = GravitySetter(Graph, Exit.Movement, 2140, ExitY, bOK);
    auto* Clear = MapCall(Graph, Exit.Character->GetCastResultPin(), TEXT("Map_Remove"), 2420, ExitY, bOK);
    bOK = Graph.Connect(Exit.Character->GetValidCastPin(), LastExit->GetExecPin())
        && Graph.Connect(LastExit->GetElsePin(), WasSaved->GetExecPin())
        && Graph.Connect(Find->GetReturnValuePin(), WasSaved->GetConditionPin())
        && Graph.Connect(WasSaved->GetThenPin(), Restore->GetExecPin())
        && Graph.Connect(Find->FindPin(TEXT("Value")), Restore->FindPin(GravityName))
        && Graph.Connect(Restore->GetThenPin(), Clear->GetExecPin()) && bOK;

    const auto* Begin = FindFProperty<FMulticastDelegateProperty>(AActor::StaticClass(), GET_MEMBER_NAME_CHECKED(AActor, OnActorBeginOverlap));
    const auto* End = FindFProperty<FMulticastDelegateProperty>(AActor::StaticClass(), GET_MEMBER_NAME_CHECKED(AActor, OnActorEndOverlap));
    const auto* Overlapping = AActor::StaticClass()->FindFunctionByName(TEXT("IsOverlappingActor"));
    const auto* BooleanOr = UKismetMathLibrary::StaticClass()->FindFunctionByName(TEXT("BooleanOR"));
    UEdGraphPin* AnyOverlap = nullptr;
    for (AActor* Volume : Volumes)
    {
        ExitY += 520;
        auto* EnterEvent = OverlapEvent(Graph, Volume, Begin, ExitY);
        auto* ExitEvent = OverlapEvent(Graph, Volume, End, ExitY + 200);
        auto* EnterVisitor = Variable<UK2Node_VariableSet>(Graph, VisitorName, 245, ExitY);
        auto* ExitVisitor = Variable<UK2Node_VariableSet>(Graph, VisitorName, 245, ExitY + 200);
        auto* Literal = Graph.Add<UK2Node_Literal>(795, ExitY + 115, [Volume](auto& Node) { Node.SetObjectRef(Volume); });
        auto* Check = Graph.Call(Overlapping, 1035, ExitY + 115);
        bOK = Graph.Connect(EnterEvent->GetThenPin(), EnterVisitor->GetExecPin())
            && Graph.Connect(EnterEvent->FindPin(OtherActorPin), EnterVisitor->FindPin(VisitorName))
            && Graph.Connect(EnterVisitor->GetThenPin(), Entry.Character->GetExecPin())
            && Graph.Connect(ExitEvent->GetThenPin(), ExitVisitor->GetExecPin())
            && Graph.Connect(ExitEvent->FindPin(OtherActorPin), ExitVisitor->FindPin(VisitorName))
            && Graph.Connect(ExitVisitor->GetThenPin(), Exit.Character->GetExecPin())
            && Graph.Connect(Exit.Character->GetCastResultPin(), Check->FindPin(UEdGraphSchema_K2::PN_Self))
            && Graph.Connect(Literal->GetValuePin(), Check->FindPin(TEXT("Other"))) && bOK;
        if (AnyOverlap)
        {
            auto* Union = Graph.Call(BooleanOr, 1250, ExitY + 115);
            bOK = Graph.Connect(AnyOverlap, Union->FindPin(TEXT("A")))
                && Graph.Connect(Check->GetReturnValuePin(), Union->FindPin(TEXT("B"))) && bOK;
            AnyOverlap = Union->GetReturnValuePin();
        }
        else
        {
            AnyOverlap = Check->GetReturnValuePin();
        }
    }
    if (Graph.Connect(AnyOverlap, LastExit->GetConditionPin()) && bOK)
    {
        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
        FKismetEditorUtilities::CompileBlueprint(Blueprint);
        if (Blueprint->Status != BS_Error)
        {
            return true;
        }
    }
    Graph.Remove();
    FBlueprintEditorUtils::RemoveMemberVariable(Blueprint, SavedGravityName);
    FBlueprintEditorUtils::RemoveMemberVariable(Blueprint, VisitorName);
    FKismetEditorUtilities::CompileBlueprint(Blueprint);
    return false;
}
