#include "CommunityGrassGroundingLibrary.h"
#include "InstancedFoliageActor.h"
#include "CollisionQueryParams.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/EngineTypes.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "StaticMeshAttributes.h"
#include "MeshDescription.h"
#include "Math/RotationMatrix.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "UObject/Package.h"

namespace
{
struct FGroundContext
{
    UWorld* World;
    FCollisionQueryParams Query;
    TSet<AActor*> Terrain;
    TArray<FVector> Roots;
    TArray<FVector> Hull;

    explicit FGroundContext(UWorld* InWorld) : World(InWorld), Query(SCENE_QUERY_STAT(CommunityGrassGrounding), true) {}

    bool Init(UStaticMesh* Mesh, FString& Error)
    {
        if (!World || !Mesh || !IFileManager::Get().FileExists(*FPaths::Combine(FPaths::ProjectDir(),TEXT(".map-2026-authoring"))) ||
            !World->GetOutermost()->GetName().StartsWith(TEXT("/Game/Maps/Community/")) ||
            !World->GetOutermost()->GetName().EndsWith(TEXT("_2026_P")) ||
            !Mesh->GetPathName().StartsWith(TEXT("/Game/Community/")))
        { Error=TEXT("Private Edition2026 world and source mesh required"); return false; }
        for (TActorIterator<AActor> It(World); It; ++It)
        {
            AActor* Actor=*It;
            if (Actor->GetActorLabel().StartsWith(TEXT("2026 terrain ")))
            {
                AStaticMeshActor* Static=Cast<AStaticMeshActor>(Actor);
                if (!Static || !Static->GetStaticMeshComponent()->GetStaticMesh() ||
                    Static->GetStaticMeshComponent()->GetStaticMesh()->GetNumSections(0)<=0)
                { Error=TEXT("Terrain collision data not ready"); return false; }
                Terrain.Add(Actor);
            }
            else Query.AddIgnoredActor(Actor);
        }
        if (Terrain.Num()!=25) { Error=TEXT("Expected exactly25 private terrain actors"); return false; }
        if (Mesh->GetNumSections(0)<=0) { Error=TEXT("Grass mesh not ready"); return false; }
        // The far mesh may have a different root footprint. Support the union
        // so switching foliage LOD cannot expose roots above a sloped surface.
        for (int32 Lod=0; Lod<Mesh->GetNumSourceModels(); ++Lod)
        {
            if(Mesh->GetNumSections(Lod)<=0){Error=TEXT("Grass LOD is not ready");return false;}
            const FMeshDescription* Description=Mesh->GetMeshDescription(Lod);
            if(!Description){Error=TEXT("Grass LOD source vertices unavailable");return false;}
            FStaticMeshConstAttributes Attributes(*Description);auto Positions=Attributes.GetVertexPositions();
            double Minimum=MAX_dbl;
            for(FVertexID Id:Description->Vertices().GetElementIDs())Minimum=FMath::Min(Minimum,double(Positions[Id].Z));
            for(FVertexID Id:Description->Vertices().GetElementIDs())
            {FVector P(Positions[Id]);if(FMath::Abs(P.Z-Minimum)<.01)Roots.AddUnique(P);}
        }
        if (Roots.Num()<3) { Error=TEXT("Grass root footprint is incomplete"); return false; }
        TArray<FVector> Sorted=Roots;
        Sorted.Sort([](const FVector& A,const FVector& B){ return A.X==B.X ? A.Y<B.Y : A.X<B.X; });
        auto Cross=[](const FVector& A,const FVector& B,const FVector& C){return (B.X-A.X)*(C.Y-A.Y)-(B.Y-A.Y)*(C.X-A.X);};
        TArray<FVector> Upper;
        for (const FVector& P:Sorted)
        { while(Hull.Num()>=2 && Cross(Hull[Hull.Num()-2],Hull.Last(),P)<=0) Hull.Pop(); Hull.Add(P); }
        for (int32 Index=Sorted.Num()-1; Index>=0; --Index)
        { const FVector& P=Sorted[Index];while(Upper.Num()>=2 && Cross(Upper[Upper.Num()-2],Upper.Last(),P)<=0) Upper.Pop(); Upper.Add(P); }
        Hull.Pop();Upper.Pop();Hull.Append(Upper);
        if (Hull.Num()<3) { Error=TEXT("Degenerate grass root hull"); return false; }
        return true;
    }

    bool Hit(double X,double Y,FHitResult& Out) const
    {
        return World->LineTraceSingleByChannel(Out,FVector(X,Y,60000),FVector(X,Y,-100000),
            UEngineTypes::ConvertToCollisionChannel(ETraceTypeQuery::TraceTypeQuery1),Query) && Terrain.Contains(Out.GetActor());
    }

