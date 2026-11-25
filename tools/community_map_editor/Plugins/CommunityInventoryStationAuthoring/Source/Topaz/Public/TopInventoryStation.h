#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "TopInventoryStation.generated.h"

// Editor class identity for cooking a Blueprint derived from the installed
// game's inventory station. The game supplies the runtime implementation.
UCLASS(Abstract, Blueprintable)
class TOPAZ_API ATopInventoryStation : public AActor
{
    GENERATED_BODY()

public:
    ATopInventoryStation();
};
