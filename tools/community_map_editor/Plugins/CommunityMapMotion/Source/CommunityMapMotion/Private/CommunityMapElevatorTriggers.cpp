#include "CommunityMapMotionLibrary.h"
#include "CommunityMapGraph.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "Engine/Level.h"
#include "Engine/LevelScriptBlueprint.h"
#include "Engine/StaticMeshActor.h"
#include "GameFramework/Pawn.h"
#include "K2Node_ActorBoundEvent.h"
#include "K2Node_CustomEvent.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_FunctionEntry.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_Literal.h"
#include "K2Node_MacroInstance.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "LevelSequenceActor.h"
#include "Misc/PackageName.h"
#include "UObject/Package.h"

namespace
{
const FName StartsInactiveTag(TEXT("T3ElevatorStartsInactive"));
const FName Revision(TEXT("T3ElevatorRevision"));
const FName ApplyState(TEXT("ApplyElevatorState"));
const FName ToggleState(TEXT("ToggleElevatorState"));
const FName SignalTrigger(TEXT("SignalProximityTrigger"));
const FName SpendTrigger(TEXT("SpendProximityTrigger"));
const FName TriggerSpent(TEXT("T3TriggerSpent"));

FEdGraphPinType PinType(FName Category, FName SubCategory = NAME_None)
{
    FEdGraphPinType Type;
    Type.PinCategory = Category;
    Type.PinSubCategory = SubCategory;
    return Type;
}

template <typename Node>
Node* Variable(FCommunityMapGraph& Graph, FName Name, int32 X, int32 Y)
{
    return Graph.Add<Node>(X, Y, [Name](auto& Entry) { Entry.VariableReference.SetSelfMember(Name); });
}

UK2Node_Literal* Literal(FCommunityMapGraph& Graph, AActor* Actor, int32 X, int32 Y)
{
    return Graph.Add<UK2Node_Literal>(X, Y, [Actor](auto& Entry) { Entry.SetObjectRef(Actor); });
}

bool DefaultPin(UEdGraph* Graph, UEdGraphPin* Pin, const FString& Value)
{
    if (!Pin)
    {
        return false;
    }
    Graph->GetSchema()->TrySetDefaultValue(*Pin, Value);
    return true;
}

UK2Node_CallFunction* Math(FCommunityMapGraph& Graph, FName Name, int32 X, int32 Y)
{
    return Graph.Call(UKismetMathLibrary::StaticClass()->FindFunctionByName(Name), X, Y);
}

UEdGraphPin* FunctionEntry(UBlueprint* Blueprint, UEdGraph*& Function, FName Name)
{
    Function = FBlueprintEditorUtils::CreateNewGraph(Blueprint, Name, UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
    FBlueprintEditorUtils::AddFunctionGraph(Blueprint, Function, true, static_cast<UFunction*>(nullptr));
    TArray<UK2Node_FunctionEntry*> Entries;
    Function->GetNodesOfClass(Entries);
    return Entries.Num() == 1 ? Entries[0]->FindPin(UEdGraphSchema_K2::PN_Then) : nullptr;
}

bool BuildProximityReceiverGraph(UBlueprint* Blueprint, int32 Mode)
{
    if (!FBlueprintEditorUtils::AddMemberVariable(Blueprint, TriggerSpent, PinType(UEdGraphSchema_K2::PC_Boolean), TEXT("false")))
    {
        return false;
    }
    const UFunction* Collision = AActor::StaticClass()->FindFunctionByName(TEXT("SetActorEnableCollision"));
    UEdGraph* SignalFunction = nullptr;
    UEdGraphPin* Received = FunctionEntry(Blueprint, SignalFunction, SignalTrigger);
    FCommunityMapGraph Signal(*SignalFunction);
    auto* Spent = Variable<UK2Node_VariableGet>(Signal, TriggerSpent, 185, 175);
    auto* Gate = Signal.Add<UK2Node_IfThenElse>(410, 0);
    auto* Change = Signal.Call(Collision, 880, 0);
    UEdGraphPin* Enable = Change->FindPin(TEXT("bNewActorEnableCollision"));
    bool bOK = Signal.Connect(Received, Gate->GetExecPin())
        && Signal.Connect(Spent->GetValuePin(), Gate->GetConditionPin())
        && Signal.Connect(Gate->GetElsePin(), Change->GetExecPin());
    if (Mode == 3)
    {
        auto* Active = Signal.Call(AActor::StaticClass()->FindFunctionByName(TEXT("GetActorEnableCollision")), 220, 340);
        auto* Toggle = Math(Signal, TEXT("Not_PreBool"), 640, 250);
        bOK = Signal.Connect(Active->GetReturnValuePin(), Toggle->FindPin(TEXT("A")))
            && Signal.Connect(Toggle->GetReturnValuePin(), Enable) && bOK;
    }
    else
    {
        bOK = DefaultPin(SignalFunction, Enable, Mode == 1 ? TEXT("true") : TEXT("false")) && bOK;
    }

    UEdGraph* SpendFunction = nullptr;
    UEdGraphPin* Entry = FunctionEntry(Blueprint, SpendFunction, SpendTrigger);
    FCommunityMapGraph Spend(*SpendFunction);
    auto* Mark = Variable<UK2Node_VariableSet>(Spend, TriggerSpent, 220, 0);
    auto* Disable = Spend.Call(Collision, 470, 0);
    return Spend.Connect(Entry, Mark->GetExecPin())
        && DefaultPin(SpendFunction, Mark->FindPin(TriggerSpent), TEXT("true"))
        && Spend.Connect(Mark->GetThenPin(), Disable->GetExecPin())
        && DefaultPin(SpendFunction, Disable->FindPin(TEXT("bNewActorEnableCollision")), TEXT("false")) && bOK;
}

bool BuildStateGraph(UBlueprint* Blueprint)
{
    if (!FBlueprintEditorUtils::AddMemberVariable(Blueprint, Revision, PinType(UEdGraphSchema_K2::PC_Int), TEXT("0")))
    {
        return false;
    }
    FBPVariableDescription& RevisionProperty = Blueprint->NewVariables[FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, Revision)];
    RevisionProperty.PropertyFlags |= CPF_Net | CPF_RepNotify;
    FBlueprintEditorUtils::SetBlueprintVariableRepNotifyFunc(Blueprint, Revision, ApplyState);

    UEdGraph* ApplyFunction = nullptr;
    UEdGraphPin* Entry = FunctionEntry(Blueprint, ApplyFunction, ApplyState);
    FCommunityMapGraph Apply(*ApplyFunction);
    auto* StartsInactive = Apply.Call(AActor::StaticClass()->FindFunctionByName(TEXT("ActorHasTag")), 420, 190);
    auto* Counter = Variable<UK2Node_VariableGet>(Apply, Revision, 180, 335);
    auto* Parity = Math(Apply, TEXT("Percent_IntInt"), 405, 345);
    auto* Odd = Math(Apply, TEXT("NotEqual_IntInt"), 620, 345);
    auto* Active = Math(Apply, TEXT("EqualEqual_BoolBool"), 835, 190);
    auto* SetCollision = Apply.Call(AActor::StaticClass()->FindFunctionByName(TEXT("SetActorEnableCollision")), 1070, 0);
    const bool bApply = Apply.Connect(Entry, SetCollision->GetExecPin())
        && Apply.Connect(Counter->GetValuePin(), Parity->FindPin(TEXT("A")))
        && DefaultPin(ApplyFunction, Parity->FindPin(TEXT("B")), TEXT("2"))
        && Apply.Connect(Parity->GetReturnValuePin(), Odd->FindPin(TEXT("A")))
        && DefaultPin(ApplyFunction, Odd->FindPin(TEXT("B")), TEXT("0"))
        && DefaultPin(ApplyFunction, StartsInactive->FindPin(TEXT("Tag")), StartsInactiveTag.ToString())
        && Apply.Connect(StartsInactive->GetReturnValuePin(), Active->FindPin(TEXT("A")))
        && Apply.Connect(Odd->GetReturnValuePin(), Active->FindPin(TEXT("B")))
        && Apply.Connect(Active->GetReturnValuePin(), SetCollision->FindPin(TEXT("bNewActorEnableCollision")));

    UEdGraph* ToggleFunction = nullptr;
    Entry = FunctionEntry(Blueprint, ToggleFunction, ToggleState);
    FCommunityMapGraph Toggle(*ToggleFunction);
    auto* Previous = Variable<UK2Node_VariableGet>(Toggle, Revision, 180, 150);
    auto* Next = Math(Toggle, TEXT("Add_IntInt"), 395, 150);
    auto* SetRevision = Variable<UK2Node_VariableSet>(Toggle, Revision, 620, 0);
    return bApply && Toggle.Connect(Entry, SetRevision->GetExecPin())
        && Toggle.Connect(Previous->GetValuePin(), Next->FindPin(TEXT("A")))
        && DefaultPin(ToggleFunction, Next->FindPin(TEXT("B")), TEXT("1"))
        && Toggle.Connect(Next->GetReturnValuePin(), SetRevision->FindPin(Revision));
}

UEdGraphPin* PlayerEligibility(FCommunityMapGraph& Graph, UEdGraphPin* Pawn, int32 Activation, int32 Y, bool& bOK)
{
    auto* Human = Graph.Call(APawn::StaticClass()->FindFunctionByName(TEXT("IsPlayerControlled")), 645, Y + 130);
    bOK = Graph.Connect(Pawn, Human->FindPin(UEdGraphSchema_K2::PN_Self)) && bOK;
    if (Activation == 5)
    {
        return Human->GetReturnValuePin();
    }
    auto* State = Graph.Add<UK2Node_VariableGet>(645, Y + 310, [](auto& Entry)
    {
        Entry.VariableReference.SetExternalMember(TEXT("PlayerState"), APawn::StaticClass());
    });
    auto* HasState = Graph.Call(UKismetSystemLibrary::StaticClass()->FindFunctionByName(TEXT("IsValid")), 880, Y + 310);
    auto* Player = Math(Graph, TEXT("BooleanOR"), 1130, Y + 135);
    bOK = Graph.Connect(Pawn, State->FindPin(UEdGraphSchema_K2::PN_Self))
        && Graph.Connect(State->GetValuePin(), HasState->FindPin(TEXT("Object")))
        && Graph.Connect(HasState->GetReturnValuePin(), Player->FindPin(TEXT("B")))
        && Graph.Connect(Human->GetReturnValuePin(), Player->FindPin(TEXT("A"))) && bOK;
    return Player->GetReturnValuePin();
}

UEdGraphPin* Cooldown(FCommunityMapGraph& Graph, UEdGraph* EventGraph, UEdGraphPin* Exec, FName Last, double Delay,
                     int32& X, int32 Y, bool& bOK)
{
    auto* Time = Graph.Call(UGameplayStatics::StaticClass()->FindFunctionByName(TEXT("GetTimeSeconds")), X, Y + 110);
    auto* Previous = Variable<UK2Node_VariableGet>(Graph, Last, X, Y + 265);
    auto* Elapsed = Math(Graph, TEXT("Subtract_DoubleDouble"), X + 250, Y + 125);
    auto* Ready = Math(Graph, TEXT("GreaterEqual_DoubleDouble"), X + 480, Y + 125);
    auto* Allowed = Graph.Add<UK2Node_IfThenElse>(X + 725, Y);
    auto* Save = Variable<UK2Node_VariableSet>(Graph, Last, X + 950, Y);
    bOK = Graph.Connect(Exec, Allowed->GetExecPin())
        && Graph.Connect(Time->GetReturnValuePin(), Elapsed->FindPin(TEXT("A")))
        && Graph.Connect(Previous->GetValuePin(), Elapsed->FindPin(TEXT("B")))
        && Graph.Connect(Elapsed->GetReturnValuePin(), Ready->FindPin(TEXT("A")))
        && DefaultPin(EventGraph, Ready->FindPin(TEXT("B")), FString::SanitizeFloat(Delay))
        && Graph.Connect(Ready->GetReturnValuePin(), Allowed->GetConditionPin())
        && Graph.Connect(Allowed->GetThenPin(), Save->GetExecPin())
        && Graph.Connect(Time->GetReturnValuePin(), Save->FindPin(Last)) && bOK;
    X += 1190;
    return Save->GetThenPin();
}

UEdGraphPin* MacroPin(UK2Node_MacroInstance* Node, const TCHAR* Name)
{
    for (UEdGraphPin* Pin : Node->Pins)
    {
        if (Pin && Pin->PinName.ToString().Replace(TEXT(" "), TEXT("")) == Name)
        {
            return Pin;
        }
    }
    return nullptr;
}

bool BuildRepeatTimer(FCommunityMapGraph& Graph, UEdGraph* EventGraph, AActor* Trigger, UEdGraph* ForEach,
    FName Callback, FName Visitor, FName Contacts, FName HasOccupants,
    double Interval, UEdGraphPin* Dispatch, UEdGraphPin* Fired, int32 Y)
{
    auto* Timer = Graph.Add<UK2Node_CustomEvent>(0, Y, [Callback](auto& Event) { Event.CustomFunctionName = Callback; });
    auto* Reset = Variable<UK2Node_VariableSet>(Graph, HasOccupants, 225, Y);
    auto* Target = Literal(Graph, Trigger, 225, Y + 185);
    auto* Overlaps = Graph.Call(AActor::StaticClass()->FindFunctionByName(TEXT("GetOverlappingActors")), 465, Y + 160);
    auto* Snapshot = Variable<UK2Node_VariableSet>(Graph, Contacts, 730, Y);
    auto* ReadContacts = Variable<UK2Node_VariableGet>(Graph, Contacts, 730, Y + 175);
    auto* VisitEach = Graph.Add<UK2Node_MacroInstance>(990, Y, [ForEach](auto& Entry) { Entry.SetMacroGraph(ForEach); });
    auto* Actor = Variable<UK2Node_VariableSet>(Graph, Visitor, 1230, Y);
    auto* KeepTiming = Variable<UK2Node_VariableGet>(Graph, HasOccupants, 1230, Y + 300);
    auto* HasRelevantOccupant = Graph.Add<UK2Node_IfThenElse>(1475, Y + 175);
    auto* Schedule = Graph.Call(UKismetSystemLibrary::StaticClass()->FindFunctionByName(TEXT("K2_SetTimer")), 1720, Y + 175);
    UEdGraphPin* Filter = Overlaps->FindPin(TEXT("ClassFilter"));
    if (!Filter)
    {
        return false;
    }
    EventGraph->GetSchema()->TrySetDefaultObject(*Filter, APawn::StaticClass());
    // Snapshot the contacts for each scan. An eligible pawn keeps the timer running even while cooldown suppresses the event.
    return Graph.Connect(Timer->FindPin(UEdGraphSchema_K2::PN_Then), Reset->GetExecPin())
        && DefaultPin(EventGraph, Reset->FindPin(HasOccupants), TEXT("false"))
        && Graph.Connect(Reset->GetThenPin(), Snapshot->GetExecPin())
        && Graph.Connect(Target->GetValuePin(), Overlaps->FindPin(UEdGraphSchema_K2::PN_Self))
        && Graph.Connect(Overlaps->FindPin(TEXT("OverlappingActors")), Snapshot->FindPin(Contacts))
        && Graph.Connect(ReadContacts->GetValuePin(), MacroPin(VisitEach, TEXT("Array")))
        && Graph.Connect(Snapshot->GetThenPin(), MacroPin(VisitEach, TEXT("Exec")))
        && Graph.Connect(MacroPin(VisitEach, TEXT("LoopBody")), Actor->GetExecPin())
        && Graph.Connect(MacroPin(VisitEach, TEXT("ArrayElement")), Actor->FindPin(Visitor))
        && Graph.Connect(Actor->GetThenPin(), Dispatch)
        && Graph.Connect(MacroPin(VisitEach, TEXT("Completed")), HasRelevantOccupant->GetExecPin())
        && Graph.Connect(KeepTiming->GetValuePin(), HasRelevantOccupant->GetConditionPin())
        && Graph.Connect(HasRelevantOccupant->GetThenPin(), Schedule->GetExecPin())
        && Graph.Connect(Fired, Schedule->GetExecPin())
        && DefaultPin(EventGraph, Schedule->FindPin(TEXT("FunctionName")), Callback.ToString())
        && DefaultPin(EventGraph, Schedule->FindPin(TEXT("Time")), FString::SanitizeFloat(Interval))
        && DefaultPin(EventGraph, Schedule->FindPin(TEXT("bLooping")), TEXT("false"));
}
}

