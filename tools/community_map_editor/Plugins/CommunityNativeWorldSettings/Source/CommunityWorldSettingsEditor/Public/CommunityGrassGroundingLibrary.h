#pragma once
#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "CommunityGrassGroundingLibrary.generated.h"
class UWorld;
class UStaticMesh;
class UFoliageType;

USTRUCT(BlueprintType)
struct COMMUNITYWORLDSETTINGSEDITOR_API FCommunityGroundingResult
{
    GENERATED_BODY()
    UPROPERTY(BlueprintReadOnly, Category="CommunityAuthoring") bool Succeeded = false;
    UPROPERTY(BlueprintReadOnly, Category="CommunityAuthoring") FString Error;
    UPROPERTY(BlueprintReadOnly, Category="CommunityAuthoring") int32 GroundedCount = 0;
    UPROPERTY(BlueprintReadOnly, Category="CommunityAuthoring") int32 SkippedTerrain = 0;
    UPROPERTY(BlueprintReadOnly, Category="CommunityAuthoring") double MaximumSampledRootGapCm = 0;
    UPROPERTY(BlueprintReadOnly, Category="CommunityAuthoring") TArray<FTransform> Transforms;
};

/** Private editor authoring only. No runtime/retail game module is changed. */
UCLASS()
class COMMUNITYWORLDSETTINGSEDITOR_API UCommunityGrassGroundingLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    UFUNCTION(BlueprintCallable, Category="CommunityAuthoring")
    static FCommunityGroundingResult GroundExistingGrass(UWorld* World, UFoliageType* Type, int32 Seed);

    UFUNCTION(BlueprintCallable, Category="CommunityAuthoring")
    static FCommunityGroundingResult GroundCandidates(UWorld* World, UStaticMesh* Mesh, const TArray<FTransform>& Candidates);
};
