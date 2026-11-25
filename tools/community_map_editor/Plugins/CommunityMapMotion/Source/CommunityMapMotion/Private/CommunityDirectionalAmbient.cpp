#include "CommunityMapMotionLibrary.h"
#include "CommunityMapGraph.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Camera/PlayerCameraManager.h"
#include "Components/AudioComponent.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "Engine/SCS_Node.h"
#include "Engine/SimpleConstructionScript.h"
#include "K2Node_Event.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/PackageName.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"

namespace
{
const FName InnerAngleName(TEXT("ConeInnerHalfAngle"));
const FName OuterAngleName(TEXT("ConeOuterHalfAngle"));
const FName InnerGainName(TEXT("ConeInnerGain"));
const FName OuterGainName(TEXT("ConeOuterGain"));
const FName StartedName(TEXT("MissionAudioStarted"));
const FName TickName(TEXT("ReceiveTick"));
const FName ComponentName(TEXT("MissionAudioComponent"));

UEdGraphPin* ReadVariable(FCommunityMapGraph& Graph, FName Name, int32 X, int32 Y)
{
    auto* Variable = Graph.Add<UK2Node_VariableGet>(X, Y, [Name](auto& Node)
    {
        Node.VariableReference.SetSelfMember(Name);
    });
    return Variable->GetValuePin();
}

bool BuildGainGraph(UBlueprint& Blueprint)
{
    UEdGraph* EventGraph = FBlueprintEditorUtils::FindEventGraph(&Blueprint);
    if (!EventGraph)
    {
        return false;
    }
    FEdGraphPinType Real;
    Real.PinCategory = UEdGraphSchema_K2::PC_Real;
    Real.PinSubCategory = UEdGraphSchema_K2::PC_Double;
    FEdGraphPinType Boolean;
    Boolean.PinCategory = UEdGraphSchema_K2::PC_Boolean;
    if (!FBlueprintEditorUtils::AddMemberVariable(&Blueprint, InnerAngleName, Real, TEXT("30"))
        || !FBlueprintEditorUtils::AddMemberVariable(&Blueprint, OuterAngleName, Real, TEXT("60"))
        || !FBlueprintEditorUtils::AddMemberVariable(&Blueprint, InnerGainName, Real, TEXT("1"))
        || !FBlueprintEditorUtils::AddMemberVariable(&Blueprint, OuterGainName, Real, TEXT("0"))
        || !FBlueprintEditorUtils::AddMemberVariable(&Blueprint, StartedName, Boolean, TEXT("false")))
    {
        return false;
    }
    // Unreal restores class defaults during construction for Blueprint
    // variables that cannot be edited per instance. Preserve each cone.
    for (const FName Name : {InnerAngleName, OuterAngleName, InnerGainName, OuterGainName})
    {
        FBlueprintEditorUtils::SetBlueprintOnlyEditableFlag(&Blueprint, Name, false);
    }

    FCommunityMapGraph Graph(*EventGraph);
    UK2Node_Event* Tick = FBlueprintEditorUtils::FindOverrideForFunction(&Blueprint, AActor::StaticClass(), TickName);
    if (!Tick)
    {
        Tick = Graph.Add<UK2Node_Event>(0, 0, [](auto& Node)
        {
            Node.EventReference.SetExternalMember(TickName, AActor::StaticClass());
            Node.bOverrideFunction = true;
        });
    }
    Tick->SetEnabledState(ENodeEnabledState::Enabled);

    auto* Camera = Graph.Call(UGameplayStatics::StaticClass()->FindFunctionByName(TEXT("GetPlayerCameraManager")), 0, 180);
    auto* CameraValid = Graph.Call(UKismetSystemLibrary::StaticClass()->FindFunctionByName(TEXT("IsValid")), 270, 180);
    auto* HasCamera = Graph.Add<UK2Node_IfThenElse>(510, 0);
    auto* Listener = Graph.Call(APlayerCameraManager::StaticClass()->FindFunctionByName(TEXT("GetCameraLocation")), 270, 330);
    auto* Origin = Graph.Call(AActor::StaticClass()->FindFunctionByName(TEXT("K2_GetActorLocation")), 270, 450);
    auto* Forward = Graph.Call(AActor::StaticClass()->FindFunctionByName(TEXT("GetActorForwardVector")), 540, 540);
    auto* Direction = Graph.Call(UKismetMathLibrary::StaticClass()->FindFunctionByName(TEXT("GetDirectionUnitVector")), 540, 330);
    auto* Dot = Graph.Call(UKismetMathLibrary::StaticClass()->FindFunctionByName(TEXT("Dot_VectorVector")), 830, 330);
    auto* Angle = Graph.Call(UKismetMathLibrary::StaticClass()->FindFunctionByName(TEXT("DegAcos")), 1060, 330);
    auto* Inside = Graph.Call(UKismetMathLibrary::StaticClass()->FindFunctionByName(TEXT("LessEqual_DoubleDouble")), 1300, 220);
    auto* Falloff = Graph.Call(UKismetMathLibrary::StaticClass()->FindFunctionByName(TEXT("MapRangeClamped")), 1300, 420);
    auto* Volume = Graph.Call(UKismetMathLibrary::StaticClass()->FindFunctionByName(TEXT("SelectFloat")), 1600, 350);
    auto* Apply = Graph.Call(UAudioComponent::StaticClass()->FindFunctionByName(TEXT("SetVolumeMultiplier")), 1870, 0);
    auto* InitialPlay = Graph.Add<UK2Node_IfThenElse>(2140, 0);
    auto* SetStarted = Graph.Add<UK2Node_VariableSet>(2410, 0, [](auto& Node)
    {
        Node.VariableReference.SetSelfMember(StartedName);
    });
    auto* Play = Graph.Call(UAudioComponent::StaticClass()->FindFunctionByName(TEXT("Play")), 2680, 0);
    if (!SetStarted->FindPin(StartedName))
    {
        return false;
    }
    EventGraph->GetSchema()->TrySetDefaultValue(*SetStarted->FindPin(StartedName), TEXT("true"));
    UEdGraphPin* CameraObject = Camera->GetReturnValuePin();
    UEdGraphPin* ConeAngle = Angle->GetReturnValuePin();
    UEdGraphPin* InnerAngle = ReadVariable(Graph, InnerAngleName, 1060, 475);
    UEdGraphPin* OuterAngle = ReadVariable(Graph, OuterAngleName, 1060, 570);
    UEdGraphPin* InnerGain = ReadVariable(Graph, InnerGainName, 1060, 670);
    UEdGraphPin* OuterGain = ReadVariable(Graph, OuterGainName, 1060, 755);
    UEdGraphPin* Component = ReadVariable(Graph, ComponentName, 1590, 190);
    UEdGraphPin* Started = ReadVariable(Graph, StartedName, 1910, 190);

    // Use the local camera to control one sound source. Keeping the angle
    // separate preserves the sphere's distance falloff and the gain floor.
    return Graph.Connect(Tick->FindPin(UEdGraphSchema_K2::PN_Then), HasCamera->GetExecPin())
        && Graph.Connect(CameraObject, CameraValid->FindPin(TEXT("Object")))
        && Graph.Connect(CameraValid->GetReturnValuePin(), HasCamera->GetConditionPin())
        && Graph.Connect(CameraObject, Listener->FindPin(UEdGraphSchema_K2::PN_Self))
        && Graph.Connect(Origin->GetReturnValuePin(), Direction->FindPin(TEXT("From")))
        && Graph.Connect(Listener->GetReturnValuePin(), Direction->FindPin(TEXT("To")))
        && Graph.Connect(Direction->GetReturnValuePin(), Dot->FindPin(TEXT("A")))
        && Graph.Connect(Forward->GetReturnValuePin(), Dot->FindPin(TEXT("B")))
        && Graph.Connect(Dot->GetReturnValuePin(), Angle->FindPin(TEXT("A")))
        && Graph.Connect(ConeAngle, Inside->FindPin(TEXT("A")))
        && Graph.Connect(InnerAngle, Inside->FindPin(TEXT("B")))
        && Graph.Connect(ConeAngle, Falloff->FindPin(TEXT("Value")))
        && Graph.Connect(InnerAngle, Falloff->FindPin(TEXT("InRangeA")))
        && Graph.Connect(OuterAngle, Falloff->FindPin(TEXT("InRangeB")))
        && Graph.Connect(InnerGain, Falloff->FindPin(TEXT("OutRangeA")))
        && Graph.Connect(OuterGain, Falloff->FindPin(TEXT("OutRangeB")))
        && Graph.Connect(Inside->GetReturnValuePin(), Volume->FindPin(TEXT("bPickA")))
        && Graph.Connect(InnerGain, Volume->FindPin(TEXT("A")))
        && Graph.Connect(Falloff->GetReturnValuePin(), Volume->FindPin(TEXT("B")))
        && Graph.Connect(HasCamera->GetThenPin(), Apply->GetExecPin())
        && Graph.Connect(Component, Apply->FindPin(UEdGraphSchema_K2::PN_Self))
        && Graph.Connect(Volume->GetReturnValuePin(), Apply->FindPin(TEXT("NewVolumeMultiplier")))
        && Graph.Connect(Apply->GetThenPin(), InitialPlay->GetExecPin())
        && Graph.Connect(Started, InitialPlay->GetConditionPin())
        && Graph.Connect(InitialPlay->GetElsePin(), SetStarted->GetExecPin())
        && Graph.Connect(SetStarted->GetThenPin(), Play->GetExecPin())
        && Graph.Connect(Component, Play->FindPin(UEdGraphSchema_K2::PN_Self));
}
}

