#include "TopInventoryStation.h"

#include "Components/SceneComponent.h"
#include "Modules/ModuleManager.h"

ATopInventoryStation::ATopInventoryStation()
{
    RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("SceneComp"));
}

IMPLEMENT_MODULE(FDefaultModuleImpl, Topaz)
