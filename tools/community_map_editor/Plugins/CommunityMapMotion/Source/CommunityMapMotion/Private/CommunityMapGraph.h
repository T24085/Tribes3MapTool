#pragma once

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphPin.h"
#include "K2Node_CallFunction.h"

class ALevelSequenceActor;

class FCommunityMapGraph
{
public:
    explicit FCommunityMapGraph(UEdGraph& InGraph) : Graph(InGraph) {}

    template <typename Node>
    Node* Add(int32 X, int32 Y, TFunctionRef<void(Node&)> Initialize = [](Node&) {})
    {
        FGraphNodeCreator<Node> Creator(Graph);
        Node* Result = Creator.CreateNode();
        Result->NodePosX = X;
        Result->NodePosY = Y;
        Initialize(*Result);
        Creator.Finalize();
        Nodes.Add(Result);
        return Result;
    }

    UK2Node_CallFunction* Call(const UFunction* Function, int32 X, int32 Y)
    {
        return Add<UK2Node_CallFunction>(X, Y, [Function](auto& Node) { Node.SetFromFunction(Function); });
    }

    bool Connect(UEdGraphPin* Output, UEdGraphPin* Input)
    {
        if (Output && Input && Graph.GetSchema()->TryCreateConnection(Output, Input))
        {
            return true;
        }
        UE_LOG(LogTemp, Warning, TEXT("Could not connect generated map Blueprint pins %s and %s."),
            Output ? *Output->PinName.ToString() : TEXT("<missing output>"),
            Input ? *Input->PinName.ToString() : TEXT("<missing input>"));
        return false;
    }

    void Remove()
    {
        for (UEdGraphNode* Node : Nodes)
        {
            Node->BreakAllNodeLinks();
            Graph.RemoveNode(Node);
        }
    }

private:
    UEdGraph& Graph;
    TArray<UEdGraphNode*> Nodes;
};

bool AddTimedSequenceStart(FCommunityMapGraph& Graph, ALevelSequenceActor* Sequence, UEdGraphPin* Exec, int32 X, int32 Y);
