#include "CommunityMapMotionLibrary.h"

#include "EdGraph/EdGraphNode.h"
#include "Sound/SoundCue.h"
#include "Sound/SoundNodeConcatenator.h"
#include "Sound/SoundNodeDelay.h"
#include "Sound/SoundNodeLooping.h"
#include "Sound/SoundNodeWavePlayer.h"
#include "Sound/SoundWave.h"

namespace
{
    template <class T>
    T* AddAudioNode(USoundCue* Cue, int32 X, int32 Y)
    {
        T* Node = Cue->ConstructSoundNode<T>();
        if (UEdGraphNode* GraphNode = Node->GetGraphNode())
        {
            GraphNode->NodePosX = X;
            GraphNode->NodePosY = Y;
        }
        return Node;
    }

    void SetChildren(USoundNode* Node, std::initializer_list<USoundNode*> Children)
    {
        if (Node->ChildNodes.IsEmpty())
        {
            Node->CreateStartingConnectors();
        }
        TArray<USoundNode*> Inputs(Children);
        Node->SetChildNodes(Inputs);
    }
}

bool UCommunityMapMotionLibrary::ConfigureAmbientRepeat(USoundCue* Cue, USoundWave* Wave, int32 RepeatCount,
    float MinGapSeconds, float MaxGapSeconds)
{
    if (!IsValid(Cue) || Cue->FirstNode || !Cue->GetGraph() || !IsValid(Wave) || Wave->IsLooping()
        || RepeatCount < -1 || RepeatCount == 0 || RepeatCount > 10000
        || !FMath::IsFinite(MinGapSeconds) || !FMath::IsFinite(MaxGapSeconds)
        || MinGapSeconds < 0 || MaxGapSeconds < MinGapSeconds || MaxGapSeconds > 600)
    {
        return false;
    }

    Cue->Modify();
    USoundNodeConcatenator* Sequence = AddAudioNode<USoundNodeConcatenator>(Cue, -200, 0);
    USoundNodeWavePlayer* FirstPlay = AddAudioNode<USoundNodeWavePlayer>(Cue, -480, -120);
    FirstPlay->SetSoundWave(Wave);
    USoundNodeLooping* Repeats = AddAudioNode<USoundNodeLooping>(Cue, -480, 160);
    Repeats->bLoopIndefinitely = RepeatCount == -1;
    Repeats->LoopCount = FMath::Max(RepeatCount, 1);

    USoundNodeWavePlayer* RepeatPlay = AddAudioNode<USoundNodeWavePlayer>(Cue, -1020, 160);
    RepeatPlay->SetSoundWave(Wave);
    USoundNode* RepeatInput = RepeatPlay;
    if (MaxGapSeconds > 0)
    {
        USoundNodeDelay* Gap = AddAudioNode<USoundNodeDelay>(Cue, -740, 160);
        Gap->DelayMin = MinGapSeconds;
        Gap->DelayMax = MaxGapSeconds;
        SetChildren(Gap, {RepeatPlay});
        RepeatInput = Gap;
    }
    // Loop payload resets the delay on every repeat. The first play starts immediately.
    SetChildren(Repeats, {RepeatInput});
    SetChildren(Sequence, {FirstPlay, Repeats});
    Cue->FirstNode = Sequence;
    Cue->LinkGraphNodesFromSoundNodes();
    Cue->CacheAggregateValues();
    Cue->MarkPackageDirty();
    return true;
}
