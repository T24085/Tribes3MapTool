#include "CommunityMapMaterialLibrary.h"

#include "MaterialEditingLibrary.h"
#include "Modules/ModuleManager.h"

IMPLEMENT_MODULE(FDefaultModuleImpl, CommunityMapMaterials)

bool UCommunityMapMaterialLibrary::ConnectWorldPositionOffset(UMaterialExpression* Expression)
{
    return UMaterialEditingLibrary::ConnectMaterialProperty(Expression, FString(), MP_WorldPositionOffset);
}
