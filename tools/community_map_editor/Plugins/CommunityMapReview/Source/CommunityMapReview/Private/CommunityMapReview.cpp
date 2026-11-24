#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/World.h"
#include "Framework/Docking/TabManager.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformFileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/Input/SComboBox.h"
#include "Widgets/Input/SSearchBox.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSplitter.h"
#include "Widgets/Layout/SWidgetSwitcher.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"
#include "Widgets/Views/SHeaderRow.h"
#include "Widgets/Views/SListView.h"

#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"
#endif

#define LOCTEXT_NAMESPACE "CommunityMapReview"

namespace
{
const FName ReviewTabId(TEXT("TribesObjectReview"));
using FRow = TSharedPtr<FJsonObject>;

FString ReadString(const FRow& Object, const FString& Field)
{
    FString Result;
    if (Object.IsValid())
    {
        Object->TryGetStringField(Field, Result);
    }
    return Result;
}

bool ReadBool(const FRow& Object, const FString& Field)
{
    bool Result = false;
    Object->TryGetBoolField(Field, Result);
    return Result;
}

const TArray<TSharedPtr<FJsonValue>>& ReadArray(const FRow& Object, const FString& Field)
{
    static const TArray<TSharedPtr<FJsonValue>> Empty;
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    return Object.IsValid() && Object->TryGetArrayField(Field, Values) && Values ? *Values : Empty;
}

FText Label(const FString& Text)
{
    return FText::FromString(Text);
}

FString ActiveDraft()
{
    const UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
    if (!World)
    {
        return {};
    }
    const FString Package = World->GetOutermost()->GetName();
    const FString Draft = FPaths::GetCleanFilename(FPaths::GetPath(Package));
    const FString Level = FString::Printf(TEXT("Arena_%s_P"), *Draft);
    return Package.StartsWith(TEXT("/Game/Maps/Community/"))
        && (Draft.StartsWith(TEXT("DraftT1")) || Draft.StartsWith(TEXT("DraftT2"))
            || Draft.StartsWith(TEXT("DraftTV")) || Draft.StartsWith(TEXT("DraftTA")))
        && Draft.Len() == 10 && Draft.Right(3).IsNumeric() && FPaths::GetCleanFilename(Package) == Level
        ? Draft : FString();
}

class SReviewRow final : public SMultiColumnTableRow<FRow>
{
public:
    SLATE_BEGIN_ARGS(SReviewRow) {}
        SLATE_ARGUMENT(FRow, Entry)
    SLATE_END_ARGS()

    void Construct(const FArguments& Args, const TSharedRef<STableViewBase>& Owner)
    {
        Entry = Args._Entry;
        SMultiColumnTableRow<FRow>::Construct(FSuperRowType::FArguments(), Owner);
    }

    TSharedRef<SWidget> GenerateWidgetForColumn(const FName& Column) override
    {
        const FString Value = ReadString(Entry, Column.ToString());
        TSharedRef<STextBlock> Text = SNew(STextBlock)
            .Text(Label(Value))
            .ToolTipText(Label(Value))
            .Margin(FMargin(5.f, 6.f))
            .Clipping(EWidgetClipping::OnDemand);
        if (Column == TEXT("status") && ReadBool(Entry, TEXT("blocked")))
        {
            Text->SetColorAndOpacity(FLinearColor(0.92f, 0.27f, 0.31f));
        }
        return Text;
    }

private:
    FRow Entry;
};

class SObjectReview final : public SCompoundWidget
{
public:
    SLATE_BEGIN_ARGS(SObjectReview) {}
    SLATE_END_ARGS()

    void Construct(const FArguments&)
    {
        Reload();
        MapOpened = FEditorDelegates::OnMapOpened.AddSP(this, &SObjectReview::OnMapOpened);
    }