UClass* UCommunityMapMotionLibrary::BuildDirectionalAmbientClass(const FString& PackageName)
{
    if (!FPackageName::IsValidLongPackageName(PackageName) || !PackageName.StartsWith(TEXT("/Game/Community/")))
    {
        return nullptr;
    }
    const FString Name = FPackageName::GetShortName(PackageName);
    if (FPackageName::DoesPackageExist(PackageName))
    {
        UClass* Existing = LoadObject<UClass>(nullptr, *(PackageName + TEXT(".") + Name + TEXT("_C")));
        return Existing && Existing->IsChildOf(AActor::StaticClass())
            && FindFProperty<FDoubleProperty>(Existing, OuterGainName) ? Existing : nullptr;
    }
    UPackage* Package = CreatePackage(*PackageName);
    UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(AActor::StaticClass(), Package,
        FName(*Name), BPTYPE_Normal, UBlueprint::StaticClass(), UBlueprintGeneratedClass::StaticClass());
    if (!Blueprint || !Blueprint->SimpleConstructionScript)
    {
        return nullptr;
    }
    // The clip belongs to each placed emitter. A stock component allows the
    // Blueprint template to compile before those instances have sounds.
    USimpleConstructionScript* Construction = Blueprint->SimpleConstructionScript;
    USCS_Node* AudioNode = Construction->CreateNode(UAudioComponent::StaticClass(), ComponentName);
    if (!AudioNode)
    {
        return nullptr;
    }
    auto* Audio = Cast<UAudioComponent>(AudioNode->ComponentTemplate);
    if (!Audio)
    {
        return nullptr;
    }
    Audio->SetAutoActivate(false);
    Construction->AddNode(AudioNode);
    FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
    if (!BuildGainGraph(*Blueprint))
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
    Defaults->PrimaryActorTick.bCanEverTick = true;
    Defaults->PrimaryActorTick.bStartWithTickEnabled = true;
    Defaults->PrimaryActorTick.bAllowTickOnDedicatedServer = false;
    Defaults->PrimaryActorTick.TickInterval = 1.0f / 30.0f;
    FAssetRegistryModule::AssetCreated(Blueprint);
    Package->MarkPackageDirty();
    return Blueprint->GeneratedClass;
}

