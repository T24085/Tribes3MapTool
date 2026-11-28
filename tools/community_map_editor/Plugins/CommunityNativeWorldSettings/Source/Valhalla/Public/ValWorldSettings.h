#pragma once
#include "CoreMinimal.h"
#include "GameFramework/WorldSettings.h"
#include "UObject/SoftObjectPath.h"
#include "ValWorldSettings.generated.h"

// Serialization authoring mirror of the six original mapped properties only.
// Original runtime code is supplied by the installed game, never by this module.
UCLASS(NotBlueprintable)
class VALHALLA_API AValWorldSettings : public AWorldSettings
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere, Category="Community") TArray<FSoftObjectPath> PreloadAssets;
    UPROPERTY(EditAnywhere, Category="Community") float StallZ = 0.0f;
    UPROPERTY(EditAnywhere, Category="Community") float MaxStallZ = 0.0f;
    UPROPERTY(EditAnywhere, Category="Community") bool bStallZEnabled = false;
    // Original native constructor writes0x47c35000 (100000.0f) at MapSize.
    UPROPERTY(EditAnywhere, Category="Community") float MapSize = 100000.0f;
    UPROPERTY(EditAnywhere, Category="Community") FText MapName;
};