UClass* UCommunityMapMotionLibrary::BuildLinkedElevatorClass(const FString& PackageName)
{
    if (!FPackageName::IsValidLongPackageName(PackageName) || !PackageName.StartsWith(TEXT("/Game/Community/")))
    {
        return nullptr;
    }
    if (FPackageName::DoesPackageExist(PackageName))
    {
        UClass* Existing = LoadObject<UClass>(nullptr, *(PackageName + TEXT(".") + FPackageName::GetShortName(PackageName) + TEXT("_C")));
        return Existing && Existing->IsChildOf(AStaticMeshActor::StaticClass()) && Existing->FindFunctionByName(ToggleState)
            && Existing->FindFunctionByName(ApplyState) ? Existing : nullptr;
    }
    UPackage* Package = CreatePackage(*PackageName);
    UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(AStaticMeshActor::StaticClass(), Package,
        FName(FPackageName::GetShortName(PackageName)), BPTYPE_Normal, UBlueprint::StaticClass(), UBlueprintGeneratedClass::StaticClass());
    if (!Blueprint || !BuildStateGraph(Blueprint))
    {
        return nullptr;
    }
    FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
    FKismetEditorUtilities::CompileBlueprint(Blueprint);
    if (Blueprint->Status == BS_Error || !Blueprint->GeneratedClass)
    {
        return nullptr;
    }
    AActor* Defaults = Blueprint->GeneratedClass->GetDefaultObject<AActor>();
    FBoolProperty* Replicates = FindFProperty<FBoolProperty>(AActor::StaticClass(), TEXT("bReplicates"));
    if (!Replicates)
    {
        return nullptr;
    }
    Replicates->SetPropertyValue_InContainer(Defaults, true);
    Defaults->bAlwaysRelevant = true;
    FAssetRegistryModule::AssetCreated(Blueprint);
    Package->MarkPackageDirty();
    return Blueprint->GeneratedClass;
}

