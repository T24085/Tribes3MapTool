#pragma once

#include "Kismet/BlueprintFunctionLibrary.h"
#include "CommunityMapMaterialLibrary.generated.h"

class UMaterialExpression;

UCLASS()
class UCommunityMapMaterialLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable, Category = "Community Map Materials")
    static bool ConnectWorldPositionOffset(UMaterialExpression* Expression);
};
