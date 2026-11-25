#include "CommunityMapMotionLibrary.h"

#include "Interfaces/MetasoundFrontendSourceInterface.h"
#include "MetasoundEditorSubsystem.h"
#include "MetasoundFrontendController.h"
#include "MetasoundFrontendRegistries.h"
#include "MetasoundSource.h"
#include "Sound/SoundWave.h"

namespace
{
    using namespace Metasound::Frontend;

    constexpr const TCHAR* OnPlay = TEXT("UE.Source.OnPlay");
    constexpr const TCHAR* OnFinished = TEXT("UE.Source.OneShot.OnFinished");

    FNodeHandle AddAmbientNode(const FGraphHandle& Graph, const TCHAR* Namespace, const TCHAR* Name,
        const TCHAR* Variant = TEXT(""), int32 MinorVersion = 0)
    {
        const FMetasoundFrontendClassName ClassName(Namespace, Name, Variant);
        return Graph->AddNode(NodeRegistryKey::CreateKey(EMetasoundFrontendClassType::External,
            ClassName.GetFullName().ToString(), 1, MinorVersion));
    }

    bool ConnectAmbientPins(const FNodeHandle& From, const TCHAR* Out, const FNodeHandle& To, const TCHAR* In)
    {
        const FInputHandle Input = To->GetInputWithVertexName(In);
        const FOutputHandle Output = From->GetOutputWithVertexName(Out);
        return Input->IsValid() && Output->IsValid() && Input->Connect(*Output);
    }

    template <class T>
    bool SetAmbientInput(const FNodeHandle& Node, const TCHAR* Name, T Value)
    {
        const FInputHandle Input = Node->GetInputWithVertexName(Name);
        if (!Input->IsValid())
        {
            return false;
        }
        FMetasoundFrontendLiteral Literal;
        Literal.Set(Value);
        Input->SetLiteral(Literal);
        return true;
    }

    bool ConnectAmbientChannel(const FGraphHandle& Graph, const FNodeHandle& Player,
        const TCHAR* PlayerPin, const TCHAR* SourcePin)
    {
        return ConnectAmbientPins(Player, PlayerPin, Graph->GetOutputNodeWithName(SourcePin), SourcePin);
    }

    bool ConnectFiniteRepeat(const FGraphHandle& Graph, const FNodeHandle& Player,
        const FNodeHandle& Repeat, int32 RepeatCount)
    {
        const FNodeHandle Counter = AddAmbientNode(Graph, TEXT("UE"), TEXT("Trigger Counter"));
        const FNodeHandle Compare = AddAmbientNode(Graph, TEXT("TriggerCompare"), TEXT("Clamp"), TEXT("Int32"));
        return SetAmbientInput(Compare, TEXT("B"), RepeatCount + 1)
            && ConnectAmbientPins(Player, TEXT("On Finished"), Counter, TEXT("In"))
            && ConnectAmbientPins(Counter, TEXT("On Trigger"), Compare, TEXT("Compare"))
            && ConnectAmbientPins(Counter, TEXT("Count"), Compare, TEXT("A"))
            && ConnectAmbientPins(Compare, TEXT("False"), Repeat, TEXT("Value"))
            && ConnectAmbientPins(Compare, TEXT("True"), Graph->GetOutputNodeWithName(OnFinished), OnFinished);
    }
}