bool UCommunityMapMotionLibrary::ConfigureDirectionalAmbient(AActor* Emitter, double InnerAngle, double OuterAngle,
    double InnerGain, double OuterGain)
{
    if (!IsValid(Emitter) || !Emitter->FindComponentByClass<UAudioComponent>()
        || !FMath::IsFinite(InnerAngle) || !FMath::IsFinite(OuterAngle)
        || !FMath::IsFinite(InnerGain) || !FMath::IsFinite(OuterGain)
        || InnerAngle < 0 || OuterAngle < InnerAngle || OuterAngle > 180
        || OuterGain < 0 || InnerGain < OuterGain || InnerGain > 1)
    {
        return false;
    }
    const FName Variables[] = {InnerAngleName, OuterAngleName, InnerGainName, OuterGainName};
    const double Values[] = {InnerAngle, OuterAngle, InnerGain, OuterGain};
    FDoubleProperty* Properties[UE_ARRAY_COUNT(Variables)];
    for (int32 Index = 0; Index < UE_ARRAY_COUNT(Variables); ++Index)
    {
        Properties[Index] = FindFProperty<FDoubleProperty>(Emitter->GetClass(), Variables[Index]);
        if (!Properties[Index])
        {
            return false;
        }
    }
    Emitter->Modify();
    for (int32 Index = 0; Index < UE_ARRAY_COUNT(Values); ++Index)
    {
        Properties[Index]->SetPropertyValue_InContainer(Emitter, Values[Index]);
    }
    Emitter->MarkPackageDirty();
    return true;
}
