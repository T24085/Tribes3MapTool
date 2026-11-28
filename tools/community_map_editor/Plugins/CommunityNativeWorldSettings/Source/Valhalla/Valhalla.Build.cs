using UnrealBuildTool;
public class Valhalla : ModuleRules
{
    public Valhalla(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.NoPCHs;
        // UE 5.3 headers use __has_feature, which MSVC 14.40 and newer reject as undefined.
        PublicDefinitions.Add("__has_feature(x)=0");
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "GameplayTags" });
    }
}