bool UCommunityMapMotionLibrary::ConfigureAmbientMetaSound(UMetaSoundSource* Source, USoundWave* Wave,
    int32 RepeatCount, float MinGapSeconds, float MaxGapSeconds)
{
    if (!IsValid(Source) || Source->bIsPreset || !IsValid(Wave) || Wave->IsLooping()
        || Wave->NumChannels < 1 || Wave->NumChannels > 2
        || RepeatCount < -1 || RepeatCount == 0 || RepeatCount > 10000
        || !FMath::IsFinite(MinGapSeconds) || !FMath::IsFinite(MaxGapSeconds)
        || MinGapSeconds < 0 || MaxGapSeconds < MinGapSeconds || MaxGapSeconds > 600)
    {
        return false;
    }

    const bool bStereo = Wave->NumChannels == 2;
    const FDocumentHandle Document = Source->GetDocumentHandle();
    const FGraphHandle Graph = Document->GetRootGraph();
    if (!Graph->IsValid() || Graph->GetNodes().Num() != Wave->NumChannels + 2
        || Source->OutputFormat != (bStereo ? EMetaSoundOutputAudioFormat::Stereo : EMetaSoundOutputAudioFormat::Mono))
    {
        return false;
    }

    Source->Modify();
    const FNodeHandle Player = AddAmbientNode(Graph, TEXT("UE"), TEXT("Wave Player"),
        bStereo ? TEXT("Stereo") : TEXT("Mono"));
    const FNodeHandle Merge = AddAmbientNode(Graph, TEXT("TriggerAny"), TEXT("Trigger Any (2)"));
    const FNodeHandle Delay = AddAmbientNode(Graph, TEXT("UE"), TEXT("Trigger Delay"));
    const FNodeHandle Random = AddAmbientNode(Graph, TEXT("UE"), TEXT("RandomTime"), TEXT(""), 1);
    const FVariableHandle Repeat = Graph->AddVariable(TEXT("Trigger"));
    if (!Repeat->IsValid())
    {
        return false;
    }
    // A deferred trigger feeds the next play without a cycle in the audio graph.
    Repeat->SetName(TEXT("Repeat after playback"));
    const FNodeHandle Write = Graph->FindOrAddVariableMutatorNode(Repeat->GetID());
    const FNodeHandle Read = Graph->AddVariableDeferredAccessorNode(Repeat->GetID());
    const bool bChannelsConnected = bStereo
        ? ConnectAmbientChannel(Graph, Player, TEXT("Out Left"), TEXT("UE.OutputFormat.Stereo.Audio:0"))
            && ConnectAmbientChannel(Graph, Player, TEXT("Out Right"), TEXT("UE.OutputFormat.Stereo.Audio:1"))
        : ConnectAmbientChannel(Graph, Player, TEXT("Out Mono"), TEXT("UE.OutputFormat.Mono.Audio:0"));
    if (!bChannelsConnected
        || !SetAmbientInput(Player, TEXT("Wave Asset"), static_cast<UObject*>(Wave))
        || !SetAmbientInput(Random, TEXT("Min"), MinGapSeconds)
        || !SetAmbientInput(Random, TEXT("Max"), MaxGapSeconds)
        || !ConnectAmbientPins(Graph->GetInputNodeWithName(OnPlay), OnPlay, Merge, TEXT("In 0"))
        || !ConnectAmbientPins(Merge, TEXT("Out"), Player, TEXT("Play"))
        || !ConnectAmbientPins(Read, TEXT("Value"), Random, TEXT("Next"))
        || !ConnectAmbientPins(Random, TEXT("On Next"), Delay, TEXT("In"))
        || !ConnectAmbientPins(Random, TEXT("Value"), Delay, TEXT("Delay Time"))
        || !ConnectAmbientPins(Delay, TEXT("Out"), Merge, TEXT("In 1")))
    {
        return false;
    }

    if (RepeatCount == -1)
    {
        if (!ConnectAmbientPins(Player, TEXT("On Finished"), Write, TEXT("Value"))
            || !Graph->RemoveOutputVertex(OnFinished))
        {
            return false;
        }
        Document->RemoveInterfaceVersion(SourceOneShotInterface::GetVersion());
    }
    else if (!ConnectFiniteRepeat(Graph, Player, Write, RepeatCount))
    {
        return false;
    }

    UMetaSoundEditorSubsystem::GetChecked().RegisterGraphWithFrontend(*Source, true);
    Source->MarkPackageDirty();
    return true;
}
