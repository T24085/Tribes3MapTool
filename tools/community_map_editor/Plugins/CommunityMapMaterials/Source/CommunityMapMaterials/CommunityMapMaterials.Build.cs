using UnrealBuildTool;

public class CommunityMapMaterials : ModuleRules
{
    public CommunityMapMaterials(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.NoPCHs;
        PrivateDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine", "MaterialEditor" });
    }
}
