#pragma once
#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "CommunityWorldSettingsLibrary.generated.h"
class AValWorldSettings;
class UWorld;
class UMaterial;
class UMaterialExpression;

UCLASS()
class COMMUNITYWORLDSETTINGSEDITOR_API UCommunityWorldSettingsLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    UFUNCTION(BlueprintCallable, Category="CommunityAuthoring")
    static AValWorldSettings* ConvertWorldSettings(UWorld* World, float MapSize, const FString& MapName);

    // Python omits the hidden MP_WorldPositionOffset enum in UE 5.3.
    UFUNCTION(BlueprintCallable, Category="CommunityAuthoring")
    static bool ConnectGrassWind(UMaterial* Material, UMaterialExpression* Expression);
};
