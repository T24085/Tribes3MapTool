using UnrealBuildTool;
public class CommunityWorldSettingsEditor : ModuleRules
{
    public CommunityWorldSettingsEditor(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.NoPCHs;
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "Valhalla" });
        PrivateDependencyModuleNames.AddRange(new string[] { "UnrealEd", "Foliage", "MeshDescription", "StaticMeshDescription" });
    }
}
