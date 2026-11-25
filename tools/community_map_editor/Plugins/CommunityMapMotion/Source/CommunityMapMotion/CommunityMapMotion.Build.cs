using UnrealBuildTool;

public class CommunityMapMotion : ModuleRules
{
    public CommunityMapMotion(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.NoPCHs;
        PrivateDependencyModuleNames.AddRange(new[] {
            "Core", "CoreUObject", "Engine", "AssetRegistry", "GeometryCache",
            "BlueprintGraph", "UnrealEd", "KismetCompiler", "LevelSequence", "MovieScene", "PhysicsCore", "Chaos",
            "MetasoundEditor", "MetasoundEngine", "MetasoundFrontend", "MetasoundGraphCore"
        });
    }
}