    bool Ground(const FTransform& Input,FTransform& Output,double MinimumNormal=.7) const
    {
        FHitResult Center;
        const FVector Position=Input.GetTranslation();
        if (!Hit(Position.X,Position.Y,Center) || Center.ImpactNormal.Z<MinimumNormal) return false;
        const FQuat Rotation=FRotationMatrix::MakeFromZ(Center.ImpactNormal).ToQuat()*Input.GetRotation();
        Output=FTransform(Rotation,Center.ImpactPoint-FVector(0,0,3),Input.GetScale3D());
        double Maximum=-MAX_dbl;
        for(const FVector& Root:Hull)
        {
            const FVector P=Output.TransformPosition(Root);FHitResult Support;
            if(!Hit(P.X,P.Y,Support)) return false;
            Maximum=FMath::Max(Maximum,P.Z-Support.ImpactPoint.Z);
        }
        if(Maximum>-1) Output.AddToTranslation(FVector(0,0,-Maximum-1));
        return true;
    }
};
}

FCommunityGroundingResult UCommunityGrassGroundingLibrary::GroundCandidates(UWorld* World,UStaticMesh* Mesh,const TArray<FTransform>& Candidates)
{
    FCommunityGroundingResult Result;
    // A reflected call retains at most one small batch; the native loops avoid
    // expanding every footprint trace into many Python-owned result structs.
    if(Candidates.Num()>2048) { Result.Error=TEXT("Use batches of at most2048 candidates");return Result; }
    FGroundContext Context(World);if(!Context.Init(Mesh,Result.Error))return Result;
    Result.Transforms.Reserve(Candidates.Num());
    for(const FTransform& Input:Candidates)
    {
        if(Input.ContainsNaN() || Input.GetScale3D().GetMin()<=0)
        {Result.Error=TEXT("Candidate transform is invalid");Result.Transforms.Reset();return Result;}
        FTransform Grounded;
        if(Context.Ground(Input,Grounded,.78))Result.Transforms.Add(Grounded);
        else ++Result.SkippedTerrain;
    }
    Result.GroundedCount=Result.Transforms.Num();Result.Succeeded=true;return Result;
}

FCommunityGroundingResult UCommunityGrassGroundingLibrary::GroundExistingGrass(UWorld* World,UFoliageType* Type,int32 Seed)
{
    FCommunityGroundingResult Result;
    UFoliageType_InstancedStaticMesh* StaticType=Cast<UFoliageType_InstancedStaticMesh>(Type);
    if(!StaticType || !Type->GetPathName().Contains(TEXT("/Edition2026/Vegetation/FT_")))
    {Result.Error=TEXT("Expected a private Edition2026 grass foliage type");return Result;}
    FGroundContext Context(World);if(!Context.Init(StaticType->GetStaticMesh(),Result.Error))return Result;
    AInstancedFoliageActor* Owner=nullptr;FFoliageInfo* Info=nullptr;
    for(TActorIterator<AInstancedFoliageActor> It(World);It;++It)
    {
        if(FFoliageInfo* Found=It->FindInfo(Type))
        {if(Info){Result.Error=TEXT("Ambiguous foliage owner");return Result;}Owner=*It;Info=Found;}
    }
    if(!Info || Info->Instances.IsEmpty() || !Info->GetComponent() || Info->GetComponent()->GetCollisionEnabled()!=ECollisionEnabled::NoCollision)
    {Result.Error=TEXT("Missing noncolliding foliage owner");return Result;}
    TArray<FTransform> Prepared;Prepared.Reserve(Info->Instances.Num());FRandomStream Random(Seed);
    for(const FFoliageInstance& Instance:Info->Instances)
    {
        FTransform Input(FRotator(0,Random.FRandRange(-360.f,0.f),0),Instance.Location,FVector(Instance.DrawScale3D));
        FTransform Grounded;
        if(!Context.Ground(Input,Grounded)){Result.Error=TEXT("Existing tuft has no usable terrain support");return Result;}
        Prepared.Add(Grounded);
    }
    double Maximum=-MAX_dbl;
    const int32 Samples=FMath::Min(512,Prepared.Num());
    for(int32 Sample=0;Sample<Samples;++Sample)
    {
        const int32 Index=Samples==1 ? 0 : FMath::RoundToInt(double(Sample)*(Prepared.Num()-1)/(Samples-1));
        for(const FVector& Root:Context.Roots)
        {
            const FVector P=Prepared[Index].TransformPosition(Root);FHitResult Support;
            if(!Context.Hit(P.X,P.Y,Support)){Result.Error=TEXT("Verification root has no terrain support");return Result;}
            Maximum=FMath::Max(Maximum,P.Z-Support.ImpactPoint.Z);
        }
    }
    if(Maximum>=1){Result.Error=TEXT("Root verification exceeds1cm");return Result;}
    // The serialized FoliageInfo owns the transforms and spatial hash. Its
    // movement lifecycle synchronizes both metadata and render implementation.
    TArray<int32> Indices;Indices.Reserve(Prepared.Num());for(int32 I=0;I<Prepared.Num();++I)Indices.Add(I);
    Owner->Modify();Info->PreMoveInstances(Indices);
    for(int32 I=0;I<Prepared.Num();++I)Info->Instances[I].SetInstanceWorldTransform(Prepared[I]);
    Info->PostMoveInstances(Indices,true);Info->Refresh(false,true);Owner->MarkPackageDirty();
    Result.GroundedCount=Prepared.Num();Result.MaximumSampledRootGapCm=Maximum;Result.Succeeded=true;return Result;
}
