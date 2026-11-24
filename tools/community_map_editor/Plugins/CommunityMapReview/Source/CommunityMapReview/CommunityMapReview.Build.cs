using UnrealBuildTool;

public class CommunityMapReview : ModuleRules
{
    public CommunityMapReview(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.NoPCHs;
        PrivateDependencyModuleNames.AddRange(new[] {
            "Core", "CoreUObject", "Engine", "InputCore", "Json", "Slate", "SlateCore", "UnrealEd"
        });
    }
}