    ~SObjectReview() override
    {
        FEditorDelegates::OnMapOpened.Remove(MapOpened);
    }

#if WITH_DEV_AUTOMATION_TESTS
    void RunChecks(FAutomationTestBase& Test)
    {
        Test.TestTrue(TEXT("Native tab consumed the active draft review"), Rows.Num() > 0 && List.IsValid());
        if (Rows.IsEmpty() || !List.IsValid())
        {
            return;
        }
        const FRow First = Rows[0];
        const bool HasBlocked = Rows.ContainsByPredicate([](const FRow& Row) { return ReadBool(Row, TEXT("blocked")); });
        Test.TestTrue(TEXT("Blocked objects sort to the top"), !HasBlocked || ReadBool(First, TEXT("blocked")));
        const FRow* Inspected = Rows.FindByPredicate([](const FRow& Row)
        {
            return ReadString(Row, TEXT("type")) == TEXT("Engine.Trigger") && !ReadArray(Row, TEXT("fields")).IsEmpty();
        });
        if (!Inspected)
        {
            Inspected = Rows.FindByPredicate([](const FRow& Row) { return !ReadArray(Row, TEXT("fields")).IsEmpty(); });
        }
        const FRow Selection = Inspected ? *Inspected : First;
        SetSearch(Label(ReadString(Selection, TEXT("object"))));
        Test.TestTrue(TEXT("Search keeps matching entries and removes others"),
            Filtered.Contains(Selection) && Filtered.Num() <= Rows.Num());
        List->SetSelection(Selection);
        const int32 ExpectedDetails = 2 + ReadArray(Selection, TEXT("links")).Num() + ReadArray(Selection, TEXT("fields")).Num();
        Test.TestTrue(TEXT("Selection displays the summary and every source field"),
            Details.IsValid() && Details->GetChildren()->Num() == ExpectedDetails);
        SelectPage(TEXT("features"));
        const int32* FeaturePage = TabIndices.Find(TEXT("features"));
        Test.TestTrue(TEXT("Coverage matrix uses a separate native panel"),
            FeaturePage && Pages.IsValid() && Pages->GetActiveWidgetIndex() == *FeaturePage);
        SelectPage(TEXT("all"));
        SetSearch(FText::GetEmpty());
        Test.TestEqual(TEXT("Clearing search restores every source row"), Filtered.Num(), Rows.Num());
    }
#endif

private:
    void OnMapOpened(const FString&, bool)
    {
        Reload();
    }

    FReply Refresh()
    {
        Reload();
        return FReply::Handled();
    }

    void Reload()
    {
        List.Reset();
        Details.Reset();
        Pages.Reset();
        Rows.Reset();
        Filtered.Reset();
        Features.Reset();
        FeatureChoices.Reset();
        TabIndices.Reset();
        Query.Reset();
        Feature.Reset();
        bReviewOnly = false;
        Draft = ActiveDraft();
        Model.Reset();
        if (!Draft.IsEmpty())
        {
            const FString File = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("T3MapIntakes/DraftReviews"), Draft + TEXT(".json"));
            const int64 Bytes = FPlatformFileManager::Get().GetPlatformFile().FileSize(*File);
            FString Json;
            if (Bytes > 0 && Bytes <= 64 * 1024 * 1024 && FFileHelper::LoadFileToString(Json, *File))
            {
                FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Model);
            }
            if (ReadString(Model, TEXT("draft")) != Draft)
            {
                Model.Reset();
            }
        }