UClass* UCommunityMapMotionLibrary::BuildProximityReceiverClass(const FString& PackageName, int32 Mode)
{
    if (!FPackageName::IsValidLongPackageName(PackageName) || !PackageName.StartsWith(TEXT("/Game/Community/"))
        || Mode < 1 || Mode > 3)
    {
        return nullptr;
    }
    const FString Name = FPackageName::GetShortName(PackageName);
    if (FPackageName::DoesPackageExist(PackageName))
    {
        UClass* Existing = LoadObject<UClass>(nullptr, *(PackageName + TEXT(".") + Name + TEXT("_C")));
        return Existing && Existing->IsChildOf(AStaticMeshActor::StaticClass()) && Existing->FindFunctionByName(SignalTrigger)
            && Existing->FindFunctionByName(SpendTrigger) ? Existing : nullptr;
    }
    UPackage* Package = CreatePackage(*PackageName);
    UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(AStaticMeshActor::StaticClass(), Package,
        FName(*Name), BPTYPE_Normal, UBlueprint::StaticClass(), UBlueprintGeneratedClass::StaticClass());
    if (!Blueprint || !BuildProximityReceiverGraph(Blueprint, Mode))
    {
        return nullptr;
    }
    FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
    FKismetEditorUtilities::CompileBlueprint(Blueprint);
    if (Blueprint->Status == BS_Error || !Blueprint->GeneratedClass)
    {
        return nullptr;
    }
    FAssetRegistryModule::AssetCreated(Blueprint);
    Package->MarkPackageDirty();
    return Blueprint->GeneratedClass;
}

