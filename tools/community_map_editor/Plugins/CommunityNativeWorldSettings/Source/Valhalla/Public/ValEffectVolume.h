#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Volume.h"
#include "GameplayTagContainer.h"
#include "ValEffectVolume.generated.h"

// Serialization authoring mirror of the original effect volume (lava, acid and other hazards).
// Only the properties a community map saves are declared, with the names and types read from the
// cooked Firestorm (T2 2026) lava volume. Original runtime code is supplied by the installed game,
// never by this module (T3BackEndServer#150).
UCLASS(NotBlueprintable)
class VALHALLA_API AValEffectVolume : public AVolume
{
    GENERATED_BODY()
public:
    // The gameplay effect applied while an actor is inside, e.g. /Game/Abilities/Effects/GE_Damage_Generic.
    UPROPERTY(EditAnywhere, Category="Community") TObjectPtr<UClass> EffectToApply;
    // The kill-feed damage source, e.g. DamageSource.Lava.
    UPROPERTY(EditAnywhere, Category="Community") FGameplayTag DamageSourceTag;
    // The set-by-caller tag that carries DamagePerSec into the effect, e.g. Data.Damage.
    UPROPERTY(EditAnywhere, Category="Community") FGameplayTag SetByCallerMagnitudeTag;
    UPROPERTY(EditAnywhere, Category="Community") float DamagePerSec = 0.0f;
    UPROPERTY(EditAnywhere, Category="Community") float PainInterval = 0.0f;
};