        TSharedRef<SHorizontalBox> Bar = SNew(SHorizontalBox);
        Bar->AddSlot().FillWidth(1.f).VAlign(VAlign_Center)
        [
            SNew(STextBlock)
            .Text(Model.IsValid() ? Label(ReadString(Model, TEXT("title"))) : LOCTEXT("Title", "TRIBES Object Review"))
        ];
        Bar->AddSlot().AutoWidth().Padding(8.f, 0.f, 0.f, 0.f)
        [
            SNew(SButton).Text(LOCTEXT("Refresh", "Refresh")).OnClicked(this, &SObjectReview::Refresh)
        ];
        TSharedRef<SVerticalBox> Body = SNew(SVerticalBox);
        Body->AddSlot().AutoHeight().Padding(0.f, 0.f, 0.f, 8.f)[Bar];
        ChildSlot[SNew(SBorder).Padding(10.f)[Body]];
        if (!Model.IsValid())
        {
            Body->AddSlot().AutoHeight()
            [
                SNew(STextBlock).AutoWrapText(true).Text(LOCTEXT("Empty", "Open an imported TRIBES draft. The TRIBES Map Editor menu can generate a review for older drafts."))
            ];
            return;
        }
        for (const TSharedPtr<FJsonValue>& Value : ReadArray(Model, TEXT("objects")))
        {
            if (Value->Type == EJson::Object)
            {
                Rows.Add(Value->AsObject());
            }
        }
        for (const TSharedPtr<FJsonValue>& Value : ReadArray(Model, TEXT("features")))
        {
            if (Value->Type == EJson::Object)
            {
                Features.Add(Value->AsObject());
            }
        }
        const TSharedPtr<FJsonObject>* LayoutObject = nullptr;
        FRow Layout;
        if (Model->TryGetObjectField(TEXT("layout"), LayoutObject) && LayoutObject)
        {
            Layout = *LayoutObject;
        }
        TSharedRef<SHorizontalBox> Navigation = SNew(SHorizontalBox);
        Body->AddSlot().AutoHeight().Padding(0.f, 0.f, 0.f, 8.f)[Navigation];
        Body->AddSlot().FillHeight(1.f)[SAssignNew(Pages, SWidgetSwitcher)];
        for (const TSharedPtr<FJsonValue>& Value : ReadArray(Layout, TEXT("tabs")))
        {
            const FRow Tab = Value->AsObject();
            const FString Key = ReadString(Tab, TEXT("id"));
            TabIndices.Add(Key, Pages->GetNumWidgets());
            Navigation->AddSlot().AutoWidth().Padding(0.f, 0.f, 6.f, 0.f)
            [
                SNew(SButton).Text(Label(ReadString(Tab, TEXT("label"))))
                .OnClicked(this, &SObjectReview::SelectPage, Key)
            ];
            Pages->AddSlot()[Key == TEXT("features") ? FeatureMatrix() : ObjectList(Layout)];
        }
        if (List.IsValid())
        {
            ApplyFilters();
        }
    }

    TSharedRef<SWidget> ObjectList(const FRow& Layout)
    {
        TSharedRef<SHeaderRow> Header = SNew(SHeaderRow);
        for (const TSharedPtr<FJsonValue>& Value : ReadArray(Layout, TEXT("columns")))
        {
            const FRow Column = Value->AsObject();
            const FName Key(*ReadString(Column, TEXT("id")));
            double Width = 150;
            Column->TryGetNumberField(TEXT("width"), Width);
            Header->AddColumn(SHeaderRow::Column(Key).DefaultLabel(Label(ReadString(Column, TEXT("label"))))
                .FillWidth(static_cast<float>(Width)));
        }
        TSharedRef<SHorizontalBox> Filters = SNew(SHorizontalBox);
        for (const TSharedPtr<FJsonValue>& Value : ReadArray(Layout, TEXT("filters")))
        {
            const FString Type = Value->AsString();
            if (Type == TEXT("query"))
            {
                Filters->AddSlot().FillWidth(1.f).Padding(0.f, 0.f, 6.f, 0.f)
                [
                    SNew(SSearchBox).HintText(LOCTEXT("Search", "Search source objects")).OnTextChanged(this, &SObjectReview::SetSearch)
                ];
            }
            else if (Type == TEXT("review"))
            {
                Filters->AddSlot().AutoWidth().Padding(0.f, 0.f, 6.f, 0.f)
                [
                    SNew(SCheckBox).OnCheckStateChanged(this, &SObjectReview::SetReviewFilter)
                    [SNew(STextBlock).Text(LOCTEXT("NeedsReview", "Needs review"))]
                ];
            }
            else if (Type == TEXT("feature"))
            {
                FeatureChoices.Add(MakeShared<FString>());
                for (const FRow& Item : Features)
                {
                    FeatureChoices.Add(MakeShared<FString>(ReadString(Item, TEXT("label"))));
                }
                Filters->AddSlot().AutoWidth()
                [
                    SNew(SComboBox<TSharedPtr<FString>>).OptionsSource(&FeatureChoices)
                    .InitiallySelectedItem(FeatureChoices[0])
                    .OnGenerateWidget(this, &SObjectReview::FeatureLabel)
                    .OnSelectionChanged(this, &SObjectReview::SetFeature)
                    [SNew(STextBlock).Text(this, &SObjectReview::FeatureText)]
                ];
            }
        }
        TSharedRef<SSplitter> Split = SNew(SSplitter);
        Split->AddSlot().Value(0.68f)
        [
            SNew(SVerticalBox)
            + SVerticalBox::Slot().AutoHeight().Padding(0.f, 0.f, 0.f, 6.f)[Filters]
            + SVerticalBox::Slot().FillHeight(1.f)
            [
                SAssignNew(List, SListView<FRow>)
                .ListItemsSource(&Filtered)
                .SelectionMode(ESelectionMode::Single)
                .OnGenerateRow(this, &SObjectReview::MakeRow)
                .OnSelectionChanged(this, &SObjectReview::ShowDetails)
                .HeaderRow(Header)
            ]
            + SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f)
            [SNew(STextBlock).Text(this, &SObjectReview::Summary)]
        ];
        Split->AddSlot().Value(0.32f)
        [
            SNew(SBorder).Padding(8.f)
            [SNew(SScrollBox) + SScrollBox::Slot()[SAssignNew(Details, SVerticalBox)]]
        ];
        ShowDetails(nullptr, ESelectInfo::Direct);
        return Split;
    }

    TSharedRef<SWidget> FeatureMatrix()
    {
        TSharedRef<SHeaderRow> Header = SNew(SHeaderRow);
        Header->AddColumn(SHeaderRow::Column(TEXT("label")).DefaultLabel(LOCTEXT("Feature", "Source feature")).FillWidth(0.25f));
        Header->AddColumn(SHeaderRow::Column(TEXT("draft")).DefaultLabel(LOCTEXT("Draft", "Unreal draft")).FillWidth(0.13f));
        Header->AddColumn(SHeaderRow::Column(TEXT("host")).DefaultLabel(LOCTEXT("Host", "Direct Host")).FillWidth(0.13f));
        Header->AddColumn(SHeaderRow::Column(TEXT("detail")).DefaultLabel(LOCTEXT("Coverage", "Coverage")).FillWidth(0.49f));
        return SNew(SListView<FRow>).ListItemsSource(&Features).SelectionMode(ESelectionMode::None)
            .OnGenerateRow(this, &SObjectReview::MakeRow).HeaderRow(Header);
    }

    TSharedRef<ITableRow> MakeRow(FRow Row, const TSharedRef<STableViewBase>& Owner)
    {
        return SNew(SReviewRow, Owner).Entry(Row);
    }

    FReply SelectPage(FString Key)
    {
        if (const int32* Found = TabIndices.Find(Key))
        {
            Pages->SetActiveWidgetIndex(*Found);
        }
        return FReply::Handled();
    }

    void AppendDetail(const FString& Text, const float Top = 5.f)
    {
        Details->AddSlot().AutoHeight().Padding(0.f, Top, 0.f, 0.f)
        [SNew(STextBlock).Text(Label(Text)).AutoWrapText(true)];
    }

    void ShowDetails(FRow Row, ESelectInfo::Type)
    {
        if (!Details.IsValid())
        {
            return;
        }
        Details->ClearChildren();
        if (!Row.IsValid())
        {
            AppendDetail(LOCTEXT("SelectObject", "Select an object to inspect its source fields.").ToString());
            return;
        }
        AppendDetail(ReadString(Row, TEXT("object")), 0.f);
        AppendDetail(ReadString(Row, TEXT("status")) + TEXT(" | ") + ReadString(Row, TEXT("reason")));
        for (const TSharedPtr<FJsonValue>& Value : ReadArray(Row, TEXT("links")))
        {
            const FRow Link = Value->AsObject();
            AppendDetail(ReadString(Link, TEXT("label")) + TEXT(": ") + ReadString(Link, TEXT("value")));
        }
        for (const TSharedPtr<FJsonValue>& Value : ReadArray(Row, TEXT("fields")))
        {
            const FRow Field = Value->AsObject();
            AppendDetail(ReadString(Field, TEXT("label")) + TEXT(": ") + ReadString(Field, TEXT("value"))
                + TEXT(" [") + ReadString(Field, TEXT("status")) + TEXT("]"), 8.f);
        }
    }

    void SetSearch(const FText& Input)
    {
        Query = Input.ToString().TrimStartAndEnd();
        ApplyFilters();
    }

    void SetReviewFilter(ECheckBoxState State)
    {
        bReviewOnly = State == ECheckBoxState::Checked;
        ApplyFilters();
    }

    void SetFeature(TSharedPtr<FString> Choice, ESelectInfo::Type)
    {
        Feature = Choice.IsValid() ? *Choice : FString();
        ApplyFilters();
    }

    TSharedRef<SWidget> FeatureLabel(TSharedPtr<FString> Choice) const
    {
        return SNew(STextBlock).Text(Choice.IsValid() && !Choice->IsEmpty() ? Label(*Choice) : LOCTEXT("All", "All features"));
    }

    FText FeatureText() const
    {
        return Feature.IsEmpty() ? LOCTEXT("AllSelected", "All features") : Label(Feature);
    }

    void ApplyFilters()
    {
        if (!List.IsValid())
        {
            return;
        }
        Filtered.Reset(Rows.Num());
        for (const FRow& Row : Rows)
        {
            if (bReviewOnly && !ReadBool(Row, TEXT("review")))
            {
                continue;
            }
            if (!Feature.IsEmpty() && ReadString(Row, TEXT("feature")) != Feature)
            {
                continue;
            }
            if (!Query.IsEmpty() && !ReadString(Row, TEXT("object")).Contains(Query)
                && !ReadString(Row, TEXT("type")).Contains(Query) && !ReadString(Row, TEXT("reason")).Contains(Query))
            {
                continue;
            }
            Filtered.Add(Row);
        }
        const TArray<FRow> Selected = List->GetSelectedItems();
        List->RequestListRefresh();
        if (Selected.IsEmpty() || !Filtered.Contains(Selected[0]))
        {
            if (Filtered.IsEmpty())
            {
                List->ClearSelection();
                ShowDetails(nullptr, ESelectInfo::Direct);
            }
            else
            {
                List->SetSelection(Filtered[0]);
            }
        }
    }

    FText Summary() const
    {
        return FText::Format(LOCTEXT("Count", "{0} of {1} source objects"), Filtered.Num(), Rows.Num());
    }

    FString Draft;
    FString Query;
    FString Feature;
    bool bReviewOnly = false;
    FRow Model;
    FDelegateHandle MapOpened;
    TMap<FString, int32> TabIndices;
    TArray<FRow> Rows;
    TArray<FRow> Filtered;
    TArray<FRow> Features;
    TArray<TSharedPtr<FString>> FeatureChoices;
    TSharedPtr<SListView<FRow>> List;
    TSharedPtr<SVerticalBox> Details;
    TSharedPtr<SWidgetSwitcher> Pages;
};

