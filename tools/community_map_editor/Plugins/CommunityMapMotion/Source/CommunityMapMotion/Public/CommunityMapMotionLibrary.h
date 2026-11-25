#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "CommunityMapMotionLibrary.generated.h"

class UGeometryCache;
class UMaterialInterface;
class UMetaSoundSource;
class ALevelSequenceActor;
class UStaticMesh;
class USoundCue;
class USoundWave;

USTRUCT(BlueprintType)
struct FCommunityMotionSection
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Geometry Cache")
    int32 MaterialIndex = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Geometry Cache")
    TArray<FVector> Corners;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Geometry Cache")
    TArray<FVector2D> TextureCoordinates;
};

USTRUCT(BlueprintType)
struct FCommunityMotionFrame
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Geometry Cache")
    float Seconds = 0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Geometry Cache")
    TArray<FCommunityMotionSection> Sections;
};

UCLASS()
class UCommunityMapMotionLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static UGeometryCache* BuildGeometryCache(const FString& PackageName, const TArray<FCommunityMotionFrame>& Frames,
        const TArray<UMaterialInterface*>& Materials);

    UFUNCTION(BlueprintPure, Category = "Community Map Motion")
    static int32 InspectTriangleCount(UGeometryCache* Cache, float Seconds);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static bool BindTimedDoor(AActor* Brush, ALevelSequenceActor* Sequence, bool bPlayerOnly);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static bool ConfigurePermanentDoor(ALevelSequenceActor* Sequence);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static bool SetConvexVolumeMesh(UStaticMesh* Mesh, const TArray<FVector>& Vertices);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static bool BindZeroGravityVolumes(const TArray<AActor*>& Volumes);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static UClass* BuildLinkedElevatorClass(const FString& PackageName);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static UClass* BuildProximityReceiverClass(const FString& PackageName, int32 Mode);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static bool ConfigureLinkedElevator(AActor* Field, bool bStartsActive);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static bool BindElevatorTrigger(AActor* Trigger, const TArray<AActor*>& Fields, int32 ActivationMode,
        bool bOnce, double RetriggerDelay);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static bool BindRepeatingElevatorTrigger(AActor* Trigger, const TArray<AActor*>& Fields,
        int32 ActivationMode, double RetriggerDelay, double RepeatInterval);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static bool BindBrushTrigger(AActor* Trigger, const TArray<AActor*>& Receivers,
        int32 ActivationMode, bool bOnce, double RetriggerDelay, double RepeatInterval);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static bool ConfigureAmbientRepeat(USoundCue* Cue, USoundWave* Wave, int32 RepeatCount,
        float MinGapSeconds, float MaxGapSeconds);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static bool ConfigureAmbientMetaSound(UMetaSoundSource* Source, USoundWave* Wave, int32 RepeatCount,
        float MinGapSeconds, float MaxGapSeconds);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static UClass* BuildDirectionalAmbientClass(const FString& PackageName);

    UFUNCTION(BlueprintCallable, Category = "Community Map Motion")
    static bool ConfigureDirectionalAmbient(AActor* Emitter, double InnerAngle, double OuterAngle,
        double InnerGain, double OuterGain);
};