bool UCommunityMapMotionLibrary::ConfigureLinkedElevator(AActor* Field, bool bStartsActive)
{
    AStaticMeshActor* MeshActor = Cast<AStaticMeshActor>(Field);
    if (!IsValid(MeshActor) || !Field->FindFunction(ToggleState))
    {
        return false;
    }
    FBoolProperty* Replicates = FindFProperty<FBoolProperty>(AActor::StaticClass(), TEXT("bReplicates"));
    if (!Replicates)
    {
        return false;
    }
    Field->Modify();
    Replicates->SetPropertyValue_InContainer(Field, true);
    // A placed static mesh registers with the net driver in BeginPlay. Its transform stays fixed.
    MeshActor->bStaticMeshReplicateMovement = true;
    if (bStartsActive)
    {
        Field->Tags.Remove(StartsInactiveTag);
    }
    else
    {
        Field->Tags.AddUnique(StartsInactiveTag);
    }
    Field->SetActorEnableCollision(bStartsActive);
    return true;
}

namespace
{
bool BindProximity(AActor* Trigger, const TArray<AActor*>& Receivers, int32 Activation, bool bOnce, double Delay,
    double Interval, bool bAllowBrushReceivers = false)
{
    if (!IsValid(Trigger) || Receivers.IsEmpty() || !FMath::IsFinite(Delay) || Delay < 0.0
        || !FMath::IsFinite(Interval) || Interval < 0.0 || Interval > 3600.0
        || (Activation != 0 && Activation != 1 && Activation != 5))
    {
        return false;
    }
    ULevel* Level = Trigger->GetLevel();
    if (!Level || Receivers.ContainsByPredicate([Level, bAllowBrushReceivers](AActor* Receiver)
        { return !IsValid(Receiver) || Receiver->GetLevel() != Level
                 || (!Receiver->FindFunction(ToggleState) && !(bAllowBrushReceivers
                     && (Receiver->IsA<ALevelSequenceActor>() || Receiver->FindFunction(SignalTrigger)))); }))
    {
        return false;
    }
    ULevelScriptBlueprint* Blueprint = Level->GetLevelScriptBlueprint();
    UEdGraph* EventGraph = Blueprint ? FBlueprintEditorUtils::FindEventGraph(Blueprint) : nullptr;
    if (!EventGraph)
    {
        return false;
    }
    TArray<FName> Variables;
    auto AddVariable = [&](FName Name, const FEdGraphPinType& Type, const FString& Initial = FString())
    {
        if (!FBlueprintEditorUtils::AddMemberVariable(Blueprint, Name, Type, Initial))
        {
            return false;
        }
        Variables.Add(Name);
        return true;
    };
    auto RemoveVariables = [&]()
    {
        for (FName Name : Variables)
        {
            FBlueprintEditorUtils::RemoveMemberVariable(Blueprint, Name);
        }
    };
    FName Last = NAME_None, Visitor = NAME_None, Contacts = NAME_None, HasOccupants = NAME_None, Callback = NAME_None;
    UEdGraph* ForEach = nullptr;
    if (Interval > 0.0)
    {
        UBlueprint* Macros = LoadObject<UBlueprint>(nullptr, TEXT("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros"));
        if (Macros)
        {
            for (UEdGraph* Macro : Macros->MacroGraphs)
            {
                if (Macro && Macro->GetFName() == TEXT("ForEachLoop"))
                {
                    ForEach = Macro;
                    break;
                }
            }
        }
        Visitor = FName(*FString::Printf(TEXT("T3ElevatorVisitor_%s"), *Trigger->GetName()));
        Contacts = FName(*FString::Printf(TEXT("T3ElevatorContacts_%s"), *Trigger->GetName()));
        HasOccupants = FName(*FString::Printf(TEXT("T3ElevatorOccupied_%s"), *Trigger->GetName()));
        Callback = FName(*FString::Printf(TEXT("T3ElevatorRepeat_%s"), *Trigger->GetName()));
        FEdGraphPinType ActorType = PinType(UEdGraphSchema_K2::PC_Object);
        ActorType.PinSubCategoryObject = AActor::StaticClass();
        FEdGraphPinType ContactsType = ActorType;
        ContactsType.ContainerType = EPinContainerType::Array;
        if (!ForEach || !AddVariable(Visitor, ActorType) || !AddVariable(Contacts, ContactsType)
            || !AddVariable(HasOccupants, PinType(UEdGraphSchema_K2::PC_Boolean)))
        {
            RemoveVariables();
            return false;
        }
    }
    if (Delay > 0.0)
    {
        Last = FName(*FString::Printf(TEXT("T3ElevatorLast_%s"), *Trigger->GetName()));
        if (!AddVariable(Last, PinType(UEdGraphSchema_K2::PC_Real, UEdGraphSchema_K2::PC_Double), TEXT("0.0")))
        {
            RemoveVariables();
            return false;
        }
    }
    int32 Y = 0;
    for (const UEdGraphNode* Node : EventGraph->Nodes)
    {
        Y = FMath::Max(Y, Node->NodePosY + 400);
    }
    Blueprint->Modify();
    EventGraph->Modify();
    FCommunityMapGraph Graph(*EventGraph);
    const auto* Overlap = FindFProperty<FMulticastDelegateProperty>(AActor::StaticClass(), GET_MEMBER_NAME_CHECKED(AActor, OnActorBeginOverlap));
    auto* Enter = Graph.Add<UK2Node_ActorBoundEvent>(0, Y, [Trigger, Overlap](auto& Node)
    {
        Node.InitializeActorBoundEventParams(Trigger, Overlap);
    });
    auto* Server = Graph.Call(UKismetSystemLibrary::StaticClass()->FindFunctionByName(TEXT("IsServer")), 220, Y + 155);
    auto* Authority = Graph.Add<UK2Node_IfThenElse>(235, Y);
    auto* Pawn = Graph.Add<UK2Node_DynamicCast>(460, Y, [](auto& Node)
    {
        Node.TargetType = APawn::StaticClass();
        Node.SetPurity(false);
    });
    bool bOK = Graph.Connect(Enter->GetThenPin(), Authority->GetExecPin())
        && Graph.Connect(Server->GetReturnValuePin(), Authority->GetConditionPin());
    UEdGraphPin* Candidate = Enter->FindPin(TEXT("OtherActor"));
    UEdGraphPin* Accepted = Authority->GetThenPin();
    if (ForEach)
    {
        auto* Set = Variable<UK2Node_VariableSet>(Graph, Visitor, 440, Y);
        auto* Get = Variable<UK2Node_VariableGet>(Graph, Visitor, 440, Y + 175);
        Pawn->NodePosX += 240;
        bOK = Graph.Connect(Accepted, Set->GetExecPin()) && Graph.Connect(Candidate, Set->FindPin(Visitor)) && bOK;
        Accepted = Set->GetThenPin();
        Candidate = Get->GetValuePin();
    }
    bOK = Graph.Connect(Accepted, Pawn->GetExecPin()) && Graph.Connect(Candidate, Pawn->GetCastSourcePin()) && bOK;
    UEdGraphPin* Exec = Pawn->GetValidCastPin();
    int32 X = 680;
    if (Activation != 1)
    {
        auto* Eligible = Graph.Add<UK2Node_IfThenElse>(1400, Y);
        UEdGraphPin* CanTrigger = PlayerEligibility(Graph, Pawn->GetCastResultPin(), Activation, Y, bOK);
        bOK = Graph.Connect(CanTrigger, Eligible->GetConditionPin()) && Graph.Connect(Exec, Eligible->GetExecPin()) && bOK;
        Exec = Eligible->GetThenPin();
        X = 1650;
    }
    if (ForEach)
    {
        auto* Occupied = Variable<UK2Node_VariableSet>(Graph, HasOccupants, FMath::Max(940, X), Y);
        bOK = Graph.Connect(Exec, Occupied->GetExecPin())
            && DefaultPin(EventGraph, Occupied->FindPin(HasOccupants), TEXT("true")) && bOK;
        Exec = Occupied->GetThenPin();
        X = FMath::Max(1190, X + 250);
    }
    if (!Last.IsNone())
    {
        Exec = Cooldown(Graph, EventGraph, Exec, Last, Delay, X, Y, bOK);
    }
    if (bOnce)
    {
        const UFunction* Spend = Trigger->FindFunction(SpendTrigger);
        auto* Once = Graph.Call(Spend ? Spend : AActor::StaticClass()->FindFunctionByName(TEXT("SetActorEnableCollision")), X, Y);
        auto* Target = Literal(Graph, Trigger, X - 125, Y + 140);
        bOK = Graph.Connect(Exec, Once->GetExecPin())
            && Graph.Connect(Target->GetValuePin(), Once->FindPin(UEdGraphSchema_K2::PN_Self))
            && (Spend || DefaultPin(EventGraph, Once->FindPin(TEXT("bNewActorEnableCollision")), TEXT("false"))) && bOK;
        Exec = Once->GetThenPin();
        X += 250;
    }
    // Each door has its own busy check. A busy door cannot prevent other receivers or the repeat timer from running.
    UK2Node_ExecutionSequence* FanOut = nullptr;
    if (Receivers.ContainsByPredicate([](AActor* Receiver) { return Receiver->IsA<ALevelSequenceActor>(); }))
    {
        FanOut = Graph.Add<UK2Node_ExecutionSequence>(X, Y);
        // The node starts with two outputs. The final pin continues the repeat timer path.
        for (int32 Number = 2; Number <= Receivers.Num(); ++Number)
        {
            FanOut->AddInputPin();
        }
        bOK = Graph.Connect(Exec, FanOut->FindPin(UEdGraphSchema_K2::PN_Execute)) && bOK;
        Exec = FanOut->GetThenPinGivenIndex(Receivers.Num());
        X += 260;
    }
    for (int32 Index = 0; Index < Receivers.Num(); ++Index)
    {
        AActor* Receiver = Receivers[Index];
        UEdGraphPin* Start = FanOut ? FanOut->GetThenPinGivenIndex(Index) : Exec;
        if (auto* Sequence = Cast<ALevelSequenceActor>(Receiver))
        {
            bOK = AddTimedSequenceStart(Graph, Sequence, Start, X, Y) && bOK;
            X += 1430;
        }
        else
        {
            auto* Target = Literal(Graph, Receiver, X - 135, Y + 170);
            const UFunction* Signal = Receiver->FindFunction(SignalTrigger);
            auto* Toggle = Graph.Call(Signal ? Signal : Receiver->FindFunction(ToggleState), X, Y);
            bOK = Graph.Connect(Start, Toggle->GetExecPin())
                && Graph.Connect(Target->GetValuePin(), Toggle->FindPin(UEdGraphSchema_K2::PN_Self)) && bOK;
            if (!FanOut)
            {
                Exec = Toggle->GetThenPin();
            }
            X += 260;
        }
    }
    if (ForEach)
    {
        bOK = BuildRepeatTimer(Graph, EventGraph, Trigger, ForEach, Callback, Visitor, Contacts, HasOccupants,
                              Interval, Pawn->GetExecPin(), Exec, Y + 690) && bOK;
    }
    if (bOK)
    {
        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
        FKismetEditorUtilities::CompileBlueprint(Blueprint);
        if (Blueprint->Status != BS_Error)
        {
            return true;
        }
    }
    Graph.Remove();
    RemoveVariables();
    FKismetEditorUtilities::CompileBlueprint(Blueprint);
    return false;
}
}

bool UCommunityMapMotionLibrary::BindElevatorTrigger(AActor* Trigger, const TArray<AActor*>& Fields,
    int32 Activation, bool bOnce, double Delay)
{
    return BindProximity(Trigger, Fields, Activation, bOnce, Delay, 0.0);
}

bool UCommunityMapMotionLibrary::BindRepeatingElevatorTrigger(AActor* Trigger, const TArray<AActor*>& Fields,
    int32 Activation, double Delay, double Repeat)
{
    return Repeat > 0.0 && BindProximity(Trigger, Fields, Activation, false, Delay, Repeat);
}

bool UCommunityMapMotionLibrary::BindBrushTrigger(AActor* Trigger, const TArray<AActor*>& Receivers,
    int32 Activation, bool bOnce, double Delay, double Interval)
{
    return !(bOnce && Interval > 0.0) && BindProximity(Trigger, Receivers, Activation, bOnce, Delay, Interval, true);
}