class FCommunityMapReviewModule final : public IModuleInterface
{
public:
    void StartupModule() override
    {
        FGlobalTabmanager::Get()->RegisterNomadTabSpawner(ReviewTabId, FOnSpawnTab::CreateRaw(this, &FCommunityMapReviewModule::Spawn))
            .SetDisplayName(LOCTEXT("TabName", "TRIBES Object Review"));
        Open = IConsoleManager::Get().RegisterConsoleCommand(TEXT("T3.MapReview"), TEXT("Open the TRIBES Object Review editor tab."),
            FConsoleCommandDelegate::CreateLambda([] { FGlobalTabmanager::Get()->TryInvokeTab(ReviewTabId); }), ECVF_Default);
    }

    void ShutdownModule() override
    {
        if (Open)
        {
            IConsoleManager::Get().UnregisterConsoleObject(Open);
        }
        FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(ReviewTabId);
    }

private:
    TSharedRef<SDockTab> Spawn(const FSpawnTabArgs&)
    {
        return SNew(SDockTab).TabRole(ETabRole::NomadTab)[SNew(SObjectReview)];
    }

    IConsoleObject* Open = nullptr;
};
}

IMPLEMENT_MODULE(FCommunityMapReviewModule, CommunityMapReview)

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FTribesObjectReviewTest, "TRIBES.MapEditor.DockedObjectReview",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FTribesObjectReviewTest::RunTest(const FString&)
{
    TestTrue(TEXT("The editor command opens object review"), IConsoleManager::Get().ProcessUserConsoleInput(
        TEXT("T3.MapReview"), *GLog, GEditor->GetEditorWorldContext().World()));
    const TSharedPtr<SDockTab> Tab = FGlobalTabmanager::Get()->FindExistingLiveTab(ReviewTabId);
    if (!TestTrue(TEXT("Unreal opened a Nomad dock tab"), Tab.IsValid() && Tab->GetTabRole() == ETabRole::NomadTab))
    {
        return false;
    }
    StaticCastSharedRef<SObjectReview>(Tab->GetContent())->RunChecks(*this);
    Tab->RequestCloseTab();
    return true;
}
#endif

#undef LOCTEXT_NAMESPACE
