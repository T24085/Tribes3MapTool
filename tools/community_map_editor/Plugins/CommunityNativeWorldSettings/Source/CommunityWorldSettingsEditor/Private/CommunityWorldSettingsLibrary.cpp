#include "CommunityWorldSettingsLibrary.h"
#include "ValWorldSettings.h"
#include "Engine/World.h"
#include "Engine/Level.h"
#include "Engine/Engine.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpression.h"

bool UCommunityWorldSettingsLibrary::ConnectGrassWind(UMaterial* Material, UMaterialExpression* Expression)
{
    if (!IFileManager::Get().FileExists(*FPaths::Combine(FPaths::ProjectDir(), TEXT(".map-2026-authoring"))) ||
        !Material || !Expression || Expression->GetOuter() != Material ||
        !Material->GetPathName().StartsWith(TEXT("/Game/Community/")) ||
        !Material->GetPathName().EndsWith(TEXT("/Edition2026/Vegetation/M_Grass2026.M_Grass2026")))
    {
        return false;
    }
    FExpressionInput* Input = Material->GetExpressionInputForProperty(MP_WorldPositionOffset);
    if (!Input) return false;
    Input->Connect(0, Expression);
    Material->MarkPackageDirty();
    return Input->Expression == Expression && Input->OutputIndex == 0;
}

AValWorldSettings* UCommunityWorldSettingsLibrary::ConvertWorldSettings(UWorld* World, float MapSize, const FString& MapName)
{
    // The conversion is deliberately restricted to a fresh private authoring copy.
    if (!IFileManager::Get().FileExists(*FPaths::Combine(FPaths::ProjectDir(), TEXT(".community-worldsettings-authoring"))) ||
        !World || !World->PersistentLevel || !FMath::IsFinite(MapSize) || MapSize <= 0.0f || MapName.IsEmpty())
    {
        UE_LOG(LogTemp, Error, TEXT("COMMUNITY_WORLD_SETTINGS_REJECTED: isolated project marker and finite map contract required"));
        return nullptr;
    }
    ULevel* Level = World->PersistentLevel;
    AWorldSettings* Old = World->GetWorldSettings(false, true);
    if (!Old || Old->GetLevel() != Level)
    {
        UE_LOG(LogTemp, Error, TEXT("COMMUNITY_WORLD_SETTINGS_REJECTED: persistent settings identity mismatch"));
        return nullptr;
    }
    int32 Count = 0;
    for (AActor* Actor : Level->Actors) if (Actor && Actor->IsA<AWorldSettings>()) ++Count;
    if (Count != 1)
    {
        UE_LOG(LogTemp, Error, TEXT("COMMUNITY_WORLD_SETTINGS_REJECTED: ambiguous preexisting settings actors"));
        return nullptr;
    }
    AValWorldSettings* Result = Cast<AValWorldSettings>(Old);
    if (!Result)
    {
        if (Old->GetClass() != AWorldSettings::StaticClass())
        {
            UE_LOG(LogTemp, Error, TEXT("COMMUNITY_WORLD_SETTINGS_REJECTED: foreign settings subclass"));
            return nullptr;
        }
        FActorSpawnParameters Params;
        Params.OverrideLevel = Level;
        Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
        Params.ObjectFlags |= RF_Transactional;
        Params.Name = MakeUniqueObjectName(Level, AValWorldSettings::StaticClass(), TEXT("CommunityWorldSettings"));
        Result = World->SpawnActor<AValWorldSettings>(FVector::ZeroVector, FRotator::ZeroRotator, Params);
        if (!Result) return nullptr;
        UEngine::FCopyPropertiesForUnrelatedObjectsParams Copy;
        Copy.bDoDelta = false;
        UEngine::CopyPropertiesForUnrelatedObjects(Old, Result, Copy);
        // Normal native owner API updates the cache/actor list and retires Old.
        // No raw pointer, actor-array or class-import byte patches are used.
        Level->SetWorldSettings(Result);
    }
    Result->MapSize = MapSize;
    Result->MapName = FText::FromString(MapName);
    Result->MarkPackageDirty();
    Level->MarkPackageDirty();
    if (World->GetWorldSettings(false, true) != Result || Result->GetClass()->GetPathName() != TEXT("/Script/Valhalla.ValWorldSettings"))
    {
        UE_LOG(LogTemp, Error, TEXT("COMMUNITY_WORLD_SETTINGS_FAILED: native owner did not select the authored settings"));
        return nullptr;
    }
    UE_LOG(LogTemp, Display, TEXT("COMMUNITY_WORLD_SETTINGS_AUTHORED|map=%s|size=%f|class=%s"), *MapName, MapSize, *Result->GetClass()->GetPathName());
    return Result;
}
