#include "CommunityMapMotionLibrary.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "GeometryCache.h"
#include "GeometryCacheCodecV1.h"
#include "GeometryCacheMeshData.h"
#include "GeometryCacheTrackStreamable.h"
#include "MaterialDomain.h"
#include "Materials/Material.h"
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include "UObject/Package.h"

namespace
{
    bool ValidFrames(const TArray<FCommunityMotionFrame>& Frames, int32 MaterialCount)
    {
        if (Frames.Num() < 2 || Frames.Num() > 4096 || Frames[0].Seconds != 0 || MaterialCount < 1 || MaterialCount > 256)
        {
            return false;
        }
        int64 TotalCorners = 0;
        float PreviousTime = -1;
        for (const FCommunityMotionFrame& Frame : Frames)
        {
            if (!FMath::IsFinite(Frame.Seconds) || Frame.Seconds <= PreviousTime || Frame.Seconds > 600)
            {
                return false;
            }
            PreviousTime = Frame.Seconds;
            for (const FCommunityMotionSection& Section : Frame.Sections)
            {
                const int32 Count = Section.Corners.Num();
                TotalCorners += Count;
                if (Section.MaterialIndex < 0 || Section.MaterialIndex >= MaterialCount || Count < 3 || Count % 3 != 0
                    || Section.TextureCoordinates.Num() != Count || TotalCorners > 1200000)
                {
                    return false;
                }
                for (int32 Corner = 0; Corner < Count; ++Corner)
                {
                    if (Section.Corners[Corner].ContainsNaN() || Section.TextureCoordinates[Corner].ContainsNaN())
                    {
                        return false;
                    }
                }
            }
        }
        return TotalCorners > 0;
    }

    void MeshSample(const FCommunityMotionFrame& Frame, FGeometryCacheMeshData& Sample)
    {
        Sample.BoundingBox.Init();
        Sample.VertexInfo.bHasTangentX = true;
        Sample.VertexInfo.bHasTangentZ = true;
        Sample.VertexInfo.bHasUV0 = true;
        for (const FCommunityMotionSection& Section : Frame.Sections)
        {
            Sample.BatchesInfo.Add({static_cast<uint32>(Sample.Indices.Num()),
                static_cast<uint32>(Section.Corners.Num() / 3), static_cast<uint32>(Section.MaterialIndex)});
            for (int32 Index = 0; Index < Section.Corners.Num(); Index += 3)
            {
                const FVector Edge1 = Section.Corners[Index + 1] - Section.Corners[Index];
                const FVector Edge2 = Section.Corners[Index + 2] - Section.Corners[Index];
                const FVector2D UV1 = Section.TextureCoordinates[Index + 1] - Section.TextureCoordinates[Index];
                const FVector2D UV2 = Section.TextureCoordinates[Index + 2] - Section.TextureCoordinates[Index];
                const double Determinant = UV1.X * UV2.Y - UV1.Y * UV2.X;
                FVector Normal = FVector::CrossProduct(Edge1, Edge2).GetSafeNormal();
                if (Normal.IsNearlyZero())
                {
                    Normal = FVector::UpVector;
                }
                FVector Tangent = Edge1.GetSafeNormal();
                if (!FMath::IsNearlyZero(Determinant))
                {
                    Tangent = ((Edge1 * UV2.Y - Edge2 * UV1.Y) / Determinant).GetSafeNormal();
                }
                if (Tangent.IsNearlyZero())
                {
                    Tangent = FVector::CrossProduct(FMath::Abs(Normal.Z) < .9 ? FVector::UpVector : FVector::RightVector,
                        Normal).GetSafeNormal();
                }
                const float Sign = Determinant < 0 ? -1.f : 1.f;
                const FPackedNormal PackedTangent(FVector4f(FVector3f(Tangent), 0));
                const FPackedNormal PackedNormal(FVector4f(FVector3f(Normal), Sign));
                for (int32 Corner = Index; Corner < Index + 3; ++Corner)
                {
                    const FVector3f Position(Section.Corners[Corner]);
                    Sample.Indices.Add(Sample.Positions.Add(Position));
                    Sample.BoundingBox += Position;
                    Sample.TextureCoordinates.Add(FVector2f(Section.TextureCoordinates[Corner]));
                    Sample.TangentsX.Add(PackedTangent);
                    Sample.TangentsZ.Add(PackedNormal);
                }
            }
        }
    }
}

UGeometryCache* UCommunityMapMotionLibrary::BuildGeometryCache(const FString& PackageName,
    const TArray<FCommunityMotionFrame>& Frames, const TArray<UMaterialInterface*>& Materials)
{
    if (!PackageName.StartsWith(TEXT("/Game/Community/T1Motion/")) || !FPackageName::IsValidLongPackageName(PackageName)
        || !ValidFrames(Frames, Materials.Num()) || FPackageName::DoesPackageExist(PackageName)
        || FindPackage(nullptr, *PackageName))
    {
        return nullptr;
    }
    UPackage* Package = CreatePackage(*PackageName);
    const FName AssetName(*FPackageName::GetLongPackageAssetName(PackageName));
    UGeometryCache* Cache = NewObject<UGeometryCache>(Package, AssetName, RF_Public | RF_Standalone);
    for (int32 Index = 0; Index < Materials.Num(); ++Index)
    {
        Cache->Materials.Add(Materials[Index] ? Materials[Index] : UMaterial::GetDefaultMaterial(MD_Surface));
        Cache->MaterialSlotNames.Add(FName(*FString::Printf(TEXT("SourceMaterial_%d"), Index)));
    }

    UGeometryCacheTrackStreamable* Track = NewObject<UGeometryCacheTrackStreamable>(Cache);
    UGeometryCacheCodecV1* Codec = NewObject<UGeometryCacheCodecV1>(Track);
    Codec->InitializeEncoder(.01f, 16);
    Track->BeginCoding(Codec, false, false, false);
    const FCommunityMotionFrame* Visible = Frames.FindByPredicate(
        [](const FCommunityMotionFrame& Frame) { return !Frame.Sections.IsEmpty(); });
    for (const FCommunityMotionFrame& Frame : Frames)
    {
        const bool bVisible = !Frame.Sections.IsEmpty();
        if (bVisible)
        {
            Visible = &Frame;
        }
        FGeometryCacheMeshData Sample;
        MeshSample(*Visible, Sample);
        Track->AddMeshSample(Sample, Frame.Seconds, false);
        Track->AddVisibilitySample(bVisible, Frame.Seconds);
    }
    Track->SetMatrixSamples({FMatrix::Identity, FMatrix::Identity}, {0.f, Frames.Last().Seconds});
    if (!Track->EndCoding())
    {
        return nullptr;
    }
    Cache->AddTrack(Track);
    Cache->SetFrameStartEnd(0, Frames.Num() - 1);
    FAssetRegistryModule::AssetCreated(Cache);
    Cache->MarkPackageDirty();
    return Cache;
}

int32 UCommunityMapMotionLibrary::InspectTriangleCount(UGeometryCache* Cache, float Seconds)
{
    if (!IsValid(Cache) || !FMath::IsFinite(Seconds) || Seconds < 0)
    {
        return -1;
    }
    TArray<FGeometryCacheMeshData> Meshes;
    Cache->GetMeshDataAtTime(Seconds, Meshes);
    int32 TriangleCount = 0;
    for (const FGeometryCacheMeshData& Mesh : Meshes)
    {
        TriangleCount += Mesh.Indices.Num() / 3;
    }
    return TriangleCount;
}

IMPLEMENT_MODULE(FDefaultModuleImpl, CommunityMapMotion)
